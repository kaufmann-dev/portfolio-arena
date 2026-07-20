"""Shared fixtures: a real PostgreSQL (testcontainers, or TEST_DATABASE_URL),
the app under TestClient with lifespan (migrations + seed), a local admin
session, and stubbed Yahoo fetching (tests never hit the network).

Environment must be configured before any `app.*` import, because Settings
is cached at first use.
"""

import os
from pathlib import Path

os.environ.setdefault("ARENA_PUBLIC_URL", "https://testserver")
os.environ.setdefault("ARENA_OIDC_ISSUER_URL", "https://idp.test/application/o/portfolio-arena")
os.environ.setdefault("ARENA_OIDC_CLIENT_ID", "portfolio-arena-test")
os.environ.setdefault("ARENA_OIDC_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault(
    "ARENA_OIDC_STATE_SECRET",
    "test-state-secret-0123456789abcdef0123456789abcdef",
)
os.environ.setdefault("ARENA_DB_CONNECT_RETRIES", "3")
os.environ.setdefault("ARENA_DB_CONNECT_RETRY_DELAY", "0.2")

# Let testcontainers talk to podman when no docker daemon is configured.
_PODMAN_SOCK = Path(f"/run/user/{os.getuid()}/podman/podman.sock")
if "TEST_DATABASE_URL" not in os.environ and "DOCKER_HOST" not in os.environ and _PODMAN_SOCK.exists():
    os.environ["DOCKER_HOST"] = f"unix://{_PODMAN_SOCK}"
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

PUBLIC_URL = os.environ["ARENA_PUBLIC_URL"]


@pytest.fixture(scope="session")
def database_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def client(database_url):
    os.environ["DATABASE_URL"] = database_url

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import dispose_engine
    from app.main import app

    dispose_engine()
    with TestClient(app, base_url=PUBLIC_URL) as test_client:
        yield test_client
    dispose_engine()


