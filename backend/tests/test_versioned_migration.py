"""Verify the destructive version migration against fresh and populated PostgreSQL schemas."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.db import get_engine
from app.models import Base


@pytest.fixture
def migration_database(client):
    """An isolated schema rolled back after each test, including Alembic's version table."""
    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("CREATE SCHEMA versioned_migration_test"))
            connection.execute(sa.text("SET LOCAL search_path TO versioned_migration_test"))
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            config.attributes["connection"] = connection
            yield connection, config
        finally:
            transaction.rollback()


def _assert_schema_matches_models(connection):
    inspector = sa.inspect(connection)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    for name, model in Base.metadata.tables.items():
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        assert set(columns) == set(model.columns.keys()), name
        assert {name: column["nullable"] for name, column in columns.items()} == {
            column.name: column.nullable for column in model.columns
        }, name
        assert {constraint["name"] for constraint in inspector.get_check_constraints(name)} == {
            constraint.name for constraint in model.constraints if isinstance(constraint, sa.CheckConstraint)
        }, name
    profile_index = next(
        index for index in inspector.get_indexes("agents") if index["name"] == "agents_execution_profile_key"
    )
    assert profile_index["unique"]
    assert not profile_index["dialect_options"].get("postgresql_where")


def test_fresh_upgrade_matches_models_and_starts_with_empty_paused_version(migration_database):
    connection, config = migration_database
    command.upgrade(config, "head")
    _assert_schema_matches_models(connection)
    assert connection.execute(sa.text("SELECT name, evaluation_enabled FROM arena_versions")).all() == [
        ("v1", False)
    ]
    assert connection.scalar(sa.text("SELECT count(*) FROM portfolios")) == 0
    assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0029"


