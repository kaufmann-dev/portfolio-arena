"""Validation for USD-denominated equities and ETFs."""
from dataclasses import dataclass

from . import yahoo

# Yahoo instrumentType values acceptable as long-only positions.
ALLOWED_INSTRUMENT_TYPES = {"EQUITY", "ETF"}

REJECTION_HINTS = {
    "index": "Raw index symbols are not investable. Use an ETF instead (e.g. SPY, QQQ, IWM).",
    "fx": "FX pairs are not supported. Use a USD-denominated equity or ETF.",
    "futures": (
        "Futures are not supported — Yahoo continuous contracts have roll artifacts. "
        "Use ETF equivalents instead (e.g. SSO for leveraged index, GLD for gold, TLT for duration)."
    ),
    "type": "Only equities and ETFs are supported.",
    "currency": "Only USD-denominated equities and ETFs are supported.",
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
    """Validate + resolve one symbol against Yahoo. Raises SymbolValidationError."""
    symbol = normalize_symbol(raw)
    check_syntax(symbol)

    meta = yahoo.fetch_chart_meta(symbol)
    if meta is None:
        raise SymbolValidationError(f"Symbol {symbol} was not found on Yahoo Finance.")

    instrument_type = (meta.get("instrumentType") or "").upper()
    if instrument_type == "INDEX":
        raise SymbolValidationError(REJECTION_HINTS["index"])
    if instrument_type == "CURRENCY":
        raise SymbolValidationError(REJECTION_HINTS["fx"])
    if instrument_type == "FUTURE":
        raise SymbolValidationError(REJECTION_HINTS["futures"])
    if instrument_type not in ALLOWED_INSTRUMENT_TYPES:
        raise SymbolValidationError(REJECTION_HINTS["type"])
    currency = str(meta.get("currency") or "").upper()
    if currency != "USD":
        raise SymbolValidationError(REJECTION_HINTS["currency"])

    return ResolvedSymbol(
        symbol=symbol,
        security_type=instrument_type.lower(),
        name=meta.get("name") or symbol,
        currency=currency,
        exchange=meta.get("exchangeName"),
    )


SEARCHABLE_TYPES = ALLOWED_INSTRUMENT_TYPES


def search_symbols_allowed(query: str) -> list[dict]:
    """Yahoo symbol search filtered to instrument types the arena accepts."""
    results = yahoo.search_symbols(query)
    return [item for item in results if str(item.get("type") or "").upper() in SEARCHABLE_TYPES]


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
            raise SymbolValidationError(f"Negative weight for {symbol}; long-only means weights >= 0.")
        total += weight

    # Weights are entered with 4 decimals; exactly-100 means to that precision.
    if abs(total - 100.0) > 1e-6:
        raise SymbolValidationError(f"Weights must sum to exactly 100 (got {total:g}).")
