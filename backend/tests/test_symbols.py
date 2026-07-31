"""Symbol syntax and position-set validation (pure rules, no network)."""

import pytest

from app.services.symbols import (
    SymbolValidationError,
    check_syntax,
    normalize_symbol,
    resolve_symbol,
    search_symbols_allowed,
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
        check_syntax("BRK.B")


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
        with pytest.raises(SymbolValidationError, match="direction is set separately"):
            validate_positions(
                [
                    {"symbol": "AAPL", "weight_pct": 150.0},
                    {"symbol": "SH", "weight_pct": -50.0},
                ]
            )

    def test_empty_rejected(self):
        with pytest.raises(SymbolValidationError):
            validate_positions([])


class TestMassiveResolution:
    @pytest.mark.parametrize(
        ("symbol", "market", "instrument_type", "security_type"),
        [
            ("AAPL", "stocks", "CS", "equity"),
            ("TCEHY", "otc", "ADRC", "equity"),
            ("SPY", "stocks", "ETF", "etf"),
        ],
    )
    def test_accepts_supported_stock_and_otc_security_types(
        self, monkeypatch, symbol, market, instrument_type, security_type
    ):
        from app.services import massive

        monkeypatch.setattr(
            massive,
            "fetch_ticker_details",
            lambda _symbol: {
                "symbol": symbol,
                "name": symbol,
                "currency": "USD",
                "exchange": "XNYS",
                "type": instrument_type,
                "active": True,
                "market": market,
            },
        )

        assert resolve_symbol(symbol).security_type == security_type

    def test_rejects_non_usd_and_unsupported_security_types(self):
        with pytest.raises(SymbolValidationError, match="USD-denominated"):
            resolve_symbol("SAP.DE")
        with pytest.raises(SymbolValidationError, match="Only equities and ETFs"):
            resolve_symbol("VFIAX")

    def test_rejects_supported_type_from_unsupported_market(self, monkeypatch):
        from app.services import massive

        monkeypatch.setattr(
            massive,
            "fetch_ticker_details",
            lambda _symbol: {
                "symbol": "NOTSTOCK",
                "name": "Not a Stock",
                "currency": "USD",
                "exchange": None,
                "type": "CS",
                "active": True,
                "market": "crypto",
            },
        )
        with pytest.raises(SymbolValidationError, match="Only equities and ETFs"):
            resolve_symbol("NOTSTOCK")

    def test_rejects_inactive_ticker(self, monkeypatch):
        from app.services import massive

        monkeypatch.setattr(
            massive,
            "fetch_ticker_details",
            lambda _symbol: {
                "symbol": "OLD",
                "name": "Inactive Corp.",
                "currency": "USD",
                "exchange": "XNYS",
                "type": "CS",
                "active": False,
                "market": "stocks",
            },
        )
        with pytest.raises(SymbolValidationError, match="inactive"):
            resolve_symbol("OLD")

    def test_rejects_ticker_without_complete_dividend_adjustments(self, monkeypatch):
        from app.services import massive

        monkeypatch.setattr(
            massive,
            "fetch_ticker_details",
            lambda _symbol: {
                "symbol": "BMO",
                "name": "Bank of Montreal",
                "currency": "USD",
                "exchange": "XNYS",
                "type": "CS",
                "active": True,
                "market": "stocks",
            },
        )
        monkeypatch.setattr(massive, "has_complete_dividend_adjustments", lambda _symbol: False)

        with pytest.raises(SymbolValidationError, match="total-return basis"):
            resolve_symbol("BMO")

    def test_missing_symbol_rejected(self):
        with pytest.raises(SymbolValidationError, match="not found on Massive"):
            resolve_symbol("NOPE")

    def test_massive_class_share_symbol_is_canonical_without_yahoo_alias(self, monkeypatch):
        from app.services import massive

        requested = []

        def details(symbol):
            requested.append(symbol)
            if symbol != "BRK.B":
                return None
            return {
                "symbol": "BRK.B",
                "name": "Berkshire Hathaway Class B",
                "currency": "USD",
                "exchange": "XNYS",
                "type": "CS",
                "active": True,
                "market": "stocks",
            }

        monkeypatch.setattr(massive, "fetch_ticker_details", details)

        assert resolve_symbol("BRK.B").symbol == "BRK.B"
        with pytest.raises(SymbolValidationError, match="BRK-B was not found"):
            resolve_symbol("BRK-B")
        assert requested == ["BRK.B", "BRK-B"]

    def test_search_filters_unaccepted_results_then_limits_to_eight(self, monkeypatch):
        from app.services import massive

        accepted = [
            {
                "symbol": f"OK{index}",
                "name": f"Accepted {index}",
                "currency": "USD",
                "exchange": "XNYS",
                "type": ("CS", "ADRC", "ETF")[index % 3],
                "active": True,
                "market": "otc" if index == 1 else "stocks",
            }
            for index in range(10)
        ]
        rejected = [
            {**accepted[0], "symbol": "INACTIVE", "active": False},
            {**accepted[0], "symbol": "NONUSD", "currency": "EUR"},
            {**accepted[0], "symbol": "FUND", "type": "MF"},
            {**accepted[0], "symbol": "CRYPTO", "market": "crypto"},
        ]
        monkeypatch.setattr(massive, "search_tickers", lambda _query: rejected + accepted)

        results = search_symbols_allowed("accepted")

        assert [item["symbol"] for item in results] == [f"OK{index}" for index in range(8)]
