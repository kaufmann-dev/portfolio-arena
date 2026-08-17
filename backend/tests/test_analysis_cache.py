"""Deterministic analytics cache behavior and invalidation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from app.services.analysis_cache import SingleFlightLru, fingerprint


def test_single_flight_shares_one_concurrent_builder():
    cache: SingleFlightLru[str, int] = SingleFlightLru(max_entries=2)
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def build() -> int:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=2)
        return 42

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_compute, "same", build)
        assert started.wait(timeout=2)
        second = executor.submit(cache.get_or_compute, "same", build)
        release.set()
        assert first.result(timeout=2) == second.result(timeout=2) == 42

    assert calls == 1
    assert cache.stats().hits == 1
    assert cache.stats().misses == 1


def test_lru_evicts_oldest_entry():
    cache: SingleFlightLru[str, str] = SingleFlightLru(max_entries=2)
    assert cache.get_or_compute("a", lambda: "A") == "A"
    assert cache.get_or_compute("b", lambda: "B") == "B"
    assert cache.get_or_compute("a", lambda: "wrong") == "A"
    assert cache.get_or_compute("c", lambda: "C") == "C"
    assert cache.get_or_compute("b", lambda: "new B") == "new B"

    stats = cache.stats()
    assert stats.evictions == 2
    assert stats.size == 2


def test_failed_builder_is_not_cached():
    cache: SingleFlightLru[str, int] = SingleFlightLru(max_entries=1)
    calls = 0

    def fail() -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        cache.get_or_compute("key", fail)

    assert cache.get_or_compute("key", lambda: 7) == 7
    assert calls == 1
    assert cache.stats().misses == 2


def test_fingerprint_is_stable_across_mapping_order():
    assert fingerprint({"a": 1, "b": [2, 3]}) == fingerprint({"b": [2, 3], "a": 1})


def _create_rebuilt(client, admin_headers, sample_agent, sample_prompt) -> dict:
    response = client.post(
        "/api/portfolios",
        json={
            "name": "Cached Rebuilt",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "rebuilt",
            "direction": "long",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_rebuilt_cache_reuses_analysis_and_invalidates_on_signal_change(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    monkeypatch,
):
    from app.services import arena
    from app.services.market_refresh import refresh_market_data_once

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    refresh_market_data_once()
    original = arena.evaluate_policy_grid
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(arena, "evaluate_policy_grid", counted)
    url = "/api/arena/rebuilt?direction=long"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert calls == 1

    renamed = client.patch(
        f"/api/portfolios/{portfolio['id']}",
        json={"name": "Renamed without recomputing"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200, renamed.text
    payload = client.get(url).json()
    assert next(row for row in payload["portfolios"] if row["id"] == portfolio["id"])["name"] == (
        "Renamed without recomputing"
    )
    assert calls == 1

    signal = client.post(
        f"/api/portfolios/{portfolio['id']}/signals",
        json={"positions": [{"symbol": "AAPL", "weight_pct": 100}], "note": "new input"},
        headers=admin_headers,
    )
    assert signal.status_code == 201, signal.text
    refresh_market_data_once()
    assert client.get(url).status_code == 200
    assert calls == 2


def test_managed_note_change_reuses_numeric_analysis_but_refreshes_holding_note(
    client,
    admin_headers,
    sample_portfolio,
    monkeypatch,
):
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import Position
    from app.services import arena
    from tests.util import backdate_allocation

    backdate_allocation(sample_portfolio["allocation"]["id"], days_back=10)
    original = arena.value_portfolio
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(arena, "value_portfolio", counted)
    url = f"/api/portfolios/{sample_portfolio['id']}/detail"
    first = client.get(url, headers=admin_headers)
    assert first.status_code == 200, first.text
    assert calls == 1

    with session_factory()() as session:
        position = session.scalar(
            select(Position).where(
                Position.allocation_id == sample_portfolio["allocation"]["id"],
                Position.symbol == "AAPL",
            )
        )
        position.note = "current handoff note"
        session.commit()

    second = client.get(url, headers=admin_headers)
    assert second.status_code == 200, second.text
    holdings = {item["symbol"]: item for item in second.json()["portfolio"]["holdings"]}
    assert holdings["AAPL"]["note"] == "current handoff note"
    assert calls == 1


def test_price_content_change_invalidates_managed_analysis(
    client,
    sample_portfolio,
    monkeypatch,
):
    from app.db import session_factory
    from app.models import PriceCache
    from app.services import arena
    from tests.util import backdate_allocation

    backdate_allocation(sample_portfolio["allocation"]["id"], days_back=10)
    original = arena.value_portfolio
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(arena, "value_portfolio", counted)
    url = "/api/arena/managed?direction=long"
    assert client.get(url).status_code == 200
    assert calls == 1

    with session_factory()() as session:
        row = session.get(PriceCache, "SPY")
        updated = [dict(point) for point in row.series]
        updated[-1]["close"] += 1.0
        row.series = updated
        session.commit()

    assert client.get(url).status_code == 200
    assert calls == 2


def test_rebuilt_policy_scope_avoids_unused_grid_cells(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    monkeypatch,
):
    from app.services import arena
    from app.services.market_refresh import refresh_market_data_once

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt)
    refresh_market_data_once()
    original = arena.evaluate_policy_grid
    pair_counts = []

    def counted(*args, **kwargs):
        pair_counts.append(len(kwargs["policy_pairs"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(arena, "evaluate_policy_grid", counted)
    assert client.get("/api/arena/rebuilt?direction=long").status_code == 200
    assert pair_counts == [20]

    detail = client.get(f"/api/portfolios/{portfolio['slug']}")
    assert detail.status_code == 200, detail.text
    assert pair_counts == [20, 200]
    assert len(detail.json()["portfolio"]["policy_matrix"]) == 200
