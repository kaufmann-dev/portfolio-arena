"""Public Meta Arena isolation, redaction, controls, and comparisons."""

from datetime import UTC, date, datetime, timedelta

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


def _ready_batch(
    effective_date: date,
    *,
    managed_long_positions: list[dict] | None = None,
    managed_long_contributors: int = 3,
) -> None:
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
    if managed_long_positions is not None:
        cells["managed_long"]["positions"] = managed_long_positions
        cells["managed_long"]["contributor_count"] = managed_long_contributors
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


def _trading_days(start: date, count: int) -> list[date]:
    from app.services.trading_calendar import is_trading_day

    days = []
    current = start
    while len(days) < count:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def test_meta_managed_is_isolated_redacted_and_has_control(
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
    assert slugs == ["spy", "consensus-control-managed-long", core["slug"]]
    assert payload["control"]["kind"] == "control"
    assert payload["control"]["rank"] is None
    assert payload["control"]["contributor_count"] == 3
    assert payload["control"]["formula_version"] == "same_cell_equal_source_v1"
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
    assert comparison.json()["control_series"]["kind"] == "control"
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
    assert payload["control"]["kind"] == "control"
    ranked = [row for row in payload["portfolios"] if row.get("rank") is not None]
    assert all(row["kind"] == "rebuilt" for row in ranked)


def test_control_formula_and_public_history_keep_equal_source_full_union(client):
    from app.db import session_factory
    from app.services.meta import control_history
    from app.services.meta_synthesis import _control_for

    sources = [
        {
            "portfolio": {"mode": "managed", "direction": "long"},
            "decision_status": "same_session",
            "positions": [{"symbol": "AAPL", "weight_pct": 100}],
        },
        {
            "portfolio": {"mode": "managed", "direction": "long"},
            "decision_status": "fallback",
            "positions": [
                {"symbol": "MSFT", "weight_pct": 50},
                {"symbol": "RSP", "weight_pct": 50},
            ],
        },
        {
            "portfolio": {"mode": "managed", "direction": "long"},
            "decision_status": "missing",
            "positions": [],
        },
    ]
    control = _control_for(sources, "managed", "long", date(2026, 6, 1))
    assert control["contributor_count"] == 2
    assert control["positions"] == [
        {"symbol": "AAPL", "weight_pct": 50.0},
        {"symbol": "MSFT", "weight_pct": 25.0},
        {"symbol": "RSP", "weight_pct": 25.0},
    ]

    _ready_batch(
        date(2026, 6, 1),
        managed_long_positions=control["positions"],
        managed_long_contributors=2,
    )
    _ready_batch(
        date(2026, 6, 2),
        managed_long_positions=[{"symbol": "AAPL", "weight_pct": 100}],
        managed_long_contributors=1,
    )
    with session_factory()() as session:
        history, latest_session = control_history(session, "managed", "long")
    assert history is not None
    assert latest_session == "2026-06-02"
    assert len(history.allocations) == 2
    assert [(position.symbol, position.weight_pct) for position in history.allocations[0].positions] == [
        ("AAPL", 50.0),
        ("MSFT", 25.0),
        ("RSP", 25.0),
    ]
    assert history.allocations[1].contributor_count == 1


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
