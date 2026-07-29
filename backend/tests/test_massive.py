"""Massive REST parsing, authentication, pagination, and total-return adjustments."""

from datetime import UTC, date, datetime

import httpx
import pytest

from app.services import massive

REAL_FETCH_TICKER_DETAILS = massive.fetch_ticker_details
REAL_HAS_COMPLETE_DIVIDEND_ADJUSTMENTS = massive.has_complete_dividend_adjustments
REAL_DOWNLOAD_PRICES = massive.download_prices
REAL_SEARCH_TICKERS = massive.search_tickers


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url=massive.MASSIVE_BASE_URL,
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-key"},
    )


def test_parse_daily_bars_uses_eastern_session_date_and_deduplicates():
    payload = {
        "status": "OK",
        "adjusted": True,
        "results": [
            {"t": int(datetime(2026, 7, 6, 4, 0, tzinfo=UTC).timestamp() * 1000), "c": 101.25},
            {"t": int(datetime(2026, 7, 6, 21, 0, tzinfo=UTC).timestamp() * 1000), "c": 102},
        ],
    }

    assert massive.parse_aggregate_bars(payload) == [{"date": "2026-07-06", "close": 102.0}]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "ERROR", "adjusted": True, "results": []},
        {"status": "OK", "adjusted": False, "results": []},
        {"status": "OK", "adjusted": True, "results": [{"t": "bad", "c": 10}]},
        {"status": "OK", "adjusted": True, "results": [{"t": 1, "c": -1}]},
    ],
)
def test_parse_daily_bars_rejects_malformed_responses(payload):
    with pytest.raises(massive.MassiveMalformedResponse):
        massive.parse_aggregate_bars(payload)


def test_request_authenticates_with_bearer_header(monkeypatch):
    def handler(request: httpx.Request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"status": "OK", "results": []})

    monkeypatch.setattr(massive, "_new_client", lambda: _client(handler))
    assert REAL_SEARCH_TICKERS("apple") == []


@pytest.mark.parametrize(
    ("status_code", "error_type", "transient"),
    [
        (401, massive.MassiveAuthenticationError, False),
        (403, massive.MassiveAuthenticationError, False),
        (404, massive.MassiveNotFoundError, False),
        (422, massive.MassiveRequestError, False),
        (429, massive.MassiveServiceError, True),
        (503, massive.MassiveServiceError, True),
    ],
)
def test_http_error_classification(status_code, error_type, transient):
    with _client(lambda _request: httpx.Response(status_code)) as client:
        with pytest.raises(error_type) as raised:
            massive._request_json(client, "/test")
    assert massive.is_transient_error(raised.value) is transient


def test_transport_error_is_transient():
    def handler(request: httpx.Request):
        raise httpx.ConnectError("provider unavailable", request=request)

    with _client(handler) as client:
        with pytest.raises(massive.MassiveTransportError) as raised:
            massive._request_json(client, "/test")
    assert massive.is_transient_error(raised.value) is True


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}),
        httpx.Response(200, json=[]),
    ],
)
def test_request_rejects_invalid_json_and_non_object_responses(response):
    with _client(lambda _request: response) as client:
        with pytest.raises(massive.MassiveMalformedResponse) as raised:
            massive._request_json(client, "/test")
    assert massive.is_transient_error(raised.value) is True


def test_request_uses_remaining_end_to_end_deadline(monkeypatch):
    requested_timeouts = []

    class Client:
        def get(self, _url, *, params=None, timeout=None):
            requested_timeouts.append(timeout)
            return httpx.Response(200, json={"status": "OK", "results": []})

    monkeypatch.setattr(massive, "monotonic", lambda: 100.0)

    assert massive._request_json(Client(), "/test", deadline=106.0)["status"] == "OK"
    assert requested_timeouts == [6.0]
    with pytest.raises(massive.MassiveTransportError, match="deadline"):
        massive._request_json(Client(), "/test", deadline=100.0)


def test_pagination_combines_successful_pages():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": [{"page": 1}],
                    "next_url": f"{massive.MASSIVE_BASE_URL}/test?cursor=next",
                },
            )
        return httpx.Response(200, json={"status": "OK", "results": [{"page": 2}]})

    with _client(handler) as client:
        results, first_payload = massive._paginated_results(client, "/test", {"seed": "first"})

    assert results == [{"page": 1}, {"page": 2}]
    assert first_payload["results"] == [{"page": 1}]
    assert dict(requests[0].url.params) == {"seed": "first"}
    assert dict(requests[1].url.params) == {"cursor": "next"}


def test_pagination_follows_next_url_and_enforces_page_limit():
    calls = 0

    def handler(_request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [{"page": calls}],
                "next_url": f"{massive.MASSIVE_BASE_URL}/test?cursor={calls}",
            },
        )

    with _client(handler) as client:
        with pytest.raises(massive.MassivePaginationError, match="2 pages"):
            massive._paginated_results(client, "/test", max_pages=2)
    assert calls == 2


