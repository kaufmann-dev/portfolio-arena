"""Symbol syntax rules: instrument derivation and validation with hints.

Instrument type is derived from Yahoo symbol syntax — equities/ETFs are plain
symbols, cash is ``CASH:CCY``. Raw indices, FX pairs, and futures are rejected
with actionable hints (see PLAN: futures roll artifacts would corrupt the
long-horizon measurement; ETFs cover the use cases).
"""

import re
from dataclasses import dataclass

from . import yahoo

CASH_RE = re.compile(r"^CASH:([A-Z]{3})$")

# Yahoo instrumentType values acceptable as long-only positions.
ALLOWED_INSTRUMENT_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}

REJECTION_HINTS = {
    "index": "Raw index symbols are not investable. Use an ETF instead (e.g. SPY, QQQ, IWM).",
    "fx": "FX pairs are not positions. Use multi-currency cash instead (e.g. CASH:EUR).",
    "futures": (
        "Futures are not supported — Yahoo continuous contracts have roll artifacts. "
        "Use ETF equivalents instead (e.g. SSO for leveraged index, GLD for gold, TLT for duration)."
    ),
    "type": "Only equities and ETFs are supported (plus CASH:CCY for cash).",
}


class SymbolValidationError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class ResolvedSymbol:
    symbol: str
    instrument: str  # equity | cash
    name: str
    currency: str | None = None
    exchange: str | None = None


def normalize_symbol(raw: str) -> str:
    return " ".join(str(raw or "").split()).upper()


def cash_currency(symbol: str) -> str | None:
    match = CASH_RE.match(symbol)
    return match.group(1) if match else None


def fx_pair_for(currency: str) -> str:
    """Yahoo ticker for CCY→USD spot, e.g. EURUSD=X."""
    return f"{currency}USD=X"


def derive_instrument(symbol: str) -> str:
    return "cash" if cash_currency(symbol) else "equity"


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
    if symbol.startswith("CASH:") and not cash_currency(symbol):
        raise SymbolValidationError("Cash symbols look like CASH:CCY, e.g. CASH:USD or CASH:EUR.")


def resolve_symbol(raw: str) -> ResolvedSymbol:
    """Validate + resolve one symbol against Yahoo. Raises SymbolValidationError."""
    symbol = normalize_symbol(raw)
    check_syntax(symbol)

    currency = cash_currency(symbol)
    if currency:
        if currency == "USD":
            return ResolvedSymbol(symbol=symbol, instrument="cash", name="US Dollar cash", currency="USD")
        meta = yahoo.fetch_chart_meta(fx_pair_for(currency))
        if meta is None:
            raise SymbolValidationError(
                f"No Yahoo FX rate for {currency} (looked up {fx_pair_for(currency)})."
            )
        return ResolvedSymbol(symbol=symbol, instrument="cash", name=f"{currency} cash", currency=currency)

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
    if instrument_type and instrument_type not in ALLOWED_INSTRUMENT_TYPES:
        raise SymbolValidationError(REJECTION_HINTS["type"])

    return ResolvedSymbol(
        symbol=symbol,
        instrument="equity",
        name=meta.get("name") or symbol,
        currency=meta.get("currency"),
        exchange=meta.get("exchangeName"),
    )


SEARCHABLE_TYPES = {"EQUITY", "ETF", "MUTUALFUND", "FUND"}


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
