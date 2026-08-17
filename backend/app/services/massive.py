"""Massive stock prices, corporate actions, and ticker reference data."""

import logging
import math
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from urllib.parse import quote, urljoin, urlsplit

import httpx

from ..config import (
    MASSIVE_BASE_URL,
    PRICE_FETCH_MAX_WORKERS,
    PRICE_FETCH_TIMEOUT_SECONDS,
    PRICE_FETCH_TOTAL_TIMEOUT_SECONDS,
    get_settings,
)
from .trading_calendar import NY

logger = logging.getLogger(__name__)

AGGREGATE_LIMIT = 50_000
CORPORATE_ACTION_LIMIT = 5_000
MAX_PAGES = 20
TICKER_SEARCH_PROVIDER_LIMIT = 1_000
DIVIDEND_HISTORY_LOOKBACK_DAYS = 5 * 366
SUCCESS_RESPONSE_STATUSES = {"OK", "DELAYED"}

Series = list[dict]  # [{"date": "YYYY-MM-DD", "close": float}, ...]


class MassiveError(RuntimeError):
    """Base class for provider errors safe to expose only by type."""

    transient = False


class MassiveTransportError(MassiveError):
    transient = True


class MassiveServiceError(MassiveError):
    transient = True


class MassiveAuthenticationError(MassiveError):
    pass


class MassiveNotFoundError(MassiveError):
    pass


class MassiveRequestError(MassiveError):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class MassiveMalformedResponse(MassiveError):
    transient = True


class MassivePaginationError(MassiveMalformedResponse):
    pass


