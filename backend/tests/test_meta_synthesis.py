"""Daily source barriers and scoped frozen execution packets for Meta portfolios."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services import evaluator
from app.services.meta_synthesis import _control_for, render_source_packet, source_packet_for


def test_control_equal_weights_sources_and_keeps_full_symbol_union():
    sources = [
        {
            "portfolio": {"mode": "managed", "direction": "long"},
            "decision_status": "same_session",
            "positions": [
                {"symbol": "AAPL", "weight_pct": 60},
                {"symbol": "MSFT", "weight_pct": 40},
            ],
        },
        {
            "portfolio": {"mode": "managed", "direction": "long"},
            "decision_status": "fallback",
            "positions": [
                {"symbol": "AAPL", "weight_pct": 20},
                {"symbol": "RSP", "weight_pct": 80},
            ],
        },
        {
            "portfolio": {"mode": "managed", "direction": "long"},
            "decision_status": "missing",
            "positions": [],
        },
        {
            "portfolio": {"mode": "managed", "direction": "short"},
            "decision_status": "same_session",
            "positions": [{"symbol": "SPY", "weight_pct": 100}],
        },
    ]

    control = _control_for(sources, "managed", "long", datetime(2026, 8, 4).date())

    assert control["contributor_count"] == 2
    assert control["positions"] == [
        {"symbol": "AAPL", "weight_pct": 40.0},
        {"symbol": "MSFT", "weight_pct": 20.0},
        {"symbol": "RSP", "weight_pct": 40.0},
    ]


def _create_meta_set(client, admin_headers, sample_agent) -> dict:
    prompt_response = client.post(
        "/api/admin/prompts",
        json={
            "name": "Arena Synthesis",
            "context_scope": "arena",
            "mode": "both",
            "direction": "both",
            "managed_long_text": "Synthesize the strongest long allocation.",
            "managed_short_text": "Synthesize the strongest short allocation.",
            "rebuilt_long_text": "Synthesize a fresh long signal.",
            "rebuilt_short_text": "Synthesize a fresh short signal.",
        },
        headers=admin_headers,
    )
    assert prompt_response.status_code == 201, prompt_response.text
    response = client.post(
        "/api/admin/meta-portfolio-sets",
        json={
            "family_name": "Confluence",
            "agent_id": sample_agent["id"],
            "prompt_id": prompt_response.json()["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _claim(session, now: datetime, limit: int = 5) -> dict:
    return evaluator.claim_runs(
        session,
        worker_id="worker-meta-test",
        harness="codex",
        harness_version="codex-cli test",
        limit=limit,
        now=now,
    )


def test_meta_packets_match_agent_and_cell_and_keep_queued_identity(
    client, admin_headers, sample_agent, sample_model, sample_prompt
):
    from app.db import session_factory
    from app.models import Allocation, MetaBatch, Portfolio, Position, Signal, SignalPosition
    from app.services import admin_ops

    first_family = _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        second_agent = admin_ops.create_agent(
            session, model_id=sample_model["id"], harness="codex", reasoning_effort="high"
        )
        second_family = admin_ops.create_meta_portfolio_set(
            session,
            family_name="Other Confluence",
            agent_id=second_agent["id"],
            prompt_id=first_family["prompt_id"],
        )
        expected = {}
        for family in (first_family, second_family):
            for member in family["portfolios"]:
                mode, direction = member["prompt_mode"], member["direction"]
                marker = f"evidence-for-{family['agent_id']}-{mode}-{direction}"
                source = Portfolio(
                    name=marker,
                    slug=marker,
                    agent_id=family["agent_id"],
                    prompt_id=sample_prompt["id"],
                    prompt_mode=mode,
                    direction=direction,
                    cost_bps=10,
                )
                session.add(source)
                decision_type, position_type = (
                    (Allocation, Position) if mode == "managed" else (Signal, SignalPosition)
                )
                decision = decision_type(
                    portfolio=source,
                    entered_at=now - timedelta(days=1),
                    effective_date=now.date() - timedelta(days=1),
                    note=marker,
                    positions=[position_type(symbol="AAPL", weight_pct=100, note=marker)],
                )
                if isinstance(decision, Signal):
                    decision.provenance = "browser_admin"
                session.add(decision)
                expected[member["id"]] = (family["agent_id"], mode, direction, marker)
        session.commit()
        evaluator.update_settings(session, max_concurrency=10)

        # Freeze and queue without claiming, then change the family's live agent.
        assert _claim(session, now, limit=0)["runs"] == []
        batch = session.query(MetaBatch).one()
        frozen = deepcopy(batch.snapshot)
        admin_ops.update_meta_portfolio_set(session, first_family["id"], agent_id=second_agent["id"])
        # Later edits to a source cannot alter its frozen agent or reasoning either.
        source.agent_id = sample_agent["id"]
        decision.note = "edited-after-freeze"
        session.commit()

        runs = _claim(session, now + timedelta(minutes=1), limit=10)["runs"]
        assert len(runs) == 8
        for run in runs:
            agent_id, mode, direction, marker = expected[run["portfolio"]["id"]]
            assert run["agent"]["id"] == agent_id
            rendered = run["execution_prompt"].split("FROZEN ARENA SYNTHESIS SOURCE PACKET\n", 1)[1]
            packet = json.loads(rendered[rendered.index("{") :])
            assert packet["scope"] == {"agent_id": agent_id, "mode": mode, "direction": direction}
            assert [item["note"] for item in packet["sources"]] == [marker]
            assert packet["counts"]["source_total"] == 1
            assert packet["counts"]["fallback_total"] == 1
            assert list(packet["controls"]) == [f"{mode}_{direction}"]
            assert packet["controls"][f"{mode}_{direction}"]["contributor_count"] == 1
            assert "edited-after-freeze" not in rendered
            assert all(other == marker or other not in rendered for *_, other in expected.values())
        session.refresh(batch)
        assert batch.snapshot == frozen


@pytest.mark.parametrize("agent_id", [1, 2])
@pytest.mark.parametrize("mode", ["managed", "rebuilt"])
@pytest.mark.parametrize("direction", ["long", "short"])
def test_filtered_control_and_counts_exclude_other_agents_and_cells(agent_id, mode, direction):
    sources = [
        {
            "portfolio": {"id": index, "mode": source_mode, "direction": source_direction},
            "agent": {"id": source_agent},
            "due": True,
            "run_status": "succeeded",
            "decision_status": "same_session",
            "positions": [{"symbol": symbol, "weight_pct": 100}],
        }
        for index, (source_agent, source_mode, source_direction, symbol) in enumerate(
            [
                (agent_id, mode, direction, "AAPL"),
                (agent_id, mode, direction, "MSFT"),
                (3 - agent_id, mode, direction, "SPY"),
                (agent_id, "rebuilt" if mode == "managed" else "managed", direction, "SPY"),
                (agent_id, mode, "short" if direction == "long" else "long", "SPY"),
            ]
        )
    ]
    snapshot = {
        "session_date": "2026-08-04",
        "created_at": "2026-08-04T19:00:00+00:00",
        "sources": sources,
        "controls": {"unfiltered": {"positions": [{"symbol": "SPY", "weight_pct": 100}]}},
    }
    packet = source_packet_for(snapshot, agent_id=agent_id, mode=mode, direction=direction)
    assert [source["portfolio"]["id"] for source in packet["sources"]] == [0, 1]
    assert packet["counts"] == {
        "source_total": 2,
        "due_total": 2,
        "terminal_total": 2,
        "succeeded_total": 2,
        "fallback_total": 0,
        "missing_total": 0,
    }
    assert packet["controls"][f"{mode}_{direction}"]["positions"] == [
        {"symbol": "AAPL", "weight_pct": 50.0},
        {"symbol": "MSFT", "weight_pct": 50.0},
    ]


def test_meta_runs_wait_for_normal_success_and_skip_cells_without_sources(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import EvaluationRun, MetaBatch

    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )

        first_claim = _claim(session, now)
        assert [run["portfolio"]["id"] for run in first_claim["runs"]] == [sample_portfolio["id"]]
        source_run = first_claim["runs"][0]
        assert "FROZEN ARENA SYNTHESIS SOURCE PACKET" not in source_run["execution_prompt"]

        evaluator.submit_run(
            session,
            run_id=source_run["id"],
            positions=[
                {"symbol": "AAPL", "weight_pct": 55, "note": "consumer evidence"},
                {"symbol": "MSFT", "weight_pct": 45, "note": "cloud evidence"},
            ],
            note="same-session source decision",
            report="source report is deliberately excluded from synthesis",
            now=now + timedelta(minutes=2),
        )

        second_claim = _claim(session, now + timedelta(minutes=3))
        assert {run["portfolio"]["id"] for run in second_claim["runs"]} == {
            portfolio["id"]
            for portfolio in meta_set["portfolios"]
            if portfolio["prompt_mode"] == "managed" and portfolio["direction"] == "long"
        }
        skipped = session.query(EvaluationRun).filter(EvaluationRun.status == "skipped").all()
        assert len(skipped) == 3
        assert all(run.error == evaluator.NO_META_SOURCES and run.attempt_count == 0 for run in skipped)
        assert len({run["meta_batch_id"] for run in second_claim["runs"]}) == 1
        assert all(
            "FROZEN ARENA SYNTHESIS SOURCE PACKET" in run["execution_prompt"] for run in second_claim["runs"]
        )
        assert all(
            "source report is deliberately excluded" not in run["execution_prompt"]
            for run in second_claim["runs"]
        )

        batch = session.get(MetaBatch, second_claim["runs"][0]["meta_batch_id"])
        assert batch.status == "ready"
        assert batch.snapshot_sha256
        assert batch.snapshot["counts"] == {
            "source_total": 1,
            "due_total": 1,
            "terminal_total": 1,
            "succeeded_total": 1,
            "fallback_total": 0,
            "missing_total": 0,
        }
        assert batch.snapshot["controls"]["managed_long"] == {
            "mode": "managed",
            "direction": "long",
            "effective_date": "2026-08-04",
            "contributor_count": 1,
            "positions": [
                {"symbol": "AAPL", "weight_pct": 55.0},
                {"symbol": "MSFT", "weight_pct": 45.0},
            ],
        }


def test_meta_barrier_waits_through_retry_then_uses_failure_fallback(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import MetaBatch
    from tests.util import set_allocation_effective_date

    set_allocation_effective_date(sample_portfolio["allocation"]["id"], date(2026, 7, 1))
    _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        first = _claim(session, now, limit=1)["runs"][0]
        evaluator.fail_run(
            session,
            run_id=first["id"],
            error="temporary source failure",
            now=now + timedelta(minutes=1),
        )

        retry = _claim(session, now + timedelta(minutes=2), limit=1)["runs"]
        assert len(retry) == 1
        assert retry[0]["id"] == first["id"]
        assert retry[0]["portfolio"]["id"] == sample_portfolio["id"]
        evaluator.fail_run(
            session,
            run_id=first["id"],
            error="terminal source failure",
            now=now + timedelta(minutes=3),
        )

        meta_runs = _claim(session, now + timedelta(minutes=4))["runs"]
        assert len(meta_runs) == 1
        batch = session.get(MetaBatch, meta_runs[0]["meta_batch_id"])
        assert batch.status == "ready"
        assert batch.snapshot["counts"]["succeeded_total"] == 0
        assert batch.snapshot["counts"]["fallback_total"] == 1
        source = batch.snapshot["sources"][0]
        assert source["run_status"] == "failed"
        assert source["decision_status"] == "fallback"
        assert source["decision_effective_date"] < batch.session_date.isoformat()


def test_manual_meta_run_requires_and_reuses_latest_ready_batch(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    from app.db import session_factory

    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    meta_portfolio_id = meta_set["portfolios"][0]["id"]
    now = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)
    with session_factory()() as session:
        rejected = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[meta_portfolio_id],
            now=now,
        )
        assert rejected["items"][0]["action"] == "rejected"
        assert "No completed Meta batch" in rejected["items"][0]["reason"]

        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        source_run = _claim(session, now.replace(hour=19), limit=1)["runs"][0]
        evaluator.submit_run(
            session,
            run_id=source_run["id"],
            positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "verified"}],
            note="source",
            report="source",
            now=now.replace(hour=19, minute=2),
        )
        # Advancing the batch queues the scheduled Meta run, so cancel it before
        # exercising the ordinary manual-run path against the same frozen batch.
        scheduled_meta = _claim(session, now.replace(hour=19, minute=3))["runs"]
        for run in scheduled_meta:
            evaluator.fail_run(
                session,
                run_id=run["id"],
                error="test cleanup",
                cancelled=True,
                now=now.replace(hour=19, minute=4),
            )
        manual = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[meta_portfolio_id],
            now=now.replace(hour=19, minute=5),
        )
        assert manual["items"][0]["action"] == "queued"
        assert manual["items"][0]["run"]["meta_batch_id"] is not None
        unmatched = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[meta_set["portfolios"][1]["id"]],
            now=now.replace(hour=19, minute=5),
        )
        assert unmatched["items"][0]["action"] == "rejected"
        assert unmatched["items"][0]["reason"] == evaluator.NO_META_SOURCES


def test_deleted_frozen_source_finishes_as_missing_without_queuing_meta(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import MetaBatch, Portfolio

    _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        _claim(session, now, limit=1)
        batch = session.query(MetaBatch).one()
        assert batch.source_portfolio_ids == [sample_portfolio["id"]]

        portfolio = session.get(Portfolio, sample_portfolio["id"])
        session.delete(portfolio)
        session.commit()

        after_close = _claim(session, now.replace(hour=20, minute=1))
        session.refresh(batch)
        assert after_close["runs"] == []
        assert batch.status == "insufficient"
        assert batch.snapshot["counts"]["missing_total"] == 1
        assert batch.snapshot["sources"][0]["run_status"] == "source_deleted"


def test_source_cohort_is_frozen_before_new_normal_portfolio_is_created(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import MetaBatch

    _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        source_run = _claim(session, now, limit=1)["runs"][0]
        batch = session.get(MetaBatch, source_run["meta_batch_id"])
        assert batch.source_portfolio_ids == [sample_portfolio["id"]]

        created = client.post(
            "/api/portfolios",
            json={
                "name": "Late Normal",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
                "prompt_mode": "managed",
                "direction": "long",
            },
            headers=admin_headers,
        )
        assert created.status_code == 201, created.text

        evaluator.submit_run(
            session,
            run_id=source_run["id"],
            positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "source"}],
            note="source",
            report="source",
            now=now + timedelta(minutes=1),
        )
        _claim(session, now + timedelta(minutes=2))
        session.refresh(batch)
        assert [source["portfolio"]["id"] for source in batch.snapshot["sources"]] == [sample_portfolio["id"]]


def test_meta_target_blocked_by_active_run_is_reconciled_later(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import EvaluationRun, MetaBatch, Portfolio

    meta_set = _create_meta_set(client, admin_headers, sample_agent)
    core_id = meta_set["portfolios"][0]["id"]
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        core = session.get(Portfolio, core_id)
        capability = next(item for item in core.agent.model.capabilities if item.harness == "codex")
        blocking = EvaluationRun(
            portfolio_id=core.id,
            agent_id=core.agent_id,
            model_id=core.agent.model_id,
            trigger_kind="manual",
            harness="codex",
            execution_model_id=capability.execution_model_id,
            reasoning_effort=core.agent.reasoning_effort,
            timeout_seconds=3600,
            max_attempts=2,
            harness_version="codex-cli test",
            worker_id="blocking-worker",
            status="running",
            attempt_count=1,
            started_at=now,
            lease_expires_at=now + timedelta(hours=1),
        )
        session.add(blocking)
        session.commit()
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )

        source = _claim(session, now)["runs"][0]
        assert source["portfolio"]["id"] == sample_portfolio["id"]
        evaluator.submit_run(
            session,
            run_id=source["id"],
            positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "source"}],
            note="source",
            report="source",
            now=now + timedelta(minutes=1),
        )
        first_meta_claim = _claim(session, now + timedelta(minutes=2))["runs"]
        assert core_id not in {run["portfolio"]["id"] for run in first_meta_claim}
        batch = session.query(MetaBatch).one()
        assert batch.pending_target_portfolio_ids == [core_id]

        evaluator.fail_run(
            session,
            run_id=blocking.id,
            error="blocking work finished",
            cancelled=True,
            now=now + timedelta(minutes=3),
        )
        reconciled = _claim(session, now + timedelta(minutes=4))["runs"]
        assert len(reconciled) == 1
        assert reconciled[0]["portfolio"]["id"] == core_id
        assert reconciled[0]["scheduled_for"] == "2026-08-04"
        assert reconciled[0]["meta_batch_id"] == batch.id
        session.refresh(batch)
        assert batch.pending_target_portfolio_ids == []


def test_failed_scheduled_meta_retry_keeps_batch_session_after_close(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
    sample_model,
):
    from app.db import session_factory
    from app.models import Allocation, EvaluationRun, MetaBatch
    from app.services import admin_ops

    family = _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        source = _claim(session, now, limit=1)["runs"][0]
        evaluator.submit_run(
            session,
            run_id=source["id"],
            positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "source"}],
            note="source",
            report="source",
            now=now + timedelta(minutes=1),
        )
        meta_runs = _claim(session, now + timedelta(minutes=2))["runs"]
        original = meta_runs[0]
        for run in meta_runs[1:]:
            evaluator.fail_run(
                session,
                run_id=run["id"],
                error="test cleanup",
                cancelled=True,
                now=now + timedelta(minutes=3),
            )
        original_row = session.get(EvaluationRun, original["id"])
        original_row.attempt_count = original_row.max_attempts
        session.commit()
        evaluator.fail_run(
            session,
            run_id=original["id"],
            error="terminal Meta failure",
            now=now + timedelta(minutes=3),
        )

        after_close = now.replace(hour=20, minute=30)
        replacement = admin_ops.create_agent(
            session, model_id=sample_model["id"], harness="codex", reasoning_effort="high"
        )
        admin_ops.update_meta_portfolio_set(session, family["id"], agent_id=replacement["id"])
        with pytest.raises(admin_ops.AdminOpError, match="No usable frozen source decisions"):
            evaluator.retry_run(session, run_id=original["id"], now=after_close)
        admin_ops.update_meta_portfolio_set(session, family["id"], agent_id=sample_agent["id"])
        retry = evaluator.retry_run(session, run_id=original["id"], now=after_close)
        assert retry["run"]["scheduled_for"] == "2026-08-04"
        assert retry["run"]["meta_batch_id"] == original["meta_batch_id"]
        claimed = _claim(session, after_close + timedelta(minutes=1), limit=1)["runs"][0]
        batch = session.get(MetaBatch, claimed["meta_batch_id"])
        assert batch.snapshot_sha256
        submitted = evaluator.submit_run(
            session,
            run_id=claimed["id"],
            positions=[{"symbol": "MSFT", "weight_pct": 100, "note": "retry"}],
            note="retry",
            report="retry",
            now=after_close + timedelta(minutes=2),
        )
        allocation = session.get(Allocation, submitted["result"]["id"])
        assert allocation.effective_date.isoformat() == "2026-08-04"


def test_unlinked_active_source_blocks_batch_past_close(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
):
    from app.db import session_factory
    from app.models import MetaBatch
    from tests.util import set_allocation_effective_date

    set_allocation_effective_date(sample_portfolio["allocation"]["id"], date(2026, 7, 1))
    _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 18, 40, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_settings(session, attempt_timeout_seconds=7200)
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[sample_portfolio["id"]],
            now=now,
        )
        manual_id = queued["items"][0]["run"]["id"]
        claimed = _claim(session, now, limit=1)["runs"][0]
        assert claimed["id"] == manual_id
        batch = session.query(MetaBatch).one()

        after_close = now.replace(hour=20, minute=1)
        assert _claim(session, after_close)["runs"] == []
        session.refresh(batch)
        assert batch.status == "waiting"

        evaluator.submit_run(
            session,
            run_id=manual_id,
            positions=[{"symbol": "MSFT", "weight_pct": 100, "note": "next session"}],
            note="manual completed after close",
            report="manual",
            now=after_close + timedelta(minutes=1),
        )
        meta_runs = _claim(session, after_close + timedelta(minutes=2))["runs"]
        assert len(meta_runs) == 1
        session.refresh(batch)
        assert batch.status == "ready"
        assert batch.snapshot["sources"][0]["decision_status"] == "fallback"
        assert batch.snapshot["sources"][0]["decision_effective_date"] < "2026-08-04"


def test_source_packet_truncates_only_prose_and_rejects_structural_overflow():
    snapshot = {
        "session_date": "2026-08-04",
        "sources": [
            {
                "portfolio": {
                    "id": 1,
                    "slug": "source",
                    "mode": "managed",
                    "direction": "long",
                },
                "strategy": {"id": 2, "question_or_notes": "q" * 5_000},
                "note": "n" * 5_000,
                "positions": [
                    {"symbol": "AAPL", "weight_pct": 100, "note": "p" * 5_000},
                ],
            }
        ],
    }

    rendered = render_source_packet(snapshot, max_chars=1_000)

    assert len(rendered) <= 1_000
    assert '"symbol":"AAPL"' in rendered
    assert '"weight_pct":100' in rendered
    assert "[truncated " in rendered

    structural = {
        "sources": [
            {
                "portfolio": {"id": index, "slug": f"source-{index}"},
                "positions": [{"symbol": f"SYM{index}", "weight_pct": 100}],
            }
            for index in range(100)
        ]
    }
    try:
        render_source_packet(structural, max_chars=500)
    except RuntimeError as exc:
        assert "structure exceeds" in str(exc)
    else:
        raise AssertionError("Expected structural packet overflow to fail before queueing")


def test_unrenderable_packet_fails_batch_before_meta_runs_are_queued(
    client,
    admin_headers,
    sample_agent,
    sample_portfolio,
    monkeypatch,
):
    from app.db import session_factory
    from app.models import EvaluationRun, MetaBatch

    _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        evaluator.update_portfolio_config(
            session,
            portfolio_id=sample_portfolio["id"],
            enabled=True,
            weekdays=[0, 1, 2, 3, 4],
        )
        source = _claim(session, now, limit=1)["runs"][0]
        evaluator.submit_run(
            session,
            run_id=source["id"],
            positions=[{"symbol": "AAPL", "weight_pct": 100, "note": "source"}],
            note="source",
            report="source",
            now=now + timedelta(minutes=1),
        )

        def fail_packet(_snapshot):
            raise RuntimeError("packet structure too large")

        monkeypatch.setattr(evaluator, "render_source_packet", fail_packet)

        assert _claim(session, now + timedelta(minutes=2))["runs"] == []
        batch = session.query(MetaBatch).one()
        assert batch.status == "failed"
        assert batch.error == "packet structure too large"
        target_run_count = (
            session.query(EvaluationRun)
            .filter(EvaluationRun.portfolio_id.in_(batch.target_portfolio_ids))
            .count()
        )
        assert target_run_count == 0
