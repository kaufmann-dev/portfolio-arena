"""Hardcode the benchmark identity and strategy.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

BENCHMARK_PROMPT_SLUG = "buy-and-hold"
BENCHMARK_AGENT_SLUG = "benchmark"
BENCHMARK_MODEL_SLUG = "benchmark"


def upgrade() -> None:
    connection = op.get_bind()
    contestant_prompt_users = connection.scalar(
        sa.text(
            "SELECT count(*) FROM portfolios p JOIN prompts pr ON pr.id = p.prompt_id "
            "WHERE NOT p.is_benchmark AND pr.slug = :slug"
        ),
        {"slug": BENCHMARK_PROMPT_SLUG},
    )
    if contestant_prompt_users:
        raise RuntimeError(
            "Cannot remove the benchmark prompt while a contestant portfolio uses it; "
            "reassign that portfolio first."
        )

    benchmark_agent_id = connection.scalar(
        sa.text("SELECT id FROM agents WHERE slug = :slug"),
        {"slug": BENCHMARK_AGENT_SLUG},
    )
    benchmark_model_id = None
    if benchmark_agent_id is not None:
        benchmark_model_id = connection.scalar(
            sa.text("SELECT model_id FROM agents WHERE id = :agent_id"),
            {"agent_id": benchmark_agent_id},
        )
        contestant_agent_users = connection.scalar(
            sa.text("SELECT count(*) FROM portfolios WHERE NOT is_benchmark AND agent_id = :agent_id"),
            {"agent_id": benchmark_agent_id},
        )
        agent_run_users = connection.scalar(
            sa.text("SELECT count(*) FROM evaluation_runs WHERE agent_id = :agent_id"),
            {"agent_id": benchmark_agent_id},
        )
        if contestant_agent_users or agent_run_users:
            raise RuntimeError(
                "Cannot remove the benchmark agent while a contestant portfolio or evaluation run "
                "uses it; reassign or remove those references first."
            )

    if benchmark_model_id is not None:
        other_model_agents = connection.scalar(
            sa.text("SELECT count(*) FROM agents WHERE model_id = :model_id AND id != :agent_id"),
            {"model_id": benchmark_model_id, "agent_id": benchmark_agent_id},
        )
        model_run_users = connection.scalar(
            sa.text("SELECT count(*) FROM evaluation_runs WHERE model_id = :model_id"),
            {"model_id": benchmark_model_id},
        )
        if other_model_agents or model_run_users:
            raise RuntimeError(
                "Cannot remove the benchmark model while another agent or evaluation run uses it; "
                "reassign or remove those references first."
            )

    op.alter_column("portfolios", "agent_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("portfolios", "prompt_id", existing_type=sa.Integer(), nullable=True)
    connection.execute(sa.text("UPDATE portfolios SET agent_id = NULL, prompt_id = NULL WHERE is_benchmark"))
    if benchmark_agent_id is not None:
        connection.execute(
            sa.text("DELETE FROM agents WHERE id = :agent_id"),
            {"agent_id": benchmark_agent_id},
        )
    if benchmark_model_id is not None:
        connection.execute(
            sa.text("DELETE FROM model_definitions WHERE id = :model_id"),
            {"model_id": benchmark_model_id},
        )
    connection.execute(
        sa.text("DELETE FROM prompts WHERE slug = :slug"),
        {"slug": BENCHMARK_PROMPT_SLUG},
    )
    op.create_check_constraint(
        "portfolios_identity_assignment_check",
        "portfolios",
        "(is_benchmark AND agent_id IS NULL AND prompt_id IS NULL) OR "
        "(NOT is_benchmark AND agent_id IS NOT NULL AND prompt_id IS NOT NULL)",
    )


def downgrade() -> None:
    connection = op.get_bind()
    model_id = connection.scalar(
        sa.text(
            "INSERT INTO model_definitions (slug, name, notes) "
            "VALUES (:slug, 'Benchmark', 'System benchmark model.') "
            "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id"
        ),
        {"slug": BENCHMARK_MODEL_SLUG},
    )
    agent_id = connection.scalar(
        sa.text(
            "INSERT INTO agents (slug, model_id, harness, reasoning_effort, notes) "
            "VALUES (:slug, :model_id, NULL, NULL, 'System benchmark identity.') "
            "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id"
        ),
        {"slug": BENCHMARK_AGENT_SLUG, "model_id": model_id},
    )
    prompt_id = connection.scalar(
        sa.text(
            "INSERT INTO prompts "
            "(slug, name, text, notes, min_position_weight_pct, max_position_weight_pct) "
            "VALUES (:slug, 'Buy & Hold', 'Hold the benchmark ETF forever.', "
            "'System prompt for benchmark portfolios.', 100, 100) "
            "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id"
        ),
        {"slug": BENCHMARK_PROMPT_SLUG},
    )
    op.drop_constraint(
        "portfolios_identity_assignment_check",
        "portfolios",
        type_="check",
    )
    connection.execute(
        sa.text("UPDATE portfolios SET agent_id = :agent_id, prompt_id = :prompt_id WHERE is_benchmark"),
        {"agent_id": agent_id, "prompt_id": prompt_id},
    )
    op.alter_column("portfolios", "agent_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("portfolios", "prompt_id", existing_type=sa.Integer(), nullable=False)
