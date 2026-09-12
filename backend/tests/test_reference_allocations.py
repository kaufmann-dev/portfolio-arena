"""Reference-backed decisions, hand-calculated returns, and terminal abstentions."""

import asyncio
import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.evaluator import worker
from app.services import evaluator
from app.services.admin_ops import AdminOpError
from app.services.prompt_policy import allocation_policy_from_limits, validate_position_weights
from app.services.rebuilt import HORIZONS, SignalInput, construct_policy, signal_horizon_statistics
from app.services.symbols import SymbolValidationError, validate_positions
from app.services.valuation import AllocationInput, PositionInput, rebase_series

from .test_evaluator import _run, _settings
from .test_rebuilt_analytics import market
from .test_valuation import DAYS, boundary, prices, value


@pytest.mark.parametrize(
    "minimum,maximum,weights",
    [
        (10, 25, [25, 25, 25]),
        (10, 50, [50]),
        (34, 40, [40, 40]),
        (60, 70, [70]),
        (10, 25, []),
        (10, 25, [25] * 4),
        (10, 33.3333, [33.3333] * 3),
    ],
)
def test_capacity_sizing(minimum, maximum, weights):
    policy = allocation_policy_from_limits(minimum, maximum)
    positions = [{"symbol": f"T{i}", "weight_pct": weight} for i, weight in enumerate(weights)]
    validate_positions(positions)
    validate_position_weights(policy, positions)
    assert policy["derived_min_positions"] == 0


@pytest.mark.parametrize("weights", [[20, 20, 20], [30, 25, 20], [25, 25, 25, 24], [5, 25, 25, 25, 20]])
def test_underallocation_and_limit_violations_rejected(weights):
    with pytest.raises(ValueError):
        validate_position_weights(
            allocation_policy_from_limits(10, 25),
            [{"symbol": f"T{i}", "weight_pct": weight} for i, weight in enumerate(weights)],
        )


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf"), 25.00001])
def test_nonpositive_nonfinite_or_overprecision_weights_rejected(weight):
    with pytest.raises(SymbolValidationError):
        validate_positions([{"symbol": "AAPL", "weight_pct": weight}])


def test_extreme_finite_weight_is_a_validation_error():
    with pytest.raises(SymbolValidationError, match="exceed 100"):
        validate_positions([{"symbol": "AAPL", "weight_pct": 1e300}])


@pytest.mark.parametrize(
    "direction,expected", [("long", [100, 105, 110.5, 116]), ("short", [100, 95, 90.5, 86])]
)
def test_managed_partial_reference_uses_benchmark_resets(direction, expected):
    data = {
        "AAPL": prices([(100, 100), (100, 100), (100, 100)]),
        "SPY": prices([(100, 110), (121, 132), (132, 132)]),
    }
    result = value(
        [AllocationInput(DAYS[0], (PositionInput("AAPL", 50),))],
        data,
        phase="open",
        direction=direction,
        as_of=boundary(DAYS[1]),
    )
    assert [point["nav"] for point in result.series] == pytest.approx(expected)
    assert result.reference_holding["target_weight_pct"] == 50
    assert result.reference_holding["weight_pct"] == pytest.approx((expected[-1] - 50) / expected[-1] * 100)
    assert [holding.symbol for holding in result.holdings] == ["AAPL"]


@pytest.mark.parametrize("direction", ["long", "short"])
def test_managed_abstention_replaces_stale_positions(direction):
    data = {
        "AAPL": prices([(100, 100), (100, 200), (250, 300)]),
        "SPY": prices([(100, 100), (100, 110), (121, 132)]),
    }
    result = value(
        [AllocationInput(DAYS[0], (PositionInput("AAPL", 100),)), AllocationInput(DAYS[1], ())],
        data,
        phase="open",
        direction=direction,
    )
    expected = rebase_series(data["SPY"], boundary(DAYS[1], "open"), boundary(DAYS[-1]), direction)
    assert [point["nav"] for point in result.series[2:]] == pytest.approx(
        [point["nav"] for point in expected]
    )
    assert result.holdings == []
    assert result.reference_holding == {"weight_pct": 100, "target_weight_pct": 100}
    assert result.cumulative_turnover_pct == pytest.approx(100)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_initial_abstention_tracks_reference(direction):
    data = {"SPY": prices([(100, 110), (121, 132), (140, 150)])}
    result = value([AllocationInput(DAYS[0], ())], data, phase="open", direction=direction)
    assert result.series == rebase_series(
        data["SPY"], boundary(DAYS[0], "open"), boundary(DAYS[-1]), direction
    )


