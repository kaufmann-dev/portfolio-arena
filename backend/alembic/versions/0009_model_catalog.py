"""Model catalog and harness-aware agent execution profiles.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-23
"""

import json
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _slugify(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result or "model"


def _unique_slug(connection, table: str, wanted: str) -> str:
    base = _slugify(wanted)
    candidate = base
    suffix = 2
    while connection.scalar(
        sa.text(f"SELECT 1 FROM {table} WHERE slug = :slug"),  # noqa: S608
        {"slug": candidate},
    ):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _create_model(connection, name: str, slug_hint: str) -> int:
    return connection.scalar(
        sa.text("INSERT INTO model_definitions (slug, name) VALUES (:slug, :name) RETURNING id"),
        {"slug": _unique_slug(connection, "model_definitions", slug_hint), "name": name},
    )


def upgrade() -> None:
    op.create_table(
        "model_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "model_harness_capabilities",
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("model_definitions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("harness", sa.Text(), primary_key=True),
        sa.Column("execution_model_id", sa.Text(), nullable=False),
        sa.Column(
            "reasoning_efforts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    connection = op.get_bind()
    global_effort = (
        connection.scalar(sa.text("SELECT reasoning_effort FROM evaluator_settings WHERE id = 1")) or "xhigh"
    )
    execution_ids = connection.scalars(
        sa.text(
            "SELECT model FROM portfolio_evaluator_configs WHERE btrim(model) <> '' "
            "UNION SELECT model FROM evaluation_runs WHERE btrim(model) <> ''"
        )
    ).all()
    model_ids: dict[str, int] = {}
    for execution_id in execution_ids:
        model_id = _create_model(connection, execution_id, execution_id)
        observed = connection.scalars(
            sa.text(
                "SELECT DISTINCT reasoning_effort FROM evaluation_runs "
                "WHERE model = :model AND reasoning_effort IS NOT NULL"
            ),
            {"model": execution_id},
        ).all()
        efforts = list(dict.fromkeys([global_effort, *observed]))
        connection.execute(
            sa.text(
                "INSERT INTO model_harness_capabilities "
                "(model_id, harness, execution_model_id, reasoning_efforts) "
                "VALUES (:model_id, 'codex', :execution_model_id, CAST(:efforts AS jsonb))"
            ),
            {
                "model_id": model_id,
                "execution_model_id": execution_id,
                "efforts": json.dumps(efforts),
            },
        )
        model_ids[execution_id] = model_id

    op.add_column("agents", sa.Column("model_id", sa.Integer(), nullable=True))
    op.add_column("agents", sa.Column("harness", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("reasoning_effort", sa.Text(), nullable=True))

    agents = (
        connection.execute(sa.text("SELECT id, slug, name, notes FROM agents ORDER BY id")).mappings().all()
    )
    for agent in agents:
        configured = connection.scalars(
            sa.text(
                "SELECT DISTINCT pec.model FROM portfolios p "
                "JOIN portfolio_evaluator_configs pec ON pec.portfolio_id = p.id "
                "WHERE p.agent_id = :agent_id AND btrim(pec.model) <> '' ORDER BY pec.model"
            ),
            {"agent_id": agent["id"]},
        ).all()
        configured_portfolio_ids = connection.scalars(
            sa.text(
                "SELECT p.id FROM portfolios p "
                "JOIN portfolio_evaluator_configs pec ON pec.portfolio_id = p.id "
                "WHERE p.agent_id = :agent_id AND btrim(pec.model) <> ''"
            ),
            {"agent_id": agent["id"]},
        ).all()
        total_portfolios = connection.scalar(
            sa.text("SELECT count(*) FROM portfolios WHERE agent_id = :agent_id"),
            {"agent_id": agent["id"]},
        )
        unconfigured_count = int(total_portfolios or 0) - len(configured_portfolio_ids)

        if not configured:
            model_id = _create_model(connection, agent["name"], f"legacy-{agent['slug']}")
            connection.execute(
                sa.text("UPDATE agents SET model_id = :model_id WHERE id = :agent_id"),
                {"model_id": model_id, "agent_id": agent["id"]},
            )
            continue

        reuse_execution_id = configured[0] if unconfigured_count == 0 else None
        if reuse_execution_id is None:
            model_id = _create_model(connection, agent["name"], f"legacy-{agent['slug']}")
            connection.execute(
                sa.text("UPDATE agents SET model_id = :model_id WHERE id = :agent_id"),
                {"model_id": model_id, "agent_id": agent["id"]},
            )
        else:
            connection.execute(
                sa.text(
                    "UPDATE agents SET model_id = :model_id, harness = 'codex', "
                    "reasoning_effort = :effort WHERE id = :agent_id"
                ),
                {
                    "model_id": model_ids[reuse_execution_id],
                    "effort": global_effort,
                    "agent_id": agent["id"],
                },
            )

        for execution_id in configured:
            if execution_id == reuse_execution_id:
                continue
            clone_id = connection.scalar(
                sa.text(
                    "INSERT INTO agents "
                    "(slug, name, model_id, harness, reasoning_effort, notes, created_at) "
                    "VALUES (:slug, :name, :model_id, 'codex', :effort, :notes, now()) RETURNING id"
                ),
                {
                    "slug": _unique_slug(
                        connection,
                        "agents",
                        f"{agent['slug']}-{execution_id}",
                    ),
                    "name": agent["name"],
                    "model_id": model_ids[execution_id],
                    "effort": global_effort,
                    "notes": agent["notes"],
                },
            )
            connection.execute(
                sa.text(
                    "UPDATE portfolios p SET agent_id = :clone_id "
                    "FROM portfolio_evaluator_configs pec "
                    "WHERE pec.portfolio_id = p.id AND p.agent_id = :agent_id "
                    "AND pec.model = :model"
                ),
                {
                    "clone_id": clone_id,
                    "agent_id": agent["id"],
                    "model": execution_id,
                },
            )

    duplicate_profiles = (
        connection.execute(
            sa.text(
                "SELECT model_id, harness, reasoning_effort, min(id) AS keep_id, "
                "array_agg(id ORDER BY id) AS ids "
                "FROM agents GROUP BY model_id, harness, reasoning_effort HAVING count(*) > 1"
            )
        )
        .mappings()
        .all()
    )
    for profile in duplicate_profiles:
        duplicate_ids = profile["ids"][1:]
        connection.execute(
            sa.text("UPDATE portfolios SET agent_id = :keep WHERE agent_id = ANY(:duplicates)"),
            {"keep": profile["keep_id"], "duplicates": duplicate_ids},
        )
        notes = connection.scalars(
            sa.text("SELECT DISTINCT notes FROM agents WHERE id = ANY(:ids) AND btrim(notes) <> ''"),
            {"ids": profile["ids"]},
        ).all()
        connection.execute(
            sa.text("UPDATE agents SET notes = :notes WHERE id = :keep"),
            {"notes": "\n\n".join(notes), "keep": profile["keep_id"]},
        )
        connection.execute(
            sa.text("DELETE FROM agents WHERE id = ANY(:duplicates)"),
            {"duplicates": duplicate_ids},
        )

    historical_profiles = (
        connection.execute(
            sa.text(
                "SELECT DISTINCT er.model, er.reasoning_effort "
                "FROM evaluation_runs er ORDER BY er.model, er.reasoning_effort"
            )
        )
        .mappings()
        .all()
    )
    for profile in historical_profiles:
        model_id = model_ids[profile["model"]]
        existing_agent_id = connection.scalar(
            sa.text(
                "SELECT id FROM agents WHERE model_id = :model_id AND harness = 'codex' "
                "AND reasoning_effort = :effort"
            ),
            {"model_id": model_id, "effort": profile["reasoning_effort"]},
        )
        if existing_agent_id is not None:
            continue
        connection.execute(
            sa.text(
                "INSERT INTO agents "
                "(slug, name, model_id, harness, reasoning_effort, notes, created_at) "
                "VALUES (:slug, :name, :model_id, 'codex', :effort, :notes, now())"
            ),
            {
                "slug": _unique_slug(
                    connection,
                    "agents",
                    f"{profile['model']}-codex-{profile['reasoning_effort']}",
                ),
                "name": profile["model"],
                "model_id": model_id,
                "effort": profile["reasoning_effort"],
                "notes": "Migrated historical evaluator profile.",
            },
        )

    op.create_foreign_key(
        "agents_model_id_fkey",
        "agents",
        "model_definitions",
        ["model_id"],
        ["id"],
    )
    op.create_foreign_key(
        "agents_model_harness_fkey",
        "agents",
        "model_harness_capabilities",
        ["model_id", "harness"],
        ["model_id", "harness"],
    )
    op.alter_column("agents", "model_id", nullable=False)
    op.create_index(
        "agents_execution_profile_key",
        "agents",
        [
            "model_id",
            sa.text("coalesce(harness, '')"),
            sa.text("coalesce(reasoning_effort, '')"),
        ],
        unique=True,
    )

    op.add_column("evaluation_runs", sa.Column("agent_id", sa.Integer(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("model_id", sa.Integer(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("harness", sa.Text(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("execution_model_id", sa.Text(), nullable=True))
    op.add_column("evaluation_runs", sa.Column("harness_version", sa.Text(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE evaluation_runs er SET "
            "agent_id = a.id, "
            "model_id = md.id, "
            "harness = 'codex', "
            "execution_model_id = er.model, "
            "harness_version = er.codex_version "
            "FROM model_harness_capabilities mhc "
            "JOIN model_definitions md ON md.id = mhc.model_id "
            "JOIN agents a ON a.model_id = md.id AND a.harness = mhc.harness "
            "WHERE mhc.harness = 'codex' AND mhc.execution_model_id = er.model "
            "AND a.reasoning_effort = er.reasoning_effort"
        )
    )
    op.alter_column("evaluation_runs", "agent_id", nullable=False)
    op.alter_column("evaluation_runs", "model_id", nullable=False)
    op.alter_column("evaluation_runs", "harness", nullable=False)
    op.alter_column("evaluation_runs", "execution_model_id", nullable=False)
    op.create_foreign_key(
        "evaluation_runs_agent_id_fkey",
        "evaluation_runs",
        "agents",
        ["agent_id"],
        ["id"],
    )
    op.create_foreign_key(
        "evaluation_runs_model_id_fkey",
        "evaluation_runs",
        "model_definitions",
        ["model_id"],
        ["id"],
    )
    op.drop_column("evaluation_runs", "model")
    op.drop_column("evaluation_runs", "service_tier")
    op.drop_column("evaluation_runs", "codex_version")

    op.drop_constraint(
        "evaluator_settings_reasoning_check",
        "evaluator_settings",
        type_="check",
    )
    op.drop_constraint(
        "evaluator_settings_service_tier_check",
        "evaluator_settings",
        type_="check",
    )
    op.drop_column("evaluator_settings", "reasoning_effort")
    op.drop_column("evaluator_settings", "service_tier")
    connection.execute(
        sa.text(
            "UPDATE portfolio_evaluator_configs pec SET enabled = false "
            "FROM portfolios p JOIN agents a ON a.id = p.agent_id "
            "WHERE p.id = pec.portfolio_id AND a.harness IS NULL"
        )
    )
    op.drop_column("portfolio_evaluator_configs", "model")

    op.add_column(
        "evaluator_instances",
        sa.Column("harness", sa.Text(), server_default="codex", nullable=False),
    )
    op.alter_column(
        "evaluator_instances",
        "codex_version",
        new_column_name="harness_version",
    )
    op.alter_column("evaluator_instances", "harness", server_default=None)
    op.drop_column("agents", "name")


def downgrade() -> None:
    op.add_column("agents", sa.Column("name", sa.Text(), nullable=True))
    op.execute(
        "UPDATE agents a SET name = md.name || CASE "
        "WHEN a.harness IS NULL THEN ' (No supported harness)' "
        "WHEN a.reasoning_effort IS NULL THEN ' (' || initcap(a.harness) || ')' "
        "ELSE ' (' || initcap(a.harness) || ', ' || a.reasoning_effort || ')' END "
        "FROM model_definitions md WHERE md.id = a.model_id"
    )
    op.alter_column("agents", "name", nullable=False)

    op.add_column(
        "portfolio_evaluator_configs",
        sa.Column("model", sa.Text(), server_default="", nullable=False),
    )
    op.execute(
        "UPDATE portfolio_evaluator_configs pec SET model = mhc.execution_model_id "
        "FROM portfolios p JOIN agents a ON a.id = p.agent_id "
        "JOIN model_harness_capabilities mhc ON mhc.model_id = a.model_id AND mhc.harness = a.harness "
        "WHERE p.id = pec.portfolio_id"
    )

    op.add_column(
        "evaluator_settings",
        sa.Column("reasoning_effort", sa.Text(), server_default="xhigh", nullable=False),
    )
    op.add_column(
        "evaluator_settings",
        sa.Column("service_tier", sa.Text(), server_default="standard", nullable=False),
    )
    op.create_check_constraint(
        "evaluator_settings_reasoning_check",
        "evaluator_settings",
        "reasoning_effort IN ('low', 'medium', 'high', 'xhigh')",
    )
    op.create_check_constraint(
        "evaluator_settings_service_tier_check",
        "evaluator_settings",
        "service_tier IN ('standard', 'fast')",
    )

    op.add_column("evaluation_runs", sa.Column("model", sa.Text(), nullable=True))
    op.add_column(
        "evaluation_runs",
        sa.Column("service_tier", sa.Text(), server_default="standard", nullable=False),
    )
    op.add_column("evaluation_runs", sa.Column("codex_version", sa.Text(), nullable=True))
    op.execute("UPDATE evaluation_runs SET model = execution_model_id, codex_version = harness_version")
    op.alter_column("evaluation_runs", "model", nullable=False)
    op.drop_constraint("evaluation_runs_model_id_fkey", "evaluation_runs", type_="foreignkey")
    op.drop_constraint("evaluation_runs_agent_id_fkey", "evaluation_runs", type_="foreignkey")
    op.drop_column("evaluation_runs", "harness_version")
    op.drop_column("evaluation_runs", "execution_model_id")
    op.drop_column("evaluation_runs", "harness")
    op.drop_column("evaluation_runs", "model_id")
    op.drop_column("evaluation_runs", "agent_id")

    op.alter_column(
        "evaluator_instances",
        "harness_version",
        new_column_name="codex_version",
    )
    op.drop_column("evaluator_instances", "harness")
    op.drop_index("agents_execution_profile_key", table_name="agents")
    op.drop_constraint("agents_model_harness_fkey", "agents", type_="foreignkey")
    op.drop_constraint("agents_model_id_fkey", "agents", type_="foreignkey")
    op.drop_column("agents", "reasoning_effort")
    op.drop_column("agents", "harness")
    op.drop_column("agents", "model_id")
    op.drop_table("model_harness_capabilities")
    op.drop_table("model_definitions")
