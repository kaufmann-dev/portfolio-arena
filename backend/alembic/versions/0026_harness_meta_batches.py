"""Group synthesis evidence by execution harness.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-09
"""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

ID_FIELDS = (
    "source_portfolio_ids",
    "due_source_portfolio_ids",
    "target_portfolio_ids",
    "pending_target_portfolio_ids",
)


def upgrade() -> None:
    op.add_column("meta_batches", sa.Column("harness", sa.Text(), nullable=True))
    op.drop_constraint("meta_batches_session_agent_key", "meta_batches", type_="unique")
    connection = op.get_bind()
    metadata = sa.MetaData()
    batches = sa.Table("meta_batches", metadata, autoload_with=connection)
    runs = sa.Table("evaluation_runs", metadata, autoload_with=connection)
    agents = sa.Table("agents", metadata, autoload_with=connection)
    portfolios = sa.Table("portfolios", metadata, autoload_with=connection)
    agent_harnesses = dict(connection.execute(sa.select(agents.c.id, agents.c.harness)).all())
    live_harnesses = dict(
        connection.execute(
            sa.select(portfolios.c.id, agents.c.harness).join(agents, portfolios.c.agent_id == agents.c.id)
        ).all()
    )
    groups = defaultdict(list)
    old_batches = connection.execute(sa.select(batches).order_by(batches.c.id)).mappings().all()
    for batch in old_batches:
        linked_runs = (
            connection.execute(
                sa.select(runs.c.id, runs.c.portfolio_id, runs.c.harness)
                .where(runs.c.meta_batch_id == batch["id"])
                .order_by(runs.c.id)
            )
            .mappings()
            .all()
        )
        recorded_harnesses = {run["harness"] for run in linked_runs}
        default_harness = agent_harnesses.get(batch["agent_id"])
        if len(recorded_harnesses) == 1:
            default_harness = next(iter(recorded_harnesses))
        owners = dict(live_harnesses)
        if default_harness:
            owners.update({pid: default_harness for field in ID_FIELDS for pid in batch[field]})
        for source in (batch["snapshot"] or {}).get("sources", []):
            identity = source.get("agent") or {}
            recorded = source.get("harness") or agent_harnesses.get(identity.get("id"))
            if recorded:
                owners[source["portfolio"]["id"]] = recorded
        # Run snapshots take precedence over later edits to an Agent's harness.
        owners.update({run["portfolio_id"]: run["harness"] for run in linked_runs})
        harnesses = set(recorded_harnesses)
        harnesses.update(owners[pid] for field in ID_FIELDS for pid in batch[field] if owners.get(pid))
        if not harnesses and default_harness:
            harnesses.add(default_harness)
        if not harnesses:
            raise RuntimeError(f"Cannot identify execution harness for Meta batch {batch['id']}")
        if len(harnesses) == 1:
            default_harness = next(iter(harnesses))
        for harness in sorted(harnesses):
            values = dict(batch)
            for field in ID_FIELDS:
                values[field] = [pid for pid in batch[field] if owners.get(pid, default_harness) == harness]
            snapshot = deepcopy(batch["snapshot"])
            if snapshot is not None:
                snapshot["sources"] = [
                    {**source, "harness": harness}
                    for source in snapshot["sources"]
                    if source["portfolio"]["id"] in values["source_portfolio_ids"]
                ]
            values["snapshot"] = snapshot
            values["run_ids"] = [run["id"] for run in linked_runs if run["harness"] == harness]
            groups[batch["session_date"], harness].append(values)

    for (session_date, harness), members in sorted(groups.items()):
        values = {field: sorted({pid for row in members for pid in row[field]}) for field in ID_FIELDS}
        waiting = any(row["status"] == "waiting" for row in members)
        failed = any(row["status"] == "failed" for row in members)
        snapshots = [row["snapshot"] for row in members if row["snapshot"] is not None]
        snapshot = None
        if snapshots and not waiting:
            sources = {
                source["portfolio"]["id"]: source for frozen in snapshots for source in frozen["sources"]
            }
            snapshot = {
                "schema_version": 1,
                "harness": harness,
                "session_date": session_date.isoformat(),
                "created_at": max(frozen["created_at"] for frozen in snapshots),
                "sources": [sources[pid] for pid in sorted(sources)],
            }
            due = [source for source in snapshot["sources"] if source["due"]]
            snapshot["counts"] = {
                "source_total": len(sources),
                "due_total": len(due),
                "terminal_total": sum(
                    source["run_status"]
                    in {
                        "cancelled",
                        "succeeded",
                        "failed",
                        "skipped",
                        "not_scheduled",
                        "source_deleted",
                        "source_reassigned",
                    }
                    for source in due
                ),
                "succeeded_total": sum(source["run_status"] == "succeeded" for source in due),
                "fallback_total": sum(source["decision_status"] == "fallback" for source in sources.values()),
                "missing_total": sum(source["decision_status"] == "missing" for source in sources.values()),
            }
        usable = snapshot and snapshot["counts"]["source_total"] > snapshot["counts"]["missing_total"]
        values.update(
            session_date=session_date,
            agent_id=members[0]["agent_id"],  # Removed after consolidation.
            harness=harness,
            status="waiting" if waiting else "failed" if failed else "ready" if usable else "insufficient",
            snapshot=snapshot,
            snapshot_sha256=hashlib.sha256(
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
            if snapshot
            else None,
            error="; ".join(dict.fromkeys(row["error"] for row in members if row["error"])) or None,
            sources_finished_at=None
            if waiting
            else max(
                (row["sources_finished_at"] for row in members if row["sources_finished_at"]), default=None
            ),
            created_at=min(row["created_at"] for row in members),
            updated_at=max(row["updated_at"] for row in members),
        )
        batch_id = connection.execute(batches.insert().values(**values).returning(batches.c.id)).scalar_one()
        run_ids = [run_id for row in members for run_id in row["run_ids"]]
        if run_ids:
            connection.execute(runs.update().where(runs.c.id.in_(run_ids)).values(meta_batch_id=batch_id))
    if old_batches:
        connection.execute(batches.delete().where(batches.c.id.in_([row["id"] for row in old_batches])))
    op.drop_column("meta_batches", "agent_id")
    op.alter_column("meta_batches", "harness", nullable=False)
    op.create_unique_constraint(
        "meta_batches_session_harness_key", "meta_batches", ["session_date", "harness"]
    )

    # Rename the two active operational families along with their harness suites.
    families = sa.Table("meta_portfolio_sets", metadata, autoload_with=connection)
    for previous, current in (("Astra", "Codex"), ("Spark", "Muse")):
        connection.execute(
            families.update()
            .where(families.c.family_name == f"Confluence {previous}")
            .where(
                sa.exists(
                    sa.select(portfolios.c.id).where(
                        portfolios.c.meta_set_id == families.c.id, portfolios.c.status == "active"
                    )
                )
            )
            .values(family_name=f"Confluence {current}")
        )


def downgrade() -> None:
    raise RuntimeError("Harness evidence cannot be split into independent Agent batches.")
