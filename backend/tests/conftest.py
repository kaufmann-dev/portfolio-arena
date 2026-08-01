"""Shared fixtures: a real PostgreSQL (direct Podman, or TEST_DATABASE_URL),
the app under TestClient with lifespan (migrations + seed), a local admin
session, and stubbed Massive fetching (tests never hit the network).

Environment must be configured before any `app.*` import, because Settings
is cached at first use.
"""

import os
import subprocess
import time
from uuid import uuid4

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
os.environ.setdefault("ARENA_INTERNAL_MCP_API_KEY", "test-internal-worker-token")
os.environ.setdefault("MASSIVE_API_KEY", "test-massive-api-key")

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

    container_name = f"portfolio-arena-tests-{uuid4().hex}"

    def podman(*args: str, timeout_seconds: float = 15) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["podman", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

    try:
        started = podman(
            "run",
            "--detach",
            "--name",
            container_name,
            "--userns",
            "keep-id:uid=70,gid=70",
            "--env",
            "POSTGRES_USER=test",
            "--env",
            "POSTGRES_PASSWORD=test",
            "--env",
            "POSTGRES_DB=test",
            "--publish",
            "127.0.0.1::5432",
            "docker.io/library/postgres:16-alpine",
            timeout_seconds=120,
        )
        if started.returncode != 0:
            raise RuntimeError(f"Could not start test Postgres with Podman: {started.stderr.strip()}")

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready = podman("exec", container_name, "pg_isready", "--username", "test", "--dbname", "test")
            if ready.returncode == 0:
                break

            state = podman("inspect", "--format", "{{.State.Status}}", container_name)
            if state.returncode != 0 or state.stdout.strip() != "running":
                logs = podman("logs", container_name)
                raise RuntimeError(f"Test Postgres exited before it was ready:\n{logs.stdout}{logs.stderr}")
            time.sleep(0.2)
        else:
            logs = podman("logs", container_name)
            raise RuntimeError(f"Test Postgres did not become ready:\n{logs.stdout}{logs.stderr}")

        published = podman("port", container_name, "5432/tcp")
        if published.returncode != 0:
            raise RuntimeError(f"Could not resolve test Postgres port: {published.stderr.strip()}")
        port = published.stdout.strip().rsplit(":", maxsplit=1)[-1]
        yield f"postgresql+psycopg2://test:test@127.0.0.1:{port}/test"
    finally:
        podman("rm", "--force", "--ignore", "--volumes", container_name)


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
    from app.services import arena, price_cache

    limiter.reset()

    with session_factory()() as session:
        session.execute(
            text(
                "TRUNCATE auth_sessions, settings, model_harness_capabilities, model_definitions, "
                "agents, prompts, portfolios, allocations, "
                "positions, signals, signal_positions, evaluation_runs, evaluator_settings, "
                "portfolio_evaluator_configs, "
                "evaluator_instances, price_cache, api_keys RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
        run_seed(session)
    with price_cache._failure_cache_lock:
        price_cache._failure_cache.clear()
    arena.clear_analysis_caches()
    get_oidc_client.cache_clear()
    client.cookies.clear()
    yield


@pytest.fixture(autouse=True)
def stub_massive(monkeypatch):
    """No test may hit Massive. Symbols resolve from a small static universe and
    price downloads return deterministic synthetic series."""
    from datetime import UTC, datetime, timedelta

    from app.services import massive
    from app.services.trading_calendar import is_trading_day

    universe = {
        "SPY": {
            "symbol": "SPY",
            "name": "SPDR S&P 500 ETF Trust",
            "currency": "USD",
            "exchange": "ARCX",
            "type": "ETF",
            "active": True,
            "market": "stocks",
            "locale": "us",
        },
        "RSP": {
            "symbol": "RSP",
            "name": "Invesco S&P 500 Equal Weight ETF",
            "currency": "USD",
            "exchange": "ARCX",
            "type": "ETF",
            "active": True,
            "market": "stocks",
            "locale": "us",
        },
        "AAPL": {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "currency": "USD",
            "exchange": "XNAS",
            "type": "CS",
            "active": True,
            "market": "stocks",
            "locale": "us",
        },
        "MSFT": {
            "symbol": "MSFT",
            "name": "Microsoft Corporation",
            "currency": "USD",
            "exchange": "XNAS",
            "type": "CS",
            "active": True,
            "market": "stocks",
            "locale": "us",
        },
        "SAP.DE": {
            "symbol": "SAP.DE",
            "name": "SAP SE",
            "currency": "EUR",
            "exchange": "XETR",
            "type": "CS",
            "active": True,
            "market": "stocks",
            "locale": "de",
        },
        "VFIAX": {
            "symbol": "VFIAX",
            "name": "Vanguard 500 Index Fund Admiral Shares",
            "currency": "USD",
            "exchange": "XNAS",
            "type": "MF",
            "active": True,
            "market": "stocks",
            "locale": "us",
        },
        "EURUSD=X": {
            "symbol": "EURUSD=X",
            "name": "EUR/USD",
            "currency": "USD",
            "exchange": None,
            "type": "CURRENCY",
            "active": True,
            "market": "fx",
            "locale": "global",
        },
        "GC=F": {
            "symbol": "GC=F",
            "name": "Gold Futures",
            "currency": "USD",
            "exchange": "XCEC",
            "type": "FUTURE",
            "active": True,
            "market": "futures",
            "locale": "us",
        },
        "BTC-USD": {
            "symbol": "BTC-USD",
            "name": "Bitcoin USD",
            "currency": "USD",
            "exchange": None,
            "type": "CRYPTO",
            "active": True,
            "market": "crypto",
            "locale": "global",
        },
    }
    base_prices = {"SPY": 500.0, "RSP": 180.0, "AAPL": 200.0, "MSFT": 400.0, "EURUSD=X": 1.10}

    def fake_meta(symbol):
        return universe.get(symbol)

    def fake_download(symbols, start, end=None):
        result = massive.PriceDownloadResult()
        end = end or datetime.now(UTC).date()
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

    monkeypatch.setattr(massive, "fetch_ticker_details", fake_meta)
    monkeypatch.setattr(massive, "has_complete_dividend_adjustments", lambda _symbol: True)
    monkeypatch.setattr(massive, "download_prices", fake_download)
    monkeypatch.setattr(
        massive,
        "search_tickers",
        lambda q: [dict(m) for s, m in universe.items() if q.upper() in s],
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
    settings = client.get("/api/settings", headers=admin_headers).json()
    settings["managed_allocation_policy"] = {
        "min_position_weight_pct": 10,
        "max_position_weight_pct": 100,
    }
    updated = client.put("/api/settings", json=settings, headers=admin_headers)
    assert updated.status_code == 200, updated.text
    response = client.post(
        "/api/admin/prompts",
        json={
            "name": "weekly-manager-v1",
            "mode": "both",
            "managed_text": "Manage a portfolio to beat SPY.",
            "rebuilt_text": "Select a fresh portfolio to beat SPY.",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def sample_model(client, admin_headers) -> dict:
    response = client.post(
        "/api/models",
        json={
            "name": "GPT-5.6 Sol",
            "capabilities": [
                {
                    "harness": "codex",
                    "execution_model_id": "gpt-5.6-sol",
                    "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                }
            ],
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def sample_agent(client, admin_headers, sample_model) -> dict:
    response = client.post(
        "/api/agents",
        json={
            "model_id": sample_model["id"],
            "harness": "codex",
            "reasoning_effort": "xhigh",
        },
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
        json={
            "name": "Claude Weekly",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "managed",
            "direction": "long",
        },
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
