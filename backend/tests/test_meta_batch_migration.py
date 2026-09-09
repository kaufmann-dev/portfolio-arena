"""Exercise the populated shared-batch upgrade on real PostgreSQL."""

from datetime import UTC, datetime
from pathlib import Path
from runpy import run_path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.services import admin_ops, evaluator
from app.services.meta import public_batches
from app.services.meta_synthesis import snapshot_hash, source_counts, source_packet_for
from tests.test_meta_synthesis import _create_meta_set


@pytest.mark.parametrize("status", ["waiting", "ready"])
def test_upgrade_splits_agents_and_preserves_runs_and_frozen_evidence(
    client, admin_headers, sample_agent, sample_model, sample_prompt, sample_portfolio, status
):
    from app.db import get_engine, session_factory
    from app.models import EvaluationRun, MetaBatch, Portfolio, PortfolioEvaluatorConfig

    first_family = _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    with session_factory()() as session:
        other_agent = admin_ops.create_agent(
            session, model_id=sample_model["id"], harness="codex", reasoning_effort="high"
        )
        other_family = admin_ops.create_meta_portfolio_set(
            session,
            family_name="Other Confluence",
            agent_id=other_agent["id"],
            prompt_id=first_family["prompt_id"],
        )
        other_source = Portfolio(
            name="Other source",
            slug="other-source",
            agent_id=other_agent["id"],
            prompt_id=sample_prompt["id"],
            prompt_mode="managed",
            direction="long",
            cost_bps=10,
        )
        session.add(other_source)
        session.commit()
        sources_by_agent = {sample_agent["id"]: sample_portfolio["id"], other_agent["id"]: other_source.id}
        sources = [
            {
                "portfolio": {"id": pid, "mode": "managed", "direction": "long"},
                "agent": {"id": agent_id},
                "due": True,
                "run_status": "succeeded",
                "decision_status": "same_session",
                "note": f"private evidence for agent {agent_id}",
                "positions": [{"symbol": "AAPL", "weight_pct": 100, "note": "private"}],
            }
            for agent_id, pid in sources_by_agent.items()
        ]
        frozen = (
            {
                "schema_version": 1,
                "session_date": now.date().isoformat(),
                "created_at": now.isoformat(),
                "sources": sources,
                "counts": source_counts(sources),
            }
            if status == "ready"
            else None
        )
        targets = [p["id"] for family in (first_family, other_family) for p in family["portfolios"]]
        batch = MetaBatch(
            agent_id=sample_agent["id"],
            session_date=now.date(),
            status=status,
            source_portfolio_ids=list(sources_by_agent.values()),
            due_source_portfolio_ids=list(sources_by_agent.values()),
            target_portfolio_ids=targets,
            pending_target_portfolio_ids=[other_family["portfolios"][0]["id"]],
            snapshot=frozen,
            snapshot_sha256=snapshot_hash(frozen) if frozen else None,
        )
        session.add(batch)
        session.commit()
        for pid in sources_by_agent.values():
            evaluator.update_portfolio_config(
                session, portfolio_id=pid, enabled=True, weekdays=[0, 1, 2, 3, 4]
            )
        settings = evaluator.get_settings(session)
        for pid in [*sources_by_agent.values(), *(targets if status == "ready" else [])]:
            run = evaluator._new_run(
                session.get(PortfolioEvaluatorConfig, pid),
                settings,
                trigger_kind="scheduled",
                scheduled_for=now.date(),
                meta_batch_id=batch.id,
            )
            if pid in sources_by_agent.values():
                run.status = "succeeded" if status == "ready" else "running"
            session.add(run)
        # Migration must use the run/snapshot identity, not this later assignment.
        session.get(Portfolio, sample_portfolio["id"]).agent_id = other_agent["id"]
        session.commit()

    migration = run_path(str(Path(__file__).parents[1] / "alembic/versions/0025_agent_meta_batches.py"))
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            before = (
                connection.execute(select(EvaluationRun.__table__).order_by(EvaluationRun.id))
                .mappings()
                .all()
            )
            # Reconstruct the actual pre-upgrade schema inside a rollback-only transaction.
            connection.execute(
                text("ALTER TABLE meta_batches DROP CONSTRAINT meta_batches_session_agent_key")
            )
            connection.execute(text("ALTER TABLE meta_batches DROP COLUMN agent_id"))
            connection.execute(
                text(
                    "ALTER TABLE meta_batches ADD CONSTRAINT meta_batches_session_date_key "
                    "UNIQUE (session_date)"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration["upgrade"]()

            batches = connection.execute(select(MetaBatch.__table__)).mappings().all()
            assert len(batches) == 2
            by_id = {row["id"]: row for row in batches}
            for row in batches:
                agent_id = row["agent_id"]
                assert row["source_portfolio_ids"] == [sources_by_agent[agent_id]]
                assert row["due_source_portfolio_ids"] == [sources_by_agent[agent_id]]
                assert row["status"] == status
                assert row["snapshot"] == frozen
                assert row["snapshot_sha256"] == (snapshot_hash(frozen) if frozen else None)
                family = first_family if agent_id == sample_agent["id"] else other_family
                assert row["target_portfolio_ids"] == [p["id"] for p in family["portfolios"]]
                assert row["pending_target_portfolio_ids"] == (
                    [other_family["portfolios"][0]["id"]] if agent_id == other_agent["id"] else []
                )
                if frozen:
                    packet = source_packet_for(
                        row["snapshot"], agent_id=agent_id, mode="managed", direction="long"
                    )
                    assert [source["portfolio"]["id"] for source in packet["sources"]] == [
                        sources_by_agent[agent_id]
                    ]
            after = (
                connection.execute(select(EvaluationRun.__table__).order_by(EvaluationRun.id))
                .mappings()
                .all()
            )
            assert len(after) == len(before)
            for old_run, new_run in zip(before, after, strict=True):
                assert {k: v for k, v in old_run.items() if k != "meta_batch_id"} == {
                    k: v for k, v in new_run.items() if k != "meta_batch_id"
                }
                assert by_id[new_run["meta_batch_id"]]["agent_id"] == new_run["agent_id"]
            with Session(bind=connection) as session:
                summaries = public_batches(session)
                assert len(summaries) == 2
                assert all(row["source_count"] == row["due_count"] == 1 for row in summaries)
                assert all(row["success_count"] == (1 if frozen else 0) for row in summaries)
        finally:
            transaction.rollback()