def test_pagination_rejects_foreign_next_url():
    with _client(
        lambda _request: httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [],
                "next_url": "https://attacker.example/steal",
            },
        )
    ) as client:
        with pytest.raises(massive.MassiveMalformedResponse, match="unexpected origin"):
            massive._paginated_results(client, "/test")


def test_cumulative_dividend_factors_apply_across_multiple_distributions():
    series = [
        {"date": "2026-01-02", "close": 100},
        {"date": "2026-02-02", "close": 100},
        {"date": "2026-03-02", "close": 100},
    ]
    dividends = [
        {
            "ex_dividend_date": "2026-02-02",
            "historical_adjustment_factor": 0.9,
            "currency": "USD",
        },
        {
            "ex_dividend_date": "2026-03-02",
            "historical_adjustment_factor": 0.95,
            "currency": "USD",
        },
    ]

    assert massive.apply_dividend_adjustments(series, dividends) == [
        {"date": "2026-01-02", "close": 90.0},
        {"date": "2026-02-02", "close": 95.0},
        {"date": "2026-03-02", "close": 100.0},
    ]


def test_dividend_factor_is_valid_independently_of_payout_currency():
    series = [
        {"date": "2026-01-29", "close": 100},
        {"date": "2026-01-30", "close": 99},
    ]
    dividends = [
        {
            "ex_dividend_date": "2026-01-30",
            "historical_adjustment_factor": 0.99,
            "currency": "CAD",
        }
    ]

    assert massive.apply_dividend_adjustments(series, dividends) == [
        {"date": "2026-01-29", "close": 99.0},
        {"date": "2026-01-30", "close": 99.0},
    ]


def test_dividend_without_historical_adjustment_factor_is_malformed():
    with pytest.raises(massive.MassiveMalformedResponse, match="dividend adjustment"):
        massive.apply_dividend_adjustments(
            [{"date": "2026-01-29", "close": 100}],
            [{"ex_dividend_date": "2026-01-30", "cash_amount": 1.67, "currency": "CAD"}],
        )


@pytest.mark.parametrize(
    ("dividend", "expected"),
    [
        (
            {
                "ex_dividend_date": "2026-06-01",
                "historical_adjustment_factor": 0.98,
                "currency": "CAD",
            },
            True,
        ),
        (
            {
                "ex_dividend_date": "2026-06-01",
                "cash_amount": 1.67,
                "currency": "CAD",
            },
            False,
        ),
    ],
)
def test_recent_dividend_history_requires_complete_adjustment_records(monkeypatch, dividend, expected):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"status": "OK", "results": [dividend]})

    monkeypatch.setattr(massive, "_new_client", lambda: _client(handler))

    assert REAL_HAS_COMPLETE_DIVIDEND_ADJUSTMENTS("BMO", as_of=date(2026, 7, 29)) is expected
    assert requests[0].url.path == "/stocks/v1/dividends"
    assert requests[0].url.params["ticker"] == "BMO"
    assert requests[0].url.params["ex_dividend_date.lte"] == "2026-07-29"
    assert requests[0].url.params["limit"] == "5000"


def test_split_adjusted_bars_plus_dividend_preserve_total_returns():
    # The pre-split closes are already on the post-split $50 basis because the
    # aggregate request uses adjusted=true. The dividend factor then adjusts
    # closes before its ex-date without reintroducing the 2-for-1 split jump.
    split_adjusted = [
        {"date": "2026-01-02", "close": 50},
        {"date": "2026-01-05", "close": 50},
        {"date": "2026-01-06", "close": 50},
        {"date": "2026-01-07", "close": 49.5},
    ]
    dividends = [
        {
            "ex_dividend_date": "2026-01-07",
            "historical_adjustment_factor": 0.98,
            "currency": "USD",
        }
    ]

    adjusted = massive.apply_dividend_adjustments(split_adjusted, dividends)
    assert adjusted[:3] == [
        {"date": "2026-01-02", "close": 49.0},
        {"date": "2026-01-05", "close": 49.0},
        {"date": "2026-01-06", "close": 49.0},
    ]
    assert adjusted[3] == {"date": "2026-01-07", "close": 49.5}


