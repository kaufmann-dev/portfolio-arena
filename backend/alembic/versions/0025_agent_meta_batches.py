"""Scope daily synthesis batches to individual agents.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meta_batches", sa.Column("agent_id", sa.Integer(), nullable=True))
    op.drop_constraint("meta_batches_session_date_key", "meta_batches", type_="unique")
    connection = op.get_bind()
    metadata = sa.MetaData()
    batches = sa.Table("meta_batches", metadata, autoload_with=connection)
    runs = sa.Table("evaluation_runs", metadata, autoload_with=connection)
    portfolios = sa.Table("portfolios", metadata, autoload_with=connection)
    live_agents = dict(connection.execute(sa.select(portfolios.c.id, portfolios.c.agent_id)).all())

    for batch in connection.execute(sa.select(batches).order_by(batches.c.id)).mappings().all():
        linked_runs = (
            connection.execute(
                sa.select(runs.c.id, runs.c.portfolio_id, runs.c.agent_id)
                .where(runs.c.meta_batch_id == batch["id"])
                .order_by(runs.c.id)
            )
            .mappings()
            .all()
        )
        # Prefer the original evaluation identity to later live reassignments.
        owners = dict(live_agents)
        owners.update({run["portfolio_id"]: run["agent_id"] for run in linked_runs})
        snapshot = batch["snapshot"] or {}
        for source in snapshot.get("sources", []):
            if source.get("agent") is not None:
                owners[source["portfolio"]["id"]] = source["agent"]["id"]
        agent_ids = {run["agent_id"] for run in linked_runs}
        for field in ("source_portfolio_ids", "target_portfolio_ids"):
            agent_ids.update(owners[pid] for pid in batch[field] if pid in owners)
        if not agent_ids:
            # An empty orphan has neither evidence identities nor any run audit.
            connection.execute(batches.delete().where(batches.c.id == batch["id"]))
            continue
        for index, agent_id in enumerate(sorted(agent_ids)):
            values = {key: value for key, value in batch.items() if key != "id"}
            values["agent_id"] = agent_id
            for field in (
                "source_portfolio_ids",
                "due_source_portfolio_ids",
                "target_portfolio_ids",
                "pending_target_portfolio_ids",
            ):
                values[field] = [pid for pid in batch[field] if owners.get(pid) == agent_id]
            # Preserve frozen snapshot bytes and their hash: already queued runs
            # must receive exactly the same evidence after deployment. Execution
            # packets select this run's agent from the stored evidence.
            if index == 0:
                batch_id = batch["id"]
                connection.execute(batches.update().where(batches.c.id == batch_id).values(**values))
            else:
                batch_id = connection.execute(
                    batches.insert().values(**values).returning(batches.c.id)
                ).scalar_one()
            run_ids = [run["id"] for run in linked_runs if run["agent_id"] == agent_id]
            if run_ids:
                connection.execute(runs.update().where(runs.c.id.in_(run_ids)).values(meta_batch_id=batch_id))

    op.alter_column("meta_batches", "agent_id", nullable=False)
    op.create_unique_constraint(
        "meta_batches_session_agent_key", "meta_batches", ["session_date", "agent_id"]
    )


def downgrade() -> None:
    raise RuntimeError("Agent batches cannot be merged without changing independently frozen evidence.")
