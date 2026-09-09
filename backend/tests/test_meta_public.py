"""Public Meta Arena isolation, redaction, and SPY comparisons."""

from datetime import UTC, date, datetime, timedelta

import pytest

from .util import backdate_allocation


def _create_meta_set(client, admin_headers, sample_agent) -> dict:
    prompt = client.post(
        "/api/admin/prompts",
        json={
            "name": "Arena Synthesis",
            "context_scope": "arena",
            "mode": "both",
            "direction": "both",
            "managed_long_text": "Synthesize managed long evidence.",
            "managed_short_text": "Synthesize managed short evidence.",
            "rebuilt_long_text": "Synthesize rebuilt long evidence.",
            "rebuilt_short_text": "Synthesize rebuilt short evidence.",
        },
        headers=admin_headers,
    )
    assert prompt.status_code == 201, prompt.text
    created = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt.json()["id"],
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    return created.json()


def _member(meta_set: dict, mode: str, direction: str) -> dict:
    return next(
        item
        for item in meta_set["portfolios"]
        if item["prompt_mode"] == mode and item["direction"] == direction
    )


def _ready_batch(effective_date: date) -> None:
    from app.db import session_factory
    from app.models import MetaBatch

    cells = {}
    for mode, direction, symbol in (
        ("managed", "long", "AAPL"),
        ("managed", "short", "MSFT"),
        ("rebuilt", "long", "AAPL"),
        ("rebuilt", "short", "MSFT"),
    ):
        cells[f"{mode}_{direction}"] = {
            "mode": mode,
            "direction": direction,
            "effective_date": effective_date.isoformat(),
            "contributor_count": 3,
            "positions": [{"symbol": symbol, "weight_pct": 100}],
        }
    with session_factory()() as session:
        session.add(
            MetaBatch(
                session_date=effective_date,
                status="ready",
                source_portfolio_ids=[1, 2, 3],
                due_source_portfolio_ids=[1, 2],
                target_portfolio_ids=[4, 5, 6, 7],
                snapshot_sha256="a" * 64,
                sources_finished_at=datetime.now(UTC),
                snapshot={
                    "schema_version": 1,
                    "formula_version": "same_cell_equal_source_v1",
                    "counts": {
                        "source_total": 3,
                        "due_total": 2,
                        "terminal_total": 2,
                        "succeeded_total": 1,
                        "fallback_total": 1,
                        "missing_total": 0,
                    },
                    "sources": [
                        {
                            "portfolio_note": "never expose this source thesis",
                            "positions": [{"symbol": "AAPL", "note": "private position note"}],
                        }
                    ],
                    "controls": cells,
                },
            )
        )
        session.commit()