class PriceDownloadResult(dict):
    def __init__(self, *args, errors: dict[str, MassiveError] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.errors = dict(errors or {})

    @property
    def transient_failures(self) -> set[str]:
        return {symbol for symbol, error in self.errors.items() if is_transient_error(error)}

    @property
    def permanent_failures(self) -> set[str]:
        return {symbol for symbol, error in self.errors.items() if is_permanent_symbol_error(error)}


@dataclass(frozen=True)
class GroupedSessionDownload:
    prices: dict[str, Series]
    historical_factors: dict[str, float]


def is_valid_series(data) -> bool:
    return isinstance(data, list) and len(data) >= 1


def is_transient_error(exc: Exception) -> bool:
    return isinstance(exc, MassiveError) and exc.transient


def is_permanent_symbol_error(exc: Exception) -> bool:
    return isinstance(exc, MassiveNotFoundError)


def _new_client() -> httpx.Client:
    api_key = get_settings().massive_api_key.get_secret_value()
    return httpx.Client(
        base_url=MASSIVE_BASE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=PRICE_FETCH_TIMEOUT_SECONDS,
    )


def _request_json(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    *,
    deadline: float | None = None,
) -> dict:
    request_timeout: float | None = None
    if deadline is not None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise MassiveTransportError("Massive request deadline exceeded")
        request_timeout = min(float(PRICE_FETCH_TIMEOUT_SECONDS), remaining)

    try:
        if request_timeout is None:
            response = client.get(url, params=params)
        else:
            response = client.get(url, params=params, timeout=request_timeout)
    except httpx.HTTPError as exc:
        raise MassiveTransportError("Massive request failed") from exc

    if response.status_code in (401, 403):
        raise MassiveAuthenticationError("Massive authentication failed")
    if response.status_code == 404:
        raise MassiveNotFoundError("Massive resource not found")
    if response.status_code in (408, 409, 425, 429) or response.status_code >= 500:
        raise MassiveServiceError(f"Massive service returned HTTP {response.status_code}")
    if response.status_code >= 400:
        raise MassiveRequestError(
            f"Massive request returned HTTP {response.status_code}",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise MassiveMalformedResponse("Massive returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MassiveMalformedResponse("Massive returned a non-object response")
    return payload


def _results(payload: dict) -> list[dict]:
    if payload.get("status") not in SUCCESS_RESPONSE_STATUSES or not isinstance(payload.get("results"), list):
        raise MassiveMalformedResponse("Massive returned an invalid result envelope")
    if not all(isinstance(item, dict) for item in payload["results"]):
        raise MassiveMalformedResponse("Massive returned an invalid result item")
    return payload["results"]


def _next_url(raw: object) -> str | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise MassiveMalformedResponse("Massive returned an invalid next_url")
    url = urljoin(f"{MASSIVE_BASE_URL}/", raw)
    expected = urlsplit(MASSIVE_BASE_URL)
    parsed = urlsplit(url)
    if parsed.scheme != expected.scheme or parsed.netloc != expected.netloc:
        raise MassiveMalformedResponse("Massive returned a next_url for an unexpected origin")
    return url


def _paginated_results(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    *,
    max_pages: int = MAX_PAGES,
    deadline: float | None = None,
) -> tuple[list[dict], dict]:
    combined: list[dict] = []
    first_payload: dict | None = None
    next_url: str | None = url
    next_params = params

    for _ in range(max_pages):
        if next_url is None:
            break
        payload = _request_json(client, next_url, next_params, deadline=deadline)
        if first_payload is None:
            first_payload = payload
        combined.extend(_results(payload))
        next_url = _next_url(payload.get("next_url"))
        next_params = None
    else:
        if next_url is not None:
            raise MassivePaginationError(f"Massive response exceeded {max_pages} pages")

    return combined, first_payload or {"status": "OK", "results": []}


def parse_aggregate_bars(payload: dict) -> Series | None:
    """Parse split-adjusted daily bars using their Eastern-time session date."""
    if payload.get("adjusted") is not True:
        raise MassiveMalformedResponse("Massive aggregate response was not split-adjusted")

    points = []
    for bar in _results(payload):
        try:
            close = float(bar["c"])
            timestamp_ms = int(bar["t"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MassiveMalformedResponse("Massive returned a malformed aggregate bar") from exc
        if not math.isfinite(close) or close <= 0:
            raise MassiveMalformedResponse("Massive returned an invalid aggregate close")
        try:
            session_date = datetime.fromtimestamp(timestamp_ms / 1000, NY).date().isoformat()
        except (OSError, OverflowError, ValueError) as exc:
            raise MassiveMalformedResponse("Massive returned an invalid aggregate timestamp") from exc
        points.append({"date": session_date, "close": round(close, 6)})

    deduped = {point["date"]: point for point in points}
    parsed = [deduped[day] for day in sorted(deduped)]
    return parsed or None


def parse_grouped_session(
    payload: dict,
    session_date: date,
    symbols: set[str],
) -> dict[str, Series]:
    """Extract requested adjusted closes from one market-wide daily response."""
    if payload.get("adjusted") is not True:
        raise MassiveMalformedResponse("Massive grouped response was not split-adjusted")

    requested = {symbol.upper() for symbol in symbols}
    prices: dict[str, Series] = {}
    for item in _results(payload):
        symbol = str(item.get("T") or "").strip().upper()
        if symbol not in requested:
            continue
        try:
            close = float(item["c"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Massive returned a malformed grouped bar symbol=%s", symbol)
            continue
        if not math.isfinite(close) or close <= 0:
            logger.warning("Massive returned an invalid grouped close symbol=%s", symbol)
            continue
        prices[symbol] = [{"date": session_date.isoformat(), "close": round(close, 6)}]
    return prices


def _parse_dividend_adjustment(dividend: dict) -> tuple[date, float]:
    try:
        ex_date = date.fromisoformat(dividend["ex_dividend_date"])
        factor = float(dividend["historical_adjustment_factor"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MassiveMalformedResponse("Massive returned a malformed dividend adjustment") from exc
    if not math.isfinite(factor) or factor <= 0:
        raise MassiveMalformedResponse("Massive returned an invalid dividend adjustment")
    return ex_date, factor


def apply_dividend_adjustments(series: Series, dividends: list[dict]) -> Series:
    """Convert split-adjusted closes to a split-and-dividend total-return basis.

    Massive's factor on each dividend is cumulative from that ex-date through
    the latest distribution. A close is multiplied by the first factor whose
    ex-date follows that close; an ex-date close itself is already ex-dividend.
    """
    factors_by_date: dict[date, float] = {}
    for dividend in dividends:
        ex_date, factor = _parse_dividend_adjustment(dividend)
        # The factor is dimensionless and already normalizes the USD-traded
        # security's history, even when the declared payout currency differs.
        # Multiple distributions can share an ex-date. The smallest cumulative
        # factor includes every same-day distribution.
        factors_by_date[ex_date] = min(factor, factors_by_date.get(ex_date, factor))

    factor_dates = sorted(factors_by_date)
    adjusted = []
    for point in series:
        day = date.fromisoformat(point["date"])
        index = bisect_right(factor_dates, day)
        factor = factors_by_date[factor_dates[index]] if index < len(factor_dates) else 1.0
        adjusted.append({"date": point["date"], "close": round(float(point["close"]) * factor, 6)})
    return adjusted


def _fetch_dividends(
    client: httpx.Client,
    symbol: str,
    start: date,
    end: date,
    *,
    deadline: float | None = None,
) -> list[dict]:
    dividends, _ = _paginated_results(
        client,
        "/stocks/v1/dividends",
        {
            "ticker": symbol,
            "ex_dividend_date.gte": start.isoformat(),
            "ex_dividend_date.lte": end.isoformat(),
            "sort": "ex_dividend_date.asc",
            "limit": CORPORATE_ACTION_LIMIT,
        },
        deadline=deadline,
    )
    return dividends


def _session_action_factors(
    dividends: list[dict],
    splits: list[dict],
    symbols: set[str],
    session_date: date,
) -> tuple[dict[str, float], set[str]]:
    requested = {symbol.upper() for symbol in symbols}
    dividend_factors: dict[str, float] = {}
    split_factors: dict[str, float] = {}
    invalid_symbols: set[str] = set()

    for dividend in dividends:
        symbol = str(dividend.get("ticker") or "").strip().upper()
        if symbol not in requested:
            continue
        try:
            ex_date, factor = _parse_dividend_adjustment(dividend)
        except MassiveMalformedResponse:
            invalid_symbols.add(symbol)
            continue
        if ex_date == session_date:
            dividend_factors[symbol] = min(factor, dividend_factors.get(symbol, factor))

    for split in splits:
        symbol = str(split.get("ticker") or "").strip().upper()
        if symbol not in requested:
            continue
        try:
            execution_date = date.fromisoformat(split["execution_date"])
            factor = float(split["historical_adjustment_factor"])
        except (KeyError, TypeError, ValueError):
            invalid_symbols.add(symbol)
            continue
        if not math.isfinite(factor) or factor <= 0:
            invalid_symbols.add(symbol)
            continue
        if execution_date == session_date:
            split_factors[symbol] = split_factors.get(symbol, 1.0) * factor

    factors = {
        symbol: dividend_factors.get(symbol, 1.0) * split_factors.get(symbol, 1.0)
        for symbol in (dividend_factors.keys() | split_factors.keys()) - invalid_symbols
    }
    return factors, invalid_symbols


def download_grouped_session(symbols: list[str], session_date: date) -> GroupedSessionDownload:
    """Fetch one U.S. session for many symbols in three market-wide requests."""
    requested = {symbol.upper() for symbol in symbols}
    if not requested:
        return GroupedSessionDownload(prices={}, historical_factors={})

    deadline = _price_fetch_deadline()
    with _new_client() as client:
        grouped = _request_json(
            client,
            f"/v2/aggs/grouped/locale/us/market/stocks/{session_date.isoformat()}",
            {"adjusted": "true"},
            deadline=deadline,
        )
        dividends, _ = _paginated_results(
            client,
            "/stocks/v1/dividends",
            {
                "ex_dividend_date.gte": session_date.isoformat(),
                "ex_dividend_date.lte": session_date.isoformat(),
                "sort": "ex_dividend_date.asc",
                "limit": CORPORATE_ACTION_LIMIT,
            },
            deadline=deadline,
        )
        splits, _ = _paginated_results(
            client,
            "/stocks/v1/splits",
            {
                "execution_date.gte": session_date.isoformat(),
                "execution_date.lte": session_date.isoformat(),
                "sort": "execution_date.asc",
                "limit": CORPORATE_ACTION_LIMIT,
            },
            deadline=deadline,
        )

    prices = parse_grouped_session(grouped, session_date, requested)
    historical_factors, invalid_symbols = _session_action_factors(
        dividends,
        splits,
        requested,
        session_date,
    )
    for symbol in invalid_symbols:
        prices.pop(symbol, None)
    if invalid_symbols:
        logger.warning(
            "Massive grouped session skipped malformed corporate actions symbols=%s",
            ",".join(sorted(invalid_symbols)),
        )
    return GroupedSessionDownload(prices=prices, historical_factors=historical_factors)


def _fetch_one(
    client: httpx.Client,
    symbol: str,
    start: date,
    end: date,
    *,
    deadline: float | None = None,
) -> Series | None:
    aggregate_path = (
        f"/v2/aggs/ticker/{quote(symbol, safe='')}/range/1/day/{start.isoformat()}/{end.isoformat()}"
    )
    bars, aggregate_payload = _paginated_results(
        client,
        aggregate_path,
        {"adjusted": "true", "sort": "asc", "limit": AGGREGATE_LIMIT},
        deadline=deadline,
    )
    split_adjusted = parse_aggregate_bars({**aggregate_payload, "status": "OK", "results": bars})
    if not split_adjusted:
        return None

    dividends = _fetch_dividends(
        client,
        symbol,
        date.fromisoformat(split_adjusted[0]["date"]),
        end,
        deadline=deadline,
    )
    return apply_dividend_adjustments(split_adjusted, dividends)


def has_complete_dividend_adjustments(symbol: str, *, as_of: date | None = None) -> bool:
    """Whether recent distributions can be represented on a total-return basis."""
    as_of = as_of or datetime.now(UTC).date()
    start = as_of - timedelta(days=DIVIDEND_HISTORY_LOOKBACK_DAYS)
    with _new_client() as client:
        dividends = _fetch_dividends(client, symbol, start, as_of)

    try:
        for dividend in dividends:
            _parse_dividend_adjustment(dividend)
    except MassiveMalformedResponse:
        return False
    return True


def _price_fetch_deadline() -> float:
    return monotonic() + PRICE_FETCH_TOTAL_TIMEOUT_SECONDS


def _download_one(symbol: str, start: date, end: date) -> Series | None:
    deadline = _price_fetch_deadline()
    with _new_client() as client:
        return _fetch_one(client, symbol, start, end, deadline=deadline)


def download_prices(symbols: list[str], start: date, end: date | None = None) -> PriceDownloadResult:
    """Fetch total-return daily series for all symbols in parallel."""
    if not symbols:
        return PriceDownloadResult()

    end = end or datetime.now(UTC).date()
    request_start = start - timedelta(days=7)
    result = PriceDownloadResult({symbol: None for symbol in symbols})
    errors: dict[str, MassiveError] = {}

    with ThreadPoolExecutor(max_workers=min(PRICE_FETCH_MAX_WORKERS, len(symbols))) as executor:
        futures = {executor.submit(_download_one, symbol, request_start, end): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result[symbol] = future.result()
            except MassiveError as exc:
                errors[symbol] = exc
                result[symbol] = None

    if errors:
        failures = [f"{symbol}: {type(error).__name__}" for symbol, error in errors.items()]
        logger.warning("Massive price failures (%d): %s", len(failures), ", ".join(failures[:8]))

    result.errors = errors
    return result


def _normalize_ticker(item: dict) -> dict:
    symbol = str(item.get("ticker") or "").strip().upper()
    return {
        "symbol": symbol,
        "name": item.get("name") or symbol,
        "currency": str(item.get("currency_name") or "").upper() or None,
        "exchange": item.get("primary_exchange"),
        "type": str(item.get("type") or "").upper(),
        "active": item.get("active"),
        "market": item.get("market"),
        "locale": item.get("locale"),
    }


def fetch_ticker_details(symbol: str) -> dict | None:
    try:
        with _new_client() as client:
            payload = _request_json(client, f"/v3/reference/tickers/{quote(symbol, safe='')}")
    except MassiveNotFoundError:
        return None
    except MassiveRequestError as exc:
        # The detail endpoint reports syntactically invalid/missing tickers as
        # 400/422 instead of 404. Other client errors still surface.
        if exc.status_code in (400, 422):
            return None
        raise
    results = payload.get("results")
    if payload.get("status") != "OK" or not isinstance(results, dict):
        raise MassiveMalformedResponse("Massive returned invalid ticker details")
    normalized = _normalize_ticker(results)
    if not normalized["symbol"]:
        raise MassiveMalformedResponse("Massive returned ticker details without a ticker")
    return normalized


def search_tickers(query: str) -> list[dict]:
    with _new_client() as client:
        items, _ = _paginated_results(
            client,
            "/v3/reference/tickers",
            {
                "active": "true",
                "search": query,
                "sort": "ticker",
                "order": "asc",
                "limit": TICKER_SEARCH_PROVIDER_LIMIT,
            },
        )
    return [ticker for item in items if (ticker := _normalize_ticker(item))["symbol"]]
