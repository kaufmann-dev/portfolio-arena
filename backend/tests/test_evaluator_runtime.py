"""Per-harness worker health and the shared evaluation queue."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db import session_factory
from app.services import admin_ops, evaluator


def _heartbeat(
    session,
    *,
    harness,
    now,
    instance_id=None,
    status="idle",
    authenticated=True,
    active_run_count=0,
    last_error=None,
    harness_version="test-version",
):
    evaluator.heartbeat(
        session,
        instance_id=instance_id or f"{harness}-worker",
        harness=harness,
        status=status,
        harness_version=harness_version,
        authenticated=authenticated,
        active_run_count=active_run_count,
        last_error=last_error,
        now=now,
    )


def test_runtime_lists_every_harness_before_workers_connect():
    with session_factory()() as session:
        runtime = evaluator.get_dashboard(session)["runtime"]

    assert set(runtime) == {
        "online",
        "status",
        "active_run_count",
        "instance_count",
        "last_heartbeat_at",
        "harnesses",
    }
    assert runtime["online"] is False
    assert runtime["status"] == "offline"
    assert runtime["instance_count"] == 0
    assert runtime["last_heartbeat_at"] is None
    assert {row["harness"] for row in runtime["harnesses"]} == {"codex", "muse"}
    for row in runtime["harnesses"]:
        assert row["harness_name"]
        assert row["online"] is False
        assert row["status"] == "offline"
        assert row["authenticated"] is False
        assert row["harness_version"] is None
        assert row["last_heartbeat_at"] is None
        assert row["last_error"] is None
        assert row["active_run_count"] == row["instance_count"] == 0


@pytest.mark.parametrize("active_run_count,expected_status", [(0, "idle"), (2, "running")])
def test_muse_authentication_failure_does_not_mask_healthy_codex(active_run_count, expected_status):
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _heartbeat(
            session,
            harness="codex",
            status=expected_status,
            active_run_count=active_run_count,
            now=now - timedelta(seconds=10),
        )
        _heartbeat(
            session,
            harness="muse",
            status="authentication_required",
            authenticated=False,
            last_error="Run muse login.",
            now=now,
        )
        runtime = evaluator.get_dashboard(session, now=now)["runtime"]

    harnesses = {row["harness"]: row for row in runtime["harnesses"]}
    assert runtime["online"] is True
    assert runtime["status"] == expected_status
    assert runtime["active_run_count"] == active_run_count
    assert runtime["instance_count"] == 2
    assert runtime["last_heartbeat_at"] == now.isoformat()
    assert harnesses["codex"]["authenticated"] is True
    assert harnesses["codex"]["status"] == expected_status
    assert harnesses["codex"]["last_error"] is None
    assert harnesses["muse"]["authenticated"] is False
    assert harnesses["muse"]["status"] == "authentication_required"
    assert harnesses["muse"]["last_error"] == "Run muse login."


def test_runtime_preserves_latest_stale_details_per_harness_and_excludes_stale_counts():
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    stale_at = now - timedelta(seconds=evaluator.INSTANCE_STALE_SECONDS + 1)
    with session_factory()() as session:
        _heartbeat(
            session,
            harness="muse",
            instance_id="old-muse",
            harness_version="old-version",
            now=stale_at - timedelta(minutes=1),
        )
        _heartbeat(
            session,
            harness="muse",
            status="running",
            active_run_count=9,
            last_error="Connection lost.",
            harness_version="latest-muse-version",
            now=stale_at,
        )
        _heartbeat(session, harness="codex", active_run_count=1, status="running", now=now)
        _heartbeat(
            session,
            harness="codex",
            instance_id="second-codex",
            active_run_count=2,
            status="running",
            now=now,
        )
        runtime = evaluator.get_dashboard(session, now=now)["runtime"]
        offline_runtime = evaluator.get_dashboard(
            session,
            now=now + timedelta(seconds=evaluator.INSTANCE_STALE_SECONDS + 1),
        )["runtime"]

    harnesses = {row["harness"]: row for row in runtime["harnesses"]}
    assert runtime["status"] == "running"
    assert runtime["active_run_count"] == 3
    assert runtime["instance_count"] == 2
    assert harnesses["codex"]["instance_count"] == 2
    assert harnesses["codex"]["active_run_count"] == 3
    muse = harnesses["muse"]
    assert muse["online"] is False
    assert muse["status"] == "offline"
    assert muse["authenticated"] is False
    assert muse["active_run_count"] == muse["instance_count"] == 0
    assert muse["harness_version"] == "latest-muse-version"
    assert muse["last_error"] == "Connection lost."
    assert muse["last_heartbeat_at"] == stale_at.isoformat()
    assert offline_runtime["online"] is False
    assert offline_runtime["status"] == "offline"
    assert offline_runtime["active_run_count"] == offline_runtime["instance_count"] == 0
    assert offline_runtime["last_heartbeat_at"] == now.isoformat()


def test_global_pause_applies_to_online_harnesses_and_preserves_missing_workers():
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        _heartbeat(session, harness="codex", status="running", active_run_count=1, now=now)
        evaluator.update_settings(session, enabled=False)
        runtime = evaluator.get_dashboard(session, now=now)["runtime"]

    harnesses = {row["harness"]: row for row in runtime["harnesses"]}
    assert runtime["status"] == "paused"
    assert runtime["active_run_count"] == 1
    assert harnesses["codex"]["status"] == "paused"
    assert harnesses["muse"]["status"] == "offline"


def test_claims_match_harness_and_share_global_concurrency(sample_agent, sample_prompt):
    now = datetime(2026, 7, 20, 13, tzinfo=UTC)
    with session_factory()() as session:
        muse_model = admin_ops.create_model(
            session,
            name="Muse test model",
            capabilities=[
                {
                    "harness": "muse",
                    "execution_model_id": "muse-test-model",
                    "reasoning_efforts": ["high"],
                }
            ],
        )
        muse_agent = admin_ops.create_agent(
            session,
            model_id=muse_model["id"],
            harness="muse",
            reasoning_effort="high",
        )
        portfolios = [
            admin_ops.create_portfolio(
                session,
                name=name,
                agent_id=agent_id,
                prompt_id=sample_prompt["id"],
                prompt_mode="managed",
                direction="long",
            )
            for name, agent_id in [
                ("Muse first in queue", muse_agent["id"]),
                ("Codex first", sample_agent["id"]),
                ("Codex second", sample_agent["id"]),
            ]
        ]
        for portfolio in portfolios:
            evaluator.update_portfolio_config(
                session,
                portfolio_id=portfolio["id"],
                enabled=True,
                weekdays=[],
            )
        evaluator.update_settings(session, max_concurrency=2)
        queued = evaluator.enqueue_manual_runs(
            session,
            portfolio_ids=[portfolio["id"] for portfolio in portfolios],
            now=now,
        )
        assert [item["action"] for item in queued["items"]] == ["queued"] * 3

        def claim(harness, limit):
            return evaluator.claim_runs(
                session,
                worker_id=f"{harness}-worker",
                harness=harness,
                harness_version=f"{harness}-test-version",
                limit=limit,
                now=now,
            )["runs"]

        first_codex = claim("codex", 1)
        assert [run["portfolio"]["id"] for run in first_codex] == [portfolios[1]["id"]]
        muse = claim("muse", 20)
        assert [run["portfolio"]["id"] for run in muse] == [portfolios[0]["id"]]
        assert muse[0]["harness"] == "muse"
        assert muse[0]["execution_model_id"] == "muse-test-model"
        assert muse[0]["reasoning_effort"] == "high"
        assert muse[0]["harness_version"] == "muse-test-version"
        assert claim("codex", 20) == []

        evaluator.fail_run(session, run_id=muse[0]["id"], error="Cancelled.", cancelled=True, now=now)
        second_codex = claim("codex", 20)
        assert [run["portfolio"]["id"] for run in second_codex] == [portfolios[2]["id"]]