def test_explicit_long_spy_and_reference_are_separate_but_transfers_have_no_turnover():
    data = {"SPY": prices([(100, 100), (110, 110), (120, 120)])}
    result = value(
        [AllocationInput(DAYS[0], (PositionInput("SPY", 50),)), AllocationInput(DAYS[1], ())], data
    )
    assert result.cumulative_turnover_pct == pytest.approx(0)
    assert result.series[-1]["nav"] == pytest.approx(120)
    assert result.holdings == []


@pytest.mark.parametrize("direction,expected", [("long", 0.105), ("short", -0.095)])
def test_rebuilt_partial_horizon_includes_reference_return(direction, expected):
    days, data, calendar = market()
    data["SPY"][0]["close"] = 110
    data["SPY"][1]["open"] = 121
    stats = signal_horizon_statistics(
        [SignalInput(1, days[0], (PositionInput("AAPL", 50),))],
        data,
        calendar,
        1,
        direction=direction,
        execution_boundary="open",
    )
    cohort = stats["completed_cohorts"][0]
    assert cohort["signal_return"] == pytest.approx(expected)
    assert cohort["daily_alpha"] == pytest.approx((1 + expected) / (1 + cohort["spy_return"]) - 1)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_abstentions_are_zero_alpha_observations_at_every_horizon(direction):
    days, data, calendar = market(24)
    for index, point in enumerate(data["SPY"]):
        point["open"] = 100 + index
        point["close"] = 100.5 + index
    signals = [SignalInput(1, days[0], ()), SignalInput(2, days[1], ())]
    for horizon in HORIZONS:
        stats = signal_horizon_statistics(signals, data, calendar, horizon, direction=direction)
        assert stats["complete_count"] == 2
        assert stats["mean_daily_alpha"] == pytest.approx(0, abs=1e-12)
        assert stats["eligible"] is True
    policy = construct_policy(signals, data, calendar, 20, direction=direction)
    assert [p["nav"] for p in policy.series] == pytest.approx([p["nav"] for p in policy.spy_series])
    assert policy.holdings == []
    assert policy.reference_holding["weight_pct"] == pytest.approx(100)


def test_new_abstention_preserves_older_active_cohort():
    days, data, calendar = market()
    result = construct_policy(
        [SignalInput(1, days[0], (PositionInput("AAPL", 100),)), SignalInput(2, days[1], ())],
        data,
        calendar[:4],
        2,
        execution_boundary="open",
    )
    assert result.holdings == [{"symbol": "AAPL", "weight_pct": 50}]
    assert result.reference_holding == {"weight_pct": 50}
    assert len(result.active_cohorts) == 2


