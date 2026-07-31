"""Validation for USD-denominated equities and ETFs."""

from dataclasses import dataclass

from . import massive

# Massive reference ticker type codes accepted as whole-book long/short positions.
SECURITY_TYPES = {
    "CS": "equity",
    "ADRC": "equity",
    "ETF": "etf",
}
SECURITY_MARKETS = {"stocks", "otc"}
SEARCH_RESULT_LIMIT = 8

REJECTION_HINTS = {
    "index": "Raw index symbols are not investable. Use an ETF instead (e.g. SPY, QQQ, IWM).",
    "fx": "FX pairs are not supported. Use a USD-denominated equity or ETF.",
    "futures": (
        "Futures are not supported — continuous contracts have roll artifacts. "
        "Use ETF equivalents instead (e.g. SSO for leveraged index, GLD for gold, TLT for duration)."
    ),
    "type": "Only equities and ETFs are supported.",
    "currency": "Only USD-denominated equities and ETFs are supported.",
    "total_return": (
        "Massive does not provide complete dividend adjustment data for this ticker, "
        "so it cannot be valued on a total-return basis."
    ),
}


class SymbolValidationError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class ResolvedSymbol:
    symbol: str
    security_type: str  # equity | etf
    name: str
    currency: str | None = None
    exchange: str | None = None


def normalize_symbol(raw: str) -> str:
    return " ".join(str(raw or "").split()).upper()


def check_syntax(symbol: str) -> None:
    """Reject disallowed symbol families with a hint. Pure, no network."""
    if not symbol:
        raise SymbolValidationError("Symbol is required.")
    if symbol.startswith("^"):
        raise SymbolValidationError(REJECTION_HINTS["index"])
    if symbol.endswith("=X"):
        raise SymbolValidationError(REJECTION_HINTS["fx"])
    if symbol.endswith("=F"):
        raise SymbolValidationError(REJECTION_HINTS["futures"])
    if symbol.startswith("CASH:"):
        raise SymbolValidationError("Cash positions are not supported; use an equity or ETF.")


def resolve_symbol(raw: str) -> ResolvedSymbol:
    """Validate + resolve one symbol against Massive. Raises SymbolValidationError."""
    symbol = normalize_symbol(raw)
    check_syntax(symbol)

    meta = massive.fetch_ticker_details(symbol)
    if meta is None:
        raise SymbolValidationError(f"Symbol {symbol} was not found on Massive.")

    if meta.get("active") is not True:
        raise SymbolValidationError(f"Symbol {symbol} is inactive.")
    if str(meta.get("market") or "").lower() not in SECURITY_MARKETS:
        raise SymbolValidationError(REJECTION_HINTS["type"])
    instrument_type = str(meta.get("type") or "").upper()
    security_type = SECURITY_TYPES.get(instrument_type)
    if security_type is None:
        raise SymbolValidationError(REJECTION_HINTS["type"])
    currency = str(meta.get("currency") or "").upper()
    if currency != "USD":
        raise SymbolValidationError(REJECTION_HINTS["currency"])
    if not massive.has_complete_dividend_adjustments(symbol):
        raise SymbolValidationError(f"Symbol {symbol} is unsupported. {REJECTION_HINTS['total_return']}")

    return ResolvedSymbol(
        symbol=symbol,
        security_type=security_type,
        name=meta.get("name") or symbol,
        currency=currency,
        exchange=meta.get("exchange"),
    )


def search_symbols_allowed(query: str) -> list[dict]:
    """Massive ticker search filtered to the arena's investable universe."""
    results = massive.search_tickers(query)
    return [
        item
        for item in results
        if SECURITY_TYPES.get(str(item.get("type") or "").upper())
        and item.get("active") is True
        and str(item.get("market") or "").lower() in SECURITY_MARKETS
        and str(item.get("currency") or "").upper() == "USD"
    ][:SEARCH_RESULT_LIMIT]


def validate_positions(positions: list[dict]) -> None:
    """Submit-time position-set rules: no duplicates, all >= 0, sum exactly 100.

    `positions` is a list of {symbol (normalized), weight_pct}. Pure, no network.
    """
    if not positions:
        raise SymbolValidationError("At least one position is required.")

    seen: set[str] = set()
    total = 0.0
    for position in positions:
        symbol = position["symbol"]
        weight = float(position["weight_pct"])
        if symbol in seen:
            raise SymbolValidationError(f"Duplicate symbol {symbol}.")
        seen.add(symbol)
        if weight < 0:
            raise SymbolValidationError(
                f"Negative weight for {symbol}; portfolio direction is set separately."
            )
        total += weight

    # Weights are entered with 4 decimals; exactly-100 means to that precision.
    if abs(total - 100.0) > 1e-6:
        raise SymbolValidationError(f"Weights must sum to exactly 100 (got {total:g}).")