def _populate_0026(connection):
    metadata = sa.MetaData()
    metadata.reflect(connection)
    tables = metadata.tables
    now = datetime(2026, 8, 4, 18, tzinfo=UTC)

    def insert(table, **values):
        table = tables[table]
        return connection.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()

    sol_model = insert("model_definitions", slug="gpt-5-6-sol", name="GPT-5.6 Sol")
    newer_model = insert("model_definitions", slug="newer-model", name="Newer model")
    for model_id, harness in ((sol_model, "codex"), (newer_model, "muse")):
        connection.execute(
            tables["model_harness_capabilities"]
            .insert()
            .values(
                model_id=model_id,
                harness=harness,
                execution_model_id="test-model",
                reasoning_efforts=["high"],
            )
        )

    sol_agent = insert(
        "agents",
        slug="sol",
        model_id=sol_model,
        harness="codex",
        reasoning_effort="high",
        status="archived",
        archived_at=now,
    )
    newer_agent = insert(
        "agents", slug="newer", model_id=newer_model, harness="muse", reasoning_effort="high"
    )

    prompt = insert("prompts", slug="ordinary", status="archived", archived_at=now)
    synthesis_prompt = insert("prompts", slug="synthesis", context_scope="arena")
    revisions = []
    for prompt_id in (prompt, synthesis_prompt):
        for revision in (1, 2):
            version = insert(
                "prompt_versions",
                prompt_id=prompt_id,
                version=revision,
                name=f"Strategy {revision}",
                mode="both",
                direction="both",
                managed_long_text="managed long",
                managed_short_text="managed short",
                rebuilt_long_text="rebuilt long",
                rebuilt_short_text="rebuilt short",
            )
            if prompt_id == prompt:
                revisions.append(version)
        connection.execute(
            tables["prompts"]
            .update()
            .where(tables["prompts"].c.id == prompt_id)
            .values(current_version_id=version)
        )

    family = insert(
        "meta_portfolio_sets",
        slug="confluence",
        family_name="Confluence",
        agent_id=newer_agent,
        prompt_id=synthesis_prompt,
    )

    def portfolio(name, agent_id, mode, **values):
        return insert(
            "portfolios",
            name=name,
            slug=name,
            agent_id=agent_id,
            prompt_id=prompt,
            prompt_mode=mode,
            direction="long",
            cost_bps=10,
            **values,
        )

    old_managed = portfolio("old-managed", sol_agent, "managed", status="archived")
    old_rebuilt = portfolio("old-rebuilt", sol_agent, "rebuilt", status="archived")
    new_rebuilt = portfolio("new-rebuilt", newer_agent, "rebuilt", founding_v2=True)
    reset_managed = portfolio("reset-managed", newer_agent, "managed")
    empty_managed = portfolio("empty-managed", newer_agent, "managed")
    meta_ids = [
        insert(
            "portfolios",
            name=f"meta-{mode}",
            slug=f"meta-{mode}",
            agent_id=newer_agent,
            prompt_id=synthesis_prompt,
            prompt_mode=mode,
            direction="long",
            cost_bps=10,
            meta_set_id=family,
        )
        for mode in ("managed", "rebuilt")
    ]
    normal_ids = [old_managed, old_rebuilt, new_rebuilt, reset_managed, empty_managed]
    for portfolio_id in [*normal_ids, *meta_ids]:
        connection.execute(
            tables["portfolio_evaluator_configs"]
            .insert()
            .values(
                portfolio_id=portfolio_id,
                enabled=True,
                weekdays=[0, 1, 2, 3, 4],
            )
        )

    allocation = insert(
        "allocations",
        portfolio_id=old_managed,
        entered_at=now,
        effective_date=date(2026, 8, 4),
        note="preserve ordinary allocation",
    )
    insert("positions", allocation_id=allocation, symbol="AAPL", weight_pct=100, note="preserve note")
    signals = [
        insert(
            "signals",
            portfolio_id=portfolio_id,
            entered_at=now,
            effective_date=date(2026, 8, 4),
            provenance="integrated",
            note="preserve ordinary signal",
        )
        for portfolio_id in (old_rebuilt, new_rebuilt)
    ]
    for signal in signals:
        insert(
            "signal_positions", signal_id=signal, symbol="MSFT", weight_pct=100, note="preserve signal note"
        )
    meta_allocation = insert(
        "allocations", portfolio_id=meta_ids[0], entered_at=now, effective_date=date(2026, 8, 4)
    )
    insert("positions", allocation_id=meta_allocation, symbol="SPY", weight_pct=100)
    meta_signal = insert(
        "signals",
        portfolio_id=meta_ids[1],
        entered_at=now,
        effective_date=date(2026, 8, 4),
        provenance="integrated",
    )
    insert("signal_positions", signal_id=meta_signal, symbol="SPY", weight_pct=100)
    batch = insert(
        "meta_batches",
        session_date=date(2026, 8, 4),
        harness="codex",
        status="ready",
        source_portfolio_ids=normal_ids,
        target_portfolio_ids=meta_ids,
        snapshot={"sources": []},
    )
    runs = []
    for portfolio_id, agent_id, model_id, result in (
        (old_managed, sol_agent, sol_model, {"allocation_id": allocation}),
        (old_rebuilt, sol_agent, sol_model, {"signal_id": signals[0]}),
        (new_rebuilt, newer_agent, newer_model, {"signal_id": signals[1]}),
        (reset_managed, newer_agent, newer_model, {}),
        (meta_ids[0], newer_agent, newer_model, {"allocation_id": meta_allocation}),
        (meta_ids[1], newer_agent, newer_model, {"signal_id": meta_signal}),
    ):
        run = insert(
            "evaluation_runs",
            portfolio_id=portfolio_id,
            agent_id=agent_id,
            model_id=model_id,
            harness="codex" if agent_id == sol_agent else "muse",
            execution_model_id="test-model",
            status="succeeded",
            scheduled_for=date(2026, 8, 4),
            meta_batch_id=batch,
            report="preserved research",
            **result,
        )
        if portfolio_id in normal_ids:
            runs.append(run)
    connection.execute(tables["evaluator_settings"].update().values(queue_before_close_minutes=135))
    connection.execute(
        tables["settings"].insert(),
        [
            {"key": "default_cost_bps", "value": "10"},
            {
                "key": "custom_strategy",
                "value": "Consider transaction costs when reading financial statements.",
            },
        ],
    )
    connection.execute(
        tables["settings"]
        .update()
        .where(tables["settings"].c.key == "managed_wrapper_prompt")
        .values(value="prefix prospective excess return after transaction\ncosts. suffix")
    )
    connection.execute(
        tables["price_cache"]
        .insert()
        .values(
            symbol="SPY",
            series=[{"date": "2026-08-04", "close": 100}],
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
        )
    )
    return {
        "portfolios": normal_ids,
        "v1": {old_managed, old_rebuilt},
        "v2": {new_rebuilt, reset_managed, empty_managed},
        "unlocked": empty_managed,
        "runs": runs,
        "prompt": prompt,
        "revisions": revisions,
        "agent": sol_agent,
        "agent_model": sol_model,
    }


