"""Symbol syntax and position-set validation (pure rules, no network)."""

import pytest

from app.services.symbols import (
    SymbolValidationError,
    check_syntax,
    normalize_symbol,
    validate_positions,
)


class TestSyntax:
    def test_normalize(self):
        assert normalize_symbol("  aapl ") == "AAPL"
        assert normalize_symbol("cash:eur") == "CASH:EUR"

    def test_index_rejected_with_etf_hint(self):
        with pytest.raises(SymbolValidationError, match="ETF"):
            check_syntax("^GSPC")

    def test_fx_pair_rejected_with_usd_security_hint(self):
        with pytest.raises(SymbolValidationError, match="USD-denominated"):
            check_syntax("EURUSD=X")

    def test_futures_rejected_with_etf_hint(self):
        with pytest.raises(SymbolValidationError, match="roll artifacts"):
            check_syntax("ES=F")

    def test_cash_rejected(self):
        with pytest.raises(SymbolValidationError, match="not supported"):
            check_syntax("CASH:EURO")

    def test_plain_symbols_pass(self):
        check_syntax("AAPL")
        check_syntax("BRK-B")


class TestPositionRules:
    def test_valid_set(self):
        validate_positions(
            [
                {"symbol": "AAPL", "weight_pct": 59.5},
                {"symbol": "MSFT", "weight_pct": 40.5},
            ]
        )

    def test_sum_must_be_exactly_100(self):
        with pytest.raises(SymbolValidationError, match="sum to exactly 100"):
            validate_positions([{"symbol": "AAPL", "weight_pct": 99.9}])

    def test_fractional_weights_summing_to_100(self):
        validate_positions(
            [
                {"symbol": "A", "weight_pct": 33.3333},
                {"symbol": "B", "weight_pct": 33.3333},
                {"symbol": "C", "weight_pct": 33.3334},
            ]
        )

    def test_duplicates_rejected(self):
        with pytest.raises(SymbolValidationError, match="Duplicate"):
            validate_positions(
                [
                    {"symbol": "AAPL", "weight_pct": 50.0},
                    {"symbol": "AAPL", "weight_pct": 50.0},
                ]
            )

    def test_negative_weight_rejected(self):
        with pytest.raises(SymbolValidationError, match="long-only"):
            validate_positions(
                [
                    {"symbol": "AAPL", "weight_pct": 150.0},
                    {"symbol": "SH", "weight_pct": -50.0},
                ]
            )

    def test_empty_rejected(self):
        with pytest.raises(SymbolValidationError):
            validate_positions([])
