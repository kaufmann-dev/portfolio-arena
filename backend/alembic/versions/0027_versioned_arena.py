"""Group experiments by version and remove retired comparison features.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Prompt revision FKs are deferred; drain their events before altering the
    # prompt tables and keep deletion checks immediate throughout this upgrade.
    connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    duplicates = connection.execute(
        sa.text("""
        SELECT model_id, coalesce(harness, ''), coalesce(reasoning_effort, '')
        FROM agents GROUP BY 1, 2, 3 HAVING count(*) > 1
    """)
    ).all()
    if duplicates:
        raise RuntimeError("Resolve duplicate agent execution profiles before upgrading: " + str(duplicates))

    # Source runs survive: only runs owned by synthesis portfolios cascade away.
    connection.execute(
        sa.text("""
        DELETE FROM portfolios WHERE meta_set_id IS NOT NULL
        OR prompt_id IN (SELECT id FROM prompts WHERE context_scope = 'arena')
    """)
    )
    connection.execute(sa.text("UPDATE prompts SET current_version_id = NULL WHERE context_scope = 'arena'"))
    connection.execute(sa.text("DELETE FROM prompts WHERE context_scope = 'arena'"))
    op.drop_column("evaluation_runs", "meta_batch_id")
    op.drop_column("portfolios", "meta_set_id")
    op.drop_table("meta_batches")
    op.drop_table("meta_portfolio_sets")

    op.create_table(
        "arena_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("evaluation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    v1 = connection.execute(
        sa.text("INSERT INTO arena_versions (name, evaluation_enabled) VALUES ('v1', false) RETURNING id")
    ).scalar_one()
    op.add_column("portfolios", sa.Column("version_id", sa.Integer(), nullable=True))
    connection.execute(sa.text("UPDATE portfolios SET version_id = :version"), {"version": v1})
    newer_ids = (
        connection.execute(
            sa.text("""
        SELECT p.id FROM portfolios p JOIN agents a ON p.agent_id = a.id
        JOIN model_definitions m ON a.model_id = m.id WHERE m.slug <> 'gpt-5-6-sol'
    """)
        )
        .scalars()
        .all()
    )
    if newer_ids:
        v2 = connection.execute(
            sa.text("INSERT INTO arena_versions (name, evaluation_enabled) VALUES ('v2', true) RETURNING id")
        ).scalar_one()
        connection.execute(
            sa.text("UPDATE portfolios SET version_id = :version WHERE id = ANY(:ids)"),
            {"version": v2, "ids": newer_ids},
        )
    op.alter_column("portfolios", "version_id", nullable=False)
    op.create_foreign_key(
        "portfolios_version_id_fkey", "portfolios", "arena_versions", ["version_id"], ["id"]
    )
    op.create_index("idx_portfolios_version_id", "portfolios", ["version_id"])

    op.drop_index("agents_execution_profile_key", table_name="agents")
    for table in ("agents", "prompts"):
        op.drop_constraint(f"{table}_status_check", table, type_="check")
        op.drop_constraint(f"{table}_archive_state_check", table, type_="check")
        op.drop_column(table, "status")
        op.drop_column(table, "archived_at")
    op.create_index(
        "agents_execution_profile_key",
        "agents",
        ["model_id", sa.text("coalesce(harness, '')"), sa.text("coalesce(reasoning_effort, '')")],
        unique=True,
    )
    op.drop_constraint("portfolios_status_check", "portfolios", type_="check")
    op.drop_column("portfolios", "status")
    op.drop_constraint("prompts_context_scope_check", "prompts", type_="check")
    op.drop_column("prompts", "context_scope")
    op.drop_constraint("portfolios_cost_bps_check", "portfolios", type_="check")
    op.drop_column("portfolios", "cost_bps")
    op.drop_column("portfolios", "founding_v2")
    connection.execute(sa.text("DELETE FROM settings WHERE key = 'default_cost_bps'"))

    connection.execute(
        sa.text(
            "UPDATE settings SET value = replace(value, :old, :new) WHERE key = 'managed_wrapper_prompt'"
        ),
        {"old": "prospective excess return after transaction\ncosts.", "new": "prospective excess return."},
    )
    # Non-reasoning harness models have no reasoning setting to snapshot.
    op.alter_column("evaluation_runs", "reasoning_effort", nullable=True, server_default=None)

    for table in ("portfolios", "evaluation_runs"):
        op.add_column(
            table, sa.Column("execution_boundary", sa.Text(), nullable=False, server_default="close")
        )
        op.create_check_constraint(
            f"{table}_execution_boundary_check", table, "execution_boundary IN ('open', 'close')"
        )
    op.add_column(
        "portfolios", sa.Column("execution_locked", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    connection.execute(
        sa.text("""
        UPDATE portfolios p SET execution_locked = true
        WHERE EXISTS (SELECT 1 FROM allocations a WHERE a.portfolio_id = p.id)
        OR EXISTS (SELECT 1 FROM signals s WHERE s.portfolio_id = p.id)
        OR EXISTS (SELECT 1 FROM evaluation_runs r WHERE r.portfolio_id = p.id AND r.status = 'succeeded')
    """)
    )
    op.add_column("evaluation_runs", sa.Column("prompt_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "evaluation_runs_prompt_version_id_fkey",
        "evaluation_runs",
        "prompt_versions",
        ["prompt_version_id"],
        ["id"],
    )
    op.add_column(
        "evaluator_settings",
        sa.Column("queue_before_open_minutes", sa.Integer(), nullable=False, server_default="90"),
    )
    connection.execute(
        sa.text("UPDATE evaluator_settings SET queue_before_open_minutes = queue_before_close_minutes")
    )
    op.create_check_constraint(
        "evaluator_settings_open_queue_check",
        "evaluator_settings",
        "queue_before_open_minutes BETWEEN 15 AND 240",
    )
    # A close-only cache cannot establish opening prices. Rebuild through Massive.
    connection.execute(sa.text("DELETE FROM price_cache"))


def downgrade() -> None:
    raise RuntimeError("This migration deletes retired Meta data; restore the pre-upgrade database backup.")