def _insert_signals(portfolio_id: int, effective_dates: list[date], symbol: str) -> None:
    from app.db import session_factory
    from app.models import Signal, SignalPosition

    with session_factory()() as session:
        for index, effective_date in enumerate(effective_dates):
            signal = Signal(
                portfolio_id=portfolio_id,
                entered_at=datetime.combine(
                    effective_date - timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                effective_date=effective_date,
                note=f"signal {index}",
                provenance="integrated",
            )
            signal.positions.append(SignalPosition(symbol=symbol, weight_pct=100, note="private"))
            session.add(signal)
        session.commit()
    from app.services.market_refresh import refresh_market_data_once

    refresh_market_data_once()


def _trading_days(start: date, count: int) -> list[date]:
    from app.services.trading_calendar import is_trading_day

    days = []
    current = start
    while len(days) < count:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def test_meta_managed_is_isolated_redacted_and_compares_only_with_spy(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    core = _member(meta_set, "managed", "long")
    allocation = client.post(
        f"/api/portfolios/{core['id']}/allocations",
        json={
            "positions": [{"symbol": "MSFT", "weight_pct": 100}],
            "note": "meta allocation",
        },
        headers=admin_headers,
    )
    assert allocation.status_code == 201, allocation.text
    backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
    backdate_allocation(allocation.json()["id"], days_back=45)
    effective_date = date.today() - timedelta(days=45)
    _ready_batch(effective_date)

    normal = client.get("/api/arena/managed?direction=long")
    assert normal.status_code == 200, normal.text
    assert core["slug"] not in {row["slug"] for row in normal.json()["portfolios"]}

    response = client.get("/api/meta/managed?direction=long")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["batch"] == {
        **{
            key: payload["batch"][key]
            for key in (
                "id",
                "session_date",
                "sources_finished_at",
                "created_at",
                "updated_at",
            )
        },
        "status": "ready",
        "error": None,
        "snapshot_sha256": "a" * 64,
        "source_count": 3,
        "due_count": 2,
        "terminal_count": 2,
        "success_count": 1,
        "fallback_count": 1,
        "missing_count": 0,
        "target_count": 4,
    }
    slugs = [row["slug"] for row in payload["portfolios"]]
    assert slugs == ["spy", core["slug"]]
    assert "control" not in payload
    assert "never expose this source thesis" not in response.text
    assert "private position note" not in response.text
    detail = client.get(f"/api/portfolios/{core['slug']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["portfolio"]["execution_prompt"] is None
    assert (
        "supplied only by the integrated evaluator" in detail.json()["portfolio"]["execution_context_notice"]
    )
    assert "never expose this source thesis" not in detail.text

    assert client.get(f"/api/compare?track=managed&direction=long&slugs={core['slug']}").status_code == 404
    assert (
        client.get(
            "/api/meta/compare",
            params={
                "track": "managed",
                "direction": "long",
                "slugs": sample_portfolio["slug"],
            },
        ).status_code
        == 404
    )
    comparison = client.get(
        "/api/meta/compare",
        params={"track": "managed", "direction": "long", "slugs": core["slug"]},
    )
    assert comparison.status_code == 200, comparison.text
    assert "control_series" not in comparison.json()
    assert comparison.json()["spy_series"]
    assert comparison.json()["series"][0]["slug"] == core["slug"]


def test_rebuilt_meta_uses_normal_common_policy_without_joining_normal_arena(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    normal = client.post(
        "/api/portfolios",
        json={
            "name": "Normal Rebuilt",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "rebuilt",
            "direction": "long",
        },
        headers=admin_headers,
    )
    assert normal.status_code == 201, normal.text
    normal = normal.json()
    days = _trading_days(date.today() - timedelta(days=90), 45)
    _insert_signals(normal["id"], days, "AAPL")

    baseline = client.get("/api/arena/rebuilt?direction=long")
    assert baseline.status_code == 200, baseline.text
    baseline_policy = baseline.json()["common_policy"]
    assert baseline_policy is not None

    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    pulse = _member(meta_set, "rebuilt", "long")
    _insert_signals(pulse["id"], days, "MSFT")
    _ready_batch(days[-1])

    normal_after = client.get("/api/arena/rebuilt?direction=long")
    assert normal_after.status_code == 200, normal_after.text
    assert normal_after.json()["common_policy"] == baseline_policy
    assert pulse["slug"] not in {row["slug"] for row in normal_after.json()["portfolios"]}

    meta = client.get("/api/meta/rebuilt?direction=long")
    assert meta.status_code == 200, meta.text
    payload = meta.json()
    assert payload["common_policy"] == baseline_policy
    assert pulse["slug"] in {row["slug"] for row in payload["portfolios"]}
    assert "control" not in payload
    assert {row["kind"] for row in payload["portfolios"]} == {"benchmark", "rebuilt"}
    ranked = [row for row in payload["portfolios"] if row.get("rank") is not None]
    assert all(row["kind"] == "rebuilt" for row in ranked)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize(
    "track,view,horizon",
    [
        ("managed", "common", None),
        ("rebuilt", "common", None),
        ("rebuilt", "tuned", None),
        ("rebuilt", "signal", 10),
    ],
)
def test_saved_controls_do_not_appear_in_empty_meta_views(
    client, admin_headers, sample_agent, direction, track, view, horizon
):
    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    member = _member(meta_set, track, direction)
    _ready_batch(date.today() - timedelta(days=45))
    params = {"direction": direction}
    if track == "rebuilt":
        params["view"] = view
        if view == "signal":
            params["cost_basis"] = "gross"
        if horizon is not None:
            params["horizon"] = horizon
    response = client.get(f"/api/meta/{track}", params=params)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "control" not in payload
    assert [row["slug"] for row in payload["portfolios"]] == ["spy", member["slug"]]
    comparison = client.get("/api/meta/compare", params={**params, "track": track, "slugs": member["slug"]})
    assert comparison.status_code == 200, comparison.text
    assert "control_series" not in comparison.json()
    assert comparison.json()["series"] == []


def test_normal_operational_mcp_reads_exclude_meta_portfolios(
    client,
    admin_headers,
    mcp_headers,
    sample_agent,
    sample_portfolio,
):
    from .test_mcp import _call_tool

    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    meta_slugs = {portfolio["slug"] for portfolio in meta_set["portfolios"]}
    overview = _call_tool(client, mcp_headers, "get_arena_overview", {"direction": "long"})
    overview_slugs = {
        row["slug"] for track in ("managed", "rebuilt") for row in overview[track]["portfolios"]
    }
    assert meta_slugs.isdisjoint(overview_slugs)
    assert sample_portfolio["slug"] in overview_slugs

    rebuilt = _call_tool(client, mcp_headers, "get_rebuilt_analysis", {"direction": "long"})
    assert meta_slugs.isdisjoint({row["slug"] for row in rebuilt["portfolios"]})
