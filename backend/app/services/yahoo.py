"""Yahoo Finance chart/search fetching.

Fetches daily closes directly from the chart endpoint in parallel with httpx
(market-deck dropped yfinance for being slow; see its
docs/bugs/slow-global-ticker-loading.md). Equities use adjusted closes (total
return); ``=X`` FX symbols use plain closes.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ..config import (
    PRICE_FETCH_MAX_WORKERS,
    PRICE_FETCH_TIMEOUT_SECONDS,
    PRICE_FETCH_TOTAL_TIMEOUT_SECONDS,
    YAHOO_CHART_BASE_URL,
    YAHOO_SEARCH_URL,
)

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0"}

Series = list[dict]  # [{"date": "YYYY-MM-DD", "close": float}, ...]


class PriceDownloadResult(dict):
    def __init__(self, *args, permanent_failures: set[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.permanent_failures = permanent_failures or set()


def is_valid_series(data) -> bool:
    return isinstance(data, list) and len(data) >= 1


def is_fx_symbol(symbol: str) -> bool:
    return symbol.endswith("=X")


def _chart_timezone(meta):
    timezone_name = (meta or {}).get("exchangeTimezoneName") or (meta or {}).get("timezone")
    if not timezone_name:
        return UTC
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return UTC


def _chart_closes(indicators, prefer_adjusted: bool):
    if not isinstance(indicators, dict):
        return []

    if prefer_adjusted:
        adjclose = indicators.get("adjclose") or []
        if adjclose:
            closes = adjclose[0].get("adjclose") if isinstance(adjclose[0], dict) else None
            if closes and any(close is not None for close in closes):
                return closes

    quote_data = indicators.get("quote") or []
    if quote_data:
        closes = quote_data[0].get("close") if isinstance(quote_data[0], dict) else None
        if closes and any(close is not None for close in closes):
            return closes
    return []


def _chart_result(payload) -> dict | None:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict) or chart.get("error"):
        return None
    results = chart.get("result") or []
    if not results or not isinstance(results[0], dict):
        return None
    return results[0]


def parse_chart_payload(payload, prefer_adjusted: bool = True) -> Series | None:
    result = _chart_result(payload)
    if result is None:
        return None

    timestamps = result.get("timestamp") or []
    closes = _chart_closes(result.get("indicators"), prefer_adjusted)
    if not timestamps or not closes:
        return None

    tz = _chart_timezone(result.get("meta"))
    points = []
    for timestamp, close in zip(timestamps, closes, strict=False):
        if close is None:
            continue
        try:
            close_value = float(close)
            timestamp_value = int(timestamp)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(close_value) or close_value <= 0:
            continue
        day = datetime.fromtimestamp(timestamp_value, tz).date().isoformat()
        points.append({"date": day, "close": round(close_value, 6)})
    # The chart endpoint can emit two entries for the latest day (intraday +
    # close); keep the last value per date.
    deduped: dict[str, dict] = {point["date"]: point for point in points}
    points = [deduped[day] for day in sorted(deduped)]
    return points if points else None


def normalize_chart_meta(meta) -> dict:
    if not isinstance(meta, dict):
        return {}
    name = meta.get("longName") or meta.get("shortName") or meta.get("symbol")
    return {
        "symbol": meta.get("symbol"),
        "name": name,
        "currency": meta.get("currency"),
        "exchangeName": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "instrumentType": meta.get("instrumentType"),
    }


def _chart_endpoint(symbol: str) -> str:
    return f"{YAHOO_CHART_BASE_URL}/{quote(symbol, safe='')}"


def _chart_params(start: date) -> dict:
    # A few days of lead so the effective close always has a prior print to
    # carry from, and so FX pairs cover the first equity date.
    period1 = datetime.combine(start - timedelta(days=7), datetime.min.time(), tzinfo=UTC)
    return {
        "period1": int(period1.timestamp()),
        "period2": int(datetime.now(UTC).timestamp()),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }


def _fetch_one(client: httpx.Client, symbol: str, start: date) -> Series | None:
    response = client.get(_chart_endpoint(symbol), params=_chart_params(start))
    response.raise_for_status()
    return parse_chart_payload(response.json(), prefer_adjusted=not is_fx_symbol(symbol))


def _permanent_chart_failure(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404


def fetch_chart_meta(symbol: str) -> dict | None:
    """Resolve a symbol via the chart endpoint (no crumb needed). Returns
    normalized meta or None when the symbol doesn't exist / has no data."""
    params = {"range": "1mo", "interval": "1d", "includeAdjustedClose": "true"}
    try:
        with httpx.Client(headers=_HEADERS, timeout=PRICE_FETCH_TIMEOUT_SECONDS) as client:
            response = client.get(_chart_endpoint(symbol), params=params)
            response.raise_for_status()
            result = _chart_result(response.json())
    except Exception as exc:
        logger.warning("Yahoo meta fetch failed for %s: %s", symbol, type(exc).__name__)
        return None
    if result is None:
        return None
    meta = normalize_chart_meta(result.get("meta"))
    return meta if meta.get("symbol") else None


def _normalize_quote(item) -> dict:
    if not isinstance(item, dict):
        return {}
    symbol = str(item.get("symbol") or "").strip().upper()
    return {
        "symbol": symbol,
        "name": item.get("longname") or item.get("shortname") or item.get("name") or symbol,
        "exchange": item.get("exchDisp") or item.get("exchange"),
        "type": item.get("typeDisp") or item.get("quoteType"),
    }


def search_symbols(query: str) -> list[dict]:
    params = {"q": query, "quotesCount": 8, "newsCount": 0, "enableFuzzyQuery": "false"}
    try:
        with httpx.Client(headers=_HEADERS, timeout=PRICE_FETCH_TIMEOUT_SECONDS) as client:
            response = client.get(YAHOO_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Yahoo search failed for %s: %s", query, type(exc).__name__)
        return []
    quotes = [_normalize_quote(item) for item in payload.get("quotes") or []]
    return [item for item in quotes if item.get("symbol")]


def download_prices(symbols: list[str], start: date) -> PriceDownloadResult:
    """Fetch daily series for all symbols in parallel, from `start` to now."""
    if not symbols:
        return PriceDownloadResult()

    result = PriceDownloadResult({symbol: None for symbol in symbols})
    permanent_failures: set[str] = set()

    executor = ThreadPoolExecutor(max_workers=min(PRICE_FETCH_MAX_WORKERS, len(symbols)))
    with httpx.Client(headers=_HEADERS, timeout=PRICE_FETCH_TIMEOUT_SECONDS) as client:
        futures = {executor.submit(_fetch_one, client, symbol, start): symbol for symbol in symbols}
        try:
            done, not_done = wait(futures, timeout=PRICE_FETCH_TOTAL_TIMEOUT_SECONDS)
            for future in not_done:
                future.cancel()

            failures = []
            for future in done:
                symbol = futures[future]
                try:
                    result[symbol] = future.result()
                except Exception as exc:
                    if _permanent_chart_failure(exc):
                        permanent_failures.add(symbol)
                    detail = type(exc).__name__
                    if isinstance(exc, httpx.HTTPStatusError):
                        detail = f"{detail}:{exc.response.status_code}"
                    failures.append(f"{symbol}: {detail}")
                    result[symbol] = None
            if failures:
                logger.warning("Yahoo chart failures (%d): %s", len(failures), ", ".join(failures[:8]))
            if not_done:
                logger.warning("Yahoo chart timed out for %d symbols", len(not_done))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    result.permanent_failures = permanent_failures
    return result
