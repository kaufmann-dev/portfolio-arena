"""Shared fixtures: a real PostgreSQL (testcontainers, or TEST_DATABASE_URL),
the app under TestClient with lifespan (migrations + seed), an admin token,
and stubbed Yahoo fetching (tests never hit the network).

Environment must be configured before any `app.*` import, because Settings
is cached at first use.
"""

import os
from pathlib import Path

os.environ.setdefault("ARENA_JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")
os.environ.setdefault("ARENA_ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ARENA_ADMIN_PASSWORD", "admin-password")
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

ADMIN_EMAIL = os.environ["ARENA_ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["ARENA_ADMIN_PASSWORD"]


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
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()


@pytest.fixture(autouse=True)
def clean_db(client):
    """Reset all tables to the freshly-seeded state before each test."""
    from app.db import session_factory
    from app.ratelimit import limiter
    from app.seed import run_seed
    from app.services import price_cache

    limiter.reset()

    with session_factory()() as session:
        session.execute(
            text(
                "TRUNCATE users, settings, agents, prompts, portfolios, allocations, "
                "positions, price_cache RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
        run_seed(session)
    with price_cache._failure_cache_lock:
        price_cache._failure_cache.clear()
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
def admin_token(client) -> str:
    response = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def sample_prompt(client, admin_headers) -> dict:
    response = client.post(
        "/api/prompts",
        json={"name": "weekly-manager-v1", "text": "Manage a portfolio to beat SPY."},
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
    response = client.post(
        "/api/portfolios",
        json={
            "name": "Claude Weekly",
            "agent_id": sample_agent["id"],
            "allocation": {
                "prompt_id": sample_prompt["id"],
                "positions": [
                    {"symbol": "AAPL", "weight_pct": 60},
                    {"symbol": "CASH:USD", "weight_pct": 40},
                ],
                "note": "initial",
            },
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