@pytest.fixture(autouse=True)
def clean_db(client):
    """Reset all tables to the freshly-seeded state before each test."""
    from app.db import session_factory
    from app.oidc import get_oidc_client
    from app.ratelimit import limiter
    from app.seed import run_seed
    from app.services import price_cache

    limiter.reset()

    with session_factory()() as session:
        session.execute(
            text(
                "TRUNCATE auth_sessions, settings, agents, prompts, portfolios, allocations, "
                "positions, evaluation_runs, price_cache, api_keys RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
        run_seed(session)
    with price_cache._failure_cache_lock:
        price_cache._failure_cache.clear()
    get_oidc_client.cache_clear()
    client.cookies.clear()
    yield


@pytest.fixture(autouse=True)
def stub_yahoo(monkeypatch):
    """No test may hit Yahoo. Symbols resolve from a small static universe and
    price downloads return deterministic synthetic series."""
    from datetime import UTC, datetime, timedelta

    from app.services import yahoo
    from app.services.trading_calendar import is_trading_day

    universe = {
        "SPY": {
            "symbol": "SPY",
            "name": "SPDR S&P 500 ETF Trust",
            "currency": "USD",
            "exchangeName": "NYSEArca",
            "instrumentType": "ETF",
        },
        "RSP": {
            "symbol": "RSP",
            "name": "Invesco S&P 500 Equal Weight ETF",
            "currency": "USD",
            "exchangeName": "NYSEArca",
            "instrumentType": "ETF",
        },
        "AAPL": {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "currency": "USD",
            "exchangeName": "NasdaqGS",
            "instrumentType": "EQUITY",
        },
        "MSFT": {
            "symbol": "MSFT",
            "name": "Microsoft Corporation",
            "currency": "USD",
            "exchangeName": "NasdaqGS",
            "instrumentType": "EQUITY",
        },
        "SAP.DE": {
            "symbol": "SAP.DE",
            "name": "SAP SE",
            "currency": "EUR",
            "exchangeName": "XETRA",
            "instrumentType": "EQUITY",
        },
        "VFIAX": {
            "symbol": "VFIAX",
            "name": "Vanguard 500 Index Fund Admiral Shares",
            "currency": "USD",
            "exchangeName": "Nasdaq",
            "instrumentType": "MUTUALFUND",
        },
        "EURUSD=X": {
            "symbol": "EURUSD=X",
            "name": "EUR/USD",
            "currency": "USD",
            "exchangeName": "CCY",
            "instrumentType": "CURRENCY",
        },
        "GC=F": {
            "symbol": "GC=F",
            "name": "Gold Futures",
            "currency": "USD",
            "exchangeName": "CMX",
            "instrumentType": "FUTURE",
        },
        "BTC-USD": {
            "symbol": "BTC-USD",
            "name": "Bitcoin USD",
            "currency": "USD",
            "exchangeName": "CCC",
            "instrumentType": "CRYPTOCURRENCY",
        },
    }
    base_prices = {"SPY": 500.0, "RSP": 180.0, "AAPL": 200.0, "MSFT": 400.0, "EURUSD=X": 1.10}

    def fake_meta(symbol):
        return universe.get(symbol)

    def fake_download(symbols, start):
        result = yahoo.PriceDownloadResult()
        end = datetime.now(UTC).date()
        for symbol in symbols:
            base = base_prices.get(symbol)
            if base is None:
                result[symbol] = None
                continue
            points = []
            day = start
            i = 0
            while day <= end:
                if is_trading_day(day):
                    points.append({"date": day.isoformat(), "close": round(base * (1 + 0.001 * i), 6)})
                    i += 1
                day += timedelta(days=1)
            result[symbol] = points
        return result

    monkeypatch.setattr(yahoo, "fetch_chart_meta", fake_meta)
    monkeypatch.setattr(yahoo, "download_prices", fake_download)
    monkeypatch.setattr(
        yahoo,
        "search_symbols",
        lambda q: [
            {"symbol": s, "name": m["name"], "exchange": m["exchangeName"], "type": m["instrumentType"]}
            for s, m in universe.items()
            if q.upper() in s
        ],
    )
    yield


@pytest.fixture
def admin_headers(client) -> dict:
    from app.config import APP_SESSION_COOKIE
    from app.db import session_factory
    from app.security import create_auth_session

    with session_factory()() as session:
        raw_token = create_auth_session(
            session,
            subject="oidc-admin-subject",
            display_name="admin@test.local",
            id_token="test-id-token",
        )
    return {
        "Cookie": f"{APP_SESSION_COOKIE}={raw_token}",
        "Origin": PUBLIC_URL,
    }


@pytest.fixture
def api_key(client, admin_headers) -> str:
    response = client.post("/api/keys", json={"name": "test-key"}, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()["key"]


@pytest.fixture
def mcp_headers(api_key) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


@pytest.fixture
def sample_prompt(client, admin_headers) -> dict:
    response = client.post(
        "/api/prompts",
        json={
            "name": "weekly-manager-v1",
            "text": "Manage a portfolio to beat SPY.",
            "allocation_policy": {
                "min_position_weight_pct": 1,
                "max_position_weight_pct": 100,
            },
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def sample_agent(client, admin_headers) -> dict:
    response = client.post(
        "/api/agents",
        json={"name": "Claude Opus 4.8 (Claude Code)"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def sample_portfolio(client, admin_headers, sample_agent, sample_prompt) -> dict:
    """A portfolio plus its first allocation. Portfolio creation and the first
    allocation are separate calls (the allocation is entered from the Allocations
    tab); the returned dict keeps an ``allocation`` key for dependent tests."""
    created = client.post(
        "/api/portfolios",
        json={"name": "Claude Weekly", "agent_id": sample_agent["id"], "prompt_id": sample_prompt["id"]},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    portfolio = created.json()

    allocation = client.post(
        f"/api/portfolios/{portfolio['id']}/allocations",
        json={
            "positions": [
                {"symbol": "AAPL", "weight_pct": 60},
                {"symbol": "MSFT", "weight_pct": 40},
            ],
            "note": "initial",
        },
        headers=admin_headers,
    )
    assert allocation.status_code == 201, allocation.text
    return {**portfolio, "allocation": allocation.json()}