def test_fetch_rejects_aggregate_response_not_marked_adjusted():
    def handler(request: httpx.Request):
        if request.url.path.startswith("/v2/aggs/"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "adjusted": False,
                    "results": [
                        {
                            "t": int(datetime(2026, 7, 6, 4, 0, tzinfo=UTC).timestamp() * 1000),
                            "c": 101.25,
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    with _client(handler) as client:
        with pytest.raises(massive.MassiveMalformedResponse, match="not split-adjusted"):
            massive._fetch_one(
                client,
                "AAPL",
                datetime(2026, 7, 1, tzinfo=UTC).date(),
                datetime(2026, 7, 7, tzinfo=UTC).date(),
            )


def test_ticker_details_and_search_normalize_massive_reference_fields(monkeypatch):
    def handler(request: httpx.Request):
        if request.url.path.endswith("/AAPL"):
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": {
                        "ticker": "AAPL",
                        "name": "Apple Inc.",
                        "currency_name": "usd",
                        "primary_exchange": "XNAS",
                        "type": "CS",
                        "active": True,
                        "market": "stocks",
                        "locale": "us",
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [
                    {
                        "ticker": "SPY",
                        "name": "SPDR S&P 500 ETF Trust",
                        "currency_name": "usd",
                        "primary_exchange": "ARCX",
                        "type": "ETF",
                        "active": True,
                        "market": "stocks",
                        "locale": "us",
                    }
                ],
            },
        )

    monkeypatch.setattr(massive, "_new_client", lambda: _client(handler))
    assert REAL_FETCH_TICKER_DETAILS("AAPL") == {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "currency": "USD",
        "exchange": "XNAS",
        "type": "CS",
        "active": True,
        "market": "stocks",
        "locale": "us",
    }
    assert REAL_SEARCH_TICKERS("SPY")[0]["type"] == "ETF"


def test_search_requests_and_paginates_full_provider_pages_for_application_filtering(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.params.get("cursor") == "next":
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": [
                        {
                            "ticker": "FUNDEXTRA",
                            "name": "Fund Extra",
                            "currency_name": "usd",
                            "primary_exchange": "ARCX",
                            "type": "ETF",
                            "active": True,
                            "market": "stocks",
                            "locale": "us",
                        }
                    ],
                },
            )
        assert request.url.params["search"] == "fund"
        assert request.url.params["active"] == "true"
        assert request.url.params["limit"] == "1000"
        return httpx.Response(
            200,
            json={
                "status": "OK",
                "results": [
                    {
                        "ticker": f"FUND{index}",
                        "name": f"Fund {index}",
                        "currency_name": "usd",
                        "primary_exchange": "ARCX",
                        "type": "ETF",
                        "active": True,
                        "market": "stocks",
                        "locale": "us",
                    }
                    for index in range(10)
                ],
                "next_url": f"{massive.MASSIVE_BASE_URL}/v3/reference/tickers?cursor=next",
            },
        )

    monkeypatch.setattr(massive, "_new_client", lambda: _client(handler))
    assert len(REAL_SEARCH_TICKERS("fund")) == 11
    assert len(requests) == 2
    assert dict(requests[1].url.params) == {"cursor": "next"}


def test_download_prices_retains_per_symbol_massive_error_classification(monkeypatch):
    series = [{"date": "2026-07-28", "close": 100.0}]

    def download_one(symbol, _start, _end):
        if symbol == "TEMP":
            raise massive.MassiveServiceError("try again")
        if symbol == "MISSING":
            raise massive.MassiveNotFoundError("gone")
        if symbol == "AUTH":
            raise massive.MassiveAuthenticationError("bad key")
        return series

    monkeypatch.setattr(massive, "_download_one", download_one)

    result = REAL_DOWNLOAD_PRICES(
        ["SPY", "TEMP", "MISSING", "AUTH"],
        date(2026, 7, 1),
        date(2026, 7, 29),
    )

    assert result["SPY"] == series
    assert isinstance(result.errors["TEMP"], massive.MassiveServiceError)
    assert isinstance(result.errors["MISSING"], massive.MassiveNotFoundError)
    assert isinstance(result.errors["AUTH"], massive.MassiveAuthenticationError)
    assert result.transient_failures == {"TEMP"}
    assert result.permanent_failures == {"MISSING"}


def test_download_prices_uses_one_client_per_symbol_and_closes_after_fetch(monkeypatch):
    clients = []
    observed = []

    class Client:
        closed = False

        def __enter__(self):
            clients.append(self)
            return self

        def __exit__(self, _error_type, _error, _traceback):
            self.closed = True

    def fetch_one(client, symbol, _start, _end, *, deadline):
        observed.append((client, symbol, deadline))
        return [{"date": "2026-07-28", "close": 100.0}]

    monkeypatch.setattr(massive, "_new_client", Client)
    monkeypatch.setattr(massive, "_fetch_one", fetch_one)

    result = REAL_DOWNLOAD_PRICES(
        ["SPY", "RSP"],
        date(2026, 7, 1),
        date(2026, 7, 29),
    )

    assert set(result) == {"SPY", "RSP"}
    assert len(clients) == 2
    assert len({id(client) for client, _symbol, _deadline in observed}) == 2
    assert all(deadline > 0 for _client, _symbol, deadline in observed)
    assert all(client.closed for client in clients)


def test_ticker_details_missing_ticker_is_malformed(monkeypatch):
    monkeypatch.setattr(
        massive,
        "_new_client",
        lambda: _client(
            lambda _request: httpx.Response(
                200,
                json={
                    "status": "OK",
                    "results": {
                        "name": "Missing Symbol Inc.",
                        "currency_name": "usd",
                        "type": "CS",
                        "active": True,
                        "market": "stocks",
                    },
                },
            )
        ),
    )

    with pytest.raises(massive.MassiveMalformedResponse, match="ticker details"):
        REAL_FETCH_TICKER_DETAILS("MISSING")


def test_missing_ticker_returns_none(monkeypatch):
    monkeypatch.setattr(
        massive,
        "_new_client",
        lambda: _client(lambda _request: httpx.Response(404)),
    )
    assert REAL_FETCH_TICKER_DETAILS("NOPE") is None
