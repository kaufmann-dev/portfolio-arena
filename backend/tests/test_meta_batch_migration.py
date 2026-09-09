"""Exercise populated Agent-to-harness batch consolidation on PostgreSQL."""

from datetime import UTC, datetime
from pathlib import Path
from runpy import run_path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from app.services import admin_ops, evaluator
from app.services.meta import public_batches
from app.services.meta_synthesis import snapshot_hash, source_counts, source_packet_for
from tests.test_meta_synthesis import _create_meta_set, _muse_agent


@pytest.mark.parametrize("status", ["waiting", "ready", "mixed"])
def test_upgrade_merges_same_harness_and_preserves_runs_and_source_evidence(
    client, admin_headers, sample_agent, sample_model, sample_prompt, status
):
    from app.db import get_engine, session_factory
    from app.models import EvaluationRun, MetaBatch, Portfolio, PortfolioEvaluatorConfig

    family = _create_meta_set(client, admin_headers, sample_agent)
    now = datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
    fixtures = []
    with session_factory()() as session:
        other_agent = admin_ops.create_agent(
            session, model_id=sample_model["id"], harness="codex", reasoning_effort="high"
        )
        muse = _muse_agent(session)
        for index, agent in enumerate((sample_agent, other_agent, muse)):
            harness = "muse" if index == 2 else "codex"
            fixture_status = ("waiting" if index == 1 else "ready") if status == "mixed" else status
            meta = (
                family
                if index == 0
                else admin_ops.create_meta_portfolio_set(
                    session,
                    family_name=f"Other Confluence {index}",
                    agent_id=agent["id"],
                    prompt_id=family["prompt_id"],
                )
            )
            source = Portfolio(
                name=f"Source {index}",
                slug=f"source-{index}",
                agent_id=agent["id"],
                prompt_id=sample_prompt["id"],
                prompt_mode="managed",
                direction="long",
                cost_bps=10,
            )
            session.add(source)
            session.commit()
            evaluator.update_portfolio_config(
                session, portfolio_id=source.id, enabled=True, weekdays=[0, 1, 2, 3, 4]
            )
            targets = [p["id"] for p in meta["portfolios"]]
            run_ids = []
            for pid in [source.id, *targets]:
                run = evaluator._new_run(
                    session.get(PortfolioEvaluatorConfig, pid),
                    evaluator.get_settings(session),
                    trigger_kind="scheduled",
                    scheduled_for=now.date(),
                )
                run.status = "succeeded" if fixture_status == "ready" else "queued"
                session.add(run)
                session.flush()
                run_ids.append(run.id)
            evidence = {
                "portfolio": {"id": source.id, "mode": "managed", "direction": "long"},
                "agent": {"id": agent["id"]},
                "due": True,
                "run_status": "succeeded",
                "decision_status": "same_session",
                "note": f"private evidence {index}",
                "positions": [{"symbol": "AAPL" if index == 0 else "MSFT", "weight_pct": 100}],
            }
            frozen = (
                {
                    "schema_version": 1,
                    "session_date": now.date().isoformat(),
                    "created_at": now.isoformat(),
                    "sources": [evidence],
                    "counts": source_counts([evidence]),
                }
                if fixture_status == "ready"
                else None
            )
            fixtures.append(
                {
                    "agent_id": agent["id"],
                    "harness": harness,
                    "source_id": source.id,
                    "targets": targets,
                    "run_ids": run_ids,
                    "snapshot": frozen,
                    "status": fixture_status,
                }
            )
        # Live assignments must not override the execution harness saved on runs.
        session.get(Portfolio, fixtures[0]["source_id"]).agent_id = muse["id"]
        session.commit()

    migration = run_path(str(Path(__file__).parents[1] / "alembic/versions/0026_harness_meta_batches.py"))
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                sa.text("ALTER TABLE meta_batches DROP CONSTRAINT meta_batches_session_harness_key")
            )
            connection.execute(sa.text("ALTER TABLE meta_batches DROP COLUMN harness"))
            connection.execute(sa.text("ALTER TABLE meta_batches ADD COLUMN agent_id INTEGER NOT NULL"))
            connection.execute(
                sa.text(
                    "ALTER TABLE meta_batches ADD CONSTRAINT meta_batches_session_agent_key "
                    "UNIQUE (session_date, agent_id)"
                )
            )
            old_batches = sa.Table("meta_batches", sa.MetaData(), autoload_with=connection)
            for fixture in fixtures:
                frozen = fixture["snapshot"]
                batch_id = connection.execute(
                    old_batches.insert()
                    .values(
                        agent_id=fixture["agent_id"],
                        session_date=now.date(),
                        status=fixture["status"],
                        source_portfolio_ids=[fixture["source_id"]],
                        due_source_portfolio_ids=[fixture["source_id"]],
                        target_portfolio_ids=fixture["targets"],
                        pending_target_portfolio_ids=[fixture["targets"][0]],
                        snapshot=frozen,
                        snapshot_sha256=snapshot_hash(frozen) if frozen else None,
                        sources_finished_at=now if frozen else None,
                    )
                    .returning(old_batches.c.id)
                ).scalar_one()
                connection.execute(
                    sa.update(EvaluationRun)
                    .where(EvaluationRun.id.in_(fixture["run_ids"]))
                    .values(meta_batch_id=batch_id)
                )
            before = (
                connection.execute(sa.select(EvaluationRun.__table__).order_by(EvaluationRun.id))
                .mappings()
                .all()
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration["upgrade"]()
            batches = connection.execute(sa.select(MetaBatch.__table__)).mappings().all()
            assert len(batches) == 2
            by_id = {batch["id"]: batch for batch in batches}
            for batch in batches:
                cohort = [fixture for fixture in fixtures if fixture["harness"] == batch["harness"]]
                expected_ids = sorted(fixture["source_id"] for fixture in cohort)
                assert batch["source_portfolio_ids"] == batch["due_source_portfolio_ids"] == expected_ids
                assert batch["target_portfolio_ids"] == sorted(
                    pid for fixture in cohort for pid in fixture["targets"]
                )
                assert batch["pending_target_portfolio_ids"] == sorted(
                    fixture["targets"][0] for fixture in cohort
                )
                expected_status = "waiting" if any(f["status"] == "waiting" for f in cohort) else "ready"
                assert batch["status"] == expected_status
                if expected_status == "ready":
                    frozen = batch["snapshot"]
                    assert snapshot_hash(frozen) == batch["snapshot_sha256"]
                    assert [
                        {k: v for k, v in source.items() if k != "harness"} for source in frozen["sources"]
                    ] == [fixture["snapshot"]["sources"][0] for fixture in cohort]
                    packet = source_packet_for(
                        frozen, harness=batch["harness"], mode="managed", direction="long"
                    )
                    assert [source["portfolio"]["id"] for source in packet["sources"]] == expected_ids
                    assert packet["controls"]["managed_long"]["contributor_count"] == len(cohort)
                else:
                    assert batch["snapshot"] is None
            after = (
                connection.execute(sa.select(EvaluationRun.__table__).order_by(EvaluationRun.id))
                .mappings()
                .all()
            )
            assert len(before) == len(after)
            for old, new in zip(before, after, strict=True):
                assert {k: v for k, v in old.items() if k != "meta_batch_id"} == {
                    k: v for k, v in new.items() if k != "meta_batch_id"
                }
                assert by_id[new["meta_batch_id"]]["harness"] == new["harness"]
            with Session(bind=connection) as session:
                summaries = {row["harness"]: row for row in public_batches(session)}
                assert summaries["codex"]["source_count"] == 2
                assert summaries["muse"]["source_count"] == 1
        finally:
            transaction.rollback()