def test_populated_upgrade_preserves_ordinary_history_and_removes_retired_data(migration_database):
    connection, config = migration_database
    command.upgrade(config, "0026")
    fixture = _populate_0026(connection)
    ordinary_tables = (
        "allocations",
        "signals",
        "positions",
        "signal_positions",
        "prompt_versions",
        "evaluation_runs",
    )
    before = {
        table: {
            row["id"]: dict(row) for row in connection.execute(sa.text(f"SELECT * FROM {table}")).mappings()
        }
        for table in ordinary_tables
    }

    command.upgrade(config, "head")
    _assert_schema_matches_models(connection)
    portfolios = (
        connection.execute(
            sa.text(
                "SELECT p.*, v.name AS version_name, v.evaluation_enabled "
                "FROM portfolios p JOIN arena_versions v ON p.version_id = v.id"
            )
        )
        .mappings()
        .all()
    )
    assert {row["id"] for row in portfolios} == set(fixture["portfolios"])
    assert {row["id"] for row in portfolios if row["version_name"] == "v1"} == fixture["v1"]
    assert {row["id"] for row in portfolios if row["version_name"] == "v2"} == fixture["v2"]
    for row in portfolios:
        assert row["evaluation_enabled"] == (row["id"] in fixture["v2"])
        assert row["execution_boundary"] == "close"
        assert row["execution_locked"] == (row["id"] != fixture["unlocked"])
    for table in ordinary_tables:
        after = connection.execute(sa.text(f"SELECT * FROM {table}")).mappings().all()
        for row in after:
            retained = {
                key: value for key, value in before[table][row["id"]].items() if key != "meta_batch_id"
            }
            assert retained == {key: row[key] for key in retained}
        if table == "evaluation_runs":
            assert {row["id"] for row in after} == set(fixture["runs"])
            assert all(
                row["prompt_version_id"] is None and row["execution_boundary"] == "close" for row in after
            )
        if table == "prompt_versions":
            assert {row["id"] for row in after} == set(fixture["revisions"])
    assert connection.scalar(sa.text("SELECT count(*) FROM allocations")) == 1
    assert connection.scalar(sa.text("SELECT count(*) FROM signals")) == 2
    assert connection.scalar(sa.text("SELECT count(*) FROM positions")) == 1
    assert connection.scalar(sa.text("SELECT count(*) FROM signal_positions")) == 2
    assert connection.scalar(sa.text("SELECT count(*) FROM prompts")) == 1
    assert connection.scalar(sa.text("SELECT current_version_id FROM prompts")) == fixture["revisions"][-1]
    assert connection.scalar(sa.text("SELECT count(*) FROM portfolio_evaluator_configs")) == 5
    assert connection.scalar(sa.text("SELECT count(*) FROM price_cache")) == 0
    assert connection.execute(
        sa.text("SELECT queue_before_close_minutes, queue_before_open_minutes FROM evaluator_settings")
    ).one() == (135, 135)
    settings = dict(connection.execute(sa.text("SELECT key, value FROM settings")).all())
    assert "default_cost_bps" not in settings
    assert settings["managed_wrapper_prompt"] == "prefix prospective excess return. suffix"
    assert settings["custom_strategy"] == "Consider transaction costs when reading financial statements."
    connection.execute(sa.text("UPDATE evaluation_runs SET reasoning_effort = NULL"))
    with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
        connection.execute(
            sa.text(
                "INSERT INTO agents (slug, model_id, harness, reasoning_effort) "
                "VALUES ('duplicate', :model, 'codex', 'high')"
            ),
            {"model": fixture["agent_model"]},
        )
    with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
        connection.execute(sa.text("UPDATE portfolios SET execution_boundary = 'midday'"))
    with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
        connection.execute(sa.text("UPDATE evaluator_settings SET queue_before_open_minutes = 14"))