def new_portfolio(client, admin_headers, sample_agent, sample_prompt, mode):
    response = client.post(
        "/api/portfolios",
        headers=admin_headers,
        json={
            "version_id": 1,
            "name": f"Reference {mode}",
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": mode,
            "direction": "long",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("abstained", [False, True])
def test_decision_rest_contract_and_explanation(
    client, admin_headers, sample_agent, sample_prompt, mode, abstained
):
    portfolio = new_portfolio(client, admin_headers, sample_agent, sample_prompt, mode)
    settings = client.get("/api/settings", headers=admin_headers).json()
    settings[f"{mode}_allocation_policy"]["max_position_weight_pct"] = 50
    assert client.put("/api/settings", headers=admin_headers, json=settings).status_code == 200
    positions = [] if abstained else [{"symbol": "AAPL", "weight_pct": 50}]
    route = f"/api/portfolios/{portfolio['id']}/{'allocations' if mode == 'managed' else 'signals'}"
    assert client.post(route, headers=admin_headers, json={"positions": positions}).status_code == 422
    response = client.post(
        route,
        headers=admin_headers,
        json={"positions": positions, "note": "Research found insufficient qualifying selections."},
    )
    assert response.status_code == 201, response.text
    assert response.json()["reference_weight_pct"] == (100 if abstained else 50)
    assert response.json()["outcome"] == ("abstained" if abstained else "partially_allocated")


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("recover", [False, True])
def test_successful_abstention_is_terminal_and_cannot_retry(
    client, admin_headers, sample_agent, sample_prompt, mode, recover
):
    from app.db import session_factory

    portfolio = new_portfolio(client, admin_headers, sample_agent, sample_prompt, mode)
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(session, portfolio_id=portfolio["id"], enabled=True, weekdays=[])
        queued = evaluator.enqueue_manual_runs(session, portfolio_ids=[portfolio["id"]], now=now)
        run_id = queued["items"][0]["run"]["id"]

        def claim():
            return evaluator.claim_runs(
                session, worker_id="test", harness="codex", harness_version="test", limit=1, now=now
            )

        assert len(claim()["runs"]) == 1
        if recover:
            failed = evaluator.fail_run(
                session,
                run_id=run_id,
                error="temporary research outage",
                report="Research interrupted.",
                now=now,
            )
            assert failed["status"] == "queued"
            assert failed["report"] == "Research interrupted."
            assert len(claim()["runs"]) == 1
        submitted = evaluator.submit_run(
            session,
            run_id=run_id,
            positions=[],
            note="No qualifying securities.",
            report="Research completed; candidates do not meet the strategy.",
            now=now + timedelta(minutes=1),
        )
        run = submitted["run"]
        assert run["status"] == "succeeded"
        assert run["outcome"] == "abstained"
        assert run["attempt_count"] == (2 if recover else 1)
        assert run["error"] is None
        assert run["report"].startswith("Research completed")
        assert claim()["runs"] == []
        assert (
            evaluator.fail_run(session, run_id=run_id, error="late failure", now=now)["status"] == "succeeded"
        )
        with pytest.raises(AdminOpError, match="Only failed"):
            evaluator.retry_run(session, run_id=run_id)


@pytest.mark.parametrize("harness", ["codex", "muse"])
@pytest.mark.parametrize("abstained", [False, True])
def test_worker_submits_completed_scarcity_without_failure(tmp_path, monkeypatch, harness, abstained):
    calls = []
    result = worker.Proposal(
        status="abstained" if abstained else "proposal",
        positions=[] if abstained else [{"symbol": "AAPL", "weight_pct": 50, "note": "Qualifies"}],
        note="Insufficient qualifying candidates.",
        report="Completed research and rejected candidates.",
        error="",
        blocked_reason=None,
    )

    async def harness_run(*_args):
        return result

    async def request(_settings, method, path, payload=None):
        calls.append((method, path, payload))
        return {}

    monkeypatch.setattr(worker, f"run_{harness}", harness_run)
    monkeypatch.setattr(worker, "internal_request", request)
    run = _run().model_copy(update={"harness": harness})
    asyncio.run(worker.evaluate_run(_settings(tmp_path), run))
    assert len(calls) == 1
    assert calls[0][1] == "/runs/17/submit"
    assert calls[0][2]["positions"] == [position.model_dump() for position in result.positions]
    assert calls[0][2]["report"] == result.report


def test_instruction_migration_updates_saved_constraints_without_overwriting_custom_text(monkeypatch):
    from app.db import session_factory

    path = Path(__file__).parents[1] / "alembic/versions/0028_reference_allocations.py"
    spec = importlib.util.spec_from_file_location("reference_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with session_factory()() as session:
        session.execute(
            text("UPDATE settings SET value = :value WHERE key = 'long_direction_instructions'"),
            {
                "value": "Custom research rule.\n"
                "- Invest exactly 100% of NAV across USD-denominated equities and ETFs."
            },
        )
        monkeypatch.setattr(migration.op, "get_bind", session.connection)
        migration.upgrade()
        value = session.execute(
            text("SELECT value FROM settings WHERE key = 'long_direction_instructions'")
        ).scalar_one()
        assert value.startswith("Custom research rule.")
        assert "exactly 100%" not in value
        assert "remainder in long SPY" in value


@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
def test_mcp_reference_decisions_public_participation_and_locking(
    client, admin_headers, mcp_headers, sample_agent, sample_prompt, mode
):
    from app.db import session_factory
    from app.models import Allocation, Signal
    from app.services.market_refresh import refresh_market_data_once

    from .test_mcp import _call_tool

    portfolio = new_portfolio(client, admin_headers, sample_agent, sample_prompt, mode)
    settings = _call_tool(client, mcp_headers, "get_settings")
    settings[f"{mode}_allocation_policy"]["max_position_weight_pct"] = 50
    updated = _call_tool(client, mcp_headers, "update_settings", settings)
    assert updated[f"{mode}_allocation_policy"]["derived_min_positions"] == 0
    kind = "allocation" if mode == "managed" else "signal"
    model = Allocation if mode == "managed" else Signal
    saved = []
    for day, positions in [
        (date(2026, 7, 20), []),
        (date(2026, 7, 21), [{"symbol": "AAPL", "weight_pct": 50, "note": "Private thesis"}]),
    ]:
        decision = _call_tool(
            client,
            mcp_headers,
            f"create_{kind}",
            {
                "portfolio_id": portfolio["id"],
                "positions": positions,
                "note": "Researched candidates; only qualifying selections submitted.",
            },
        )
        saved.append(decision)
        with session_factory()() as session:
            record = session.get(model, decision["id"])
            record.effective_date = day
            record.entered_at = datetime(2026, 7, 19, tzinfo=UTC)
            session.commit()
    pending = _call_tool(
        client,
        mcp_headers,
        f"create_{kind}",
        {"portfolio_id": portfolio["id"], "positions": [], "note": "No qualifying candidates."},
    )
    assert pending["outcome"] == "abstained"
    refresh_market_data_once()
    detail = client.get(f"/api/portfolios/{portfolio['slug']}").json()["portfolio"]
    assert detail["participation"] == {
        "decision_count": 2,
        "selected_count": 1,
        "abstention_count": 1,
        "participation_rate": 0.5,
    }
    assert detail["reference_holding"]["weight_pct"] > 0
    history = detail["allocations" if mode == "managed" else "signals"]
    assert {decision["outcome"] for decision in history} == {"abstained", "partially_allocated"}
    for decision in history:
        for position in decision["positions"]:
            assert "note" not in position
    for holding in detail["holdings"]:
        assert "note" not in holding and "entry_price" not in holding and "current_price" not in holding
    assert (
        client.put(
            f"/api/{kind}s/{saved[0]['id']}",
            headers=admin_headers,
            json={"positions": [], "note": "Change locked decision"},
        ).status_code
        == 403
    )
    assert (
        client.put(f"/api/{kind}s/{pending['id']}", headers=admin_headers, json={"note": ""}).status_code
        == 422
    )
    _call_tool(client, mcp_headers, f"delete_{kind}", {f"{kind}_id": saved[0]["id"]})
    detail = client.get(f"/api/portfolios/{portfolio['slug']}").json()["portfolio"]
    assert detail["participation"]["abstention_count"] == 0
    _call_tool(client, mcp_headers, "reset_portfolio", {"portfolio_id": portfolio["id"]})
    detail = client.get(f"/api/portfolios/{portfolio['slug']}").json()["portfolio"]
    assert detail["participation"]["decision_count"] == 0
    assert detail["reference_holding"] is None
