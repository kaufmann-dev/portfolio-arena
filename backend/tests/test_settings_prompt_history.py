"""Independent Settings prompt revisions, imports, and atomic restoration."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select, text

from app.db import session_factory
from app.models import Setting, SettingPromptVersion
from app.seed import SETTINGS_PROMPT_DEFAULTS, seed_settings
from app.services import admin_ops, evaluator
from app.settings_prompt_baseline import SETTINGS_PROMPT_BASELINE

from .test_mcp import _call_tool

KEYS = tuple(SETTINGS_PROMPT_DEFAULTS)


def history(client, headers, key):
    response = client.get(f"/api/admin/settings/prompts/{key}/versions", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def restore(client, headers, key, version):
    return client.post(f"/api/admin/settings/prompts/{key}/versions/{version}/restore", headers=headers)


def settings(client, headers):
    return client.get("/api/settings", headers=headers).json()


@pytest.mark.parametrize("key", KEYS)
def test_each_prompt_imports_before_today_and_current_then_saves_and_restores(client, admin_headers, key):
    original = settings(client, admin_headers)
    initial = history(client, admin_headers, key)
    assert initial["current_version"] == 2
    assert initial["versions"][0]["text"] == original[key]
    assert initial["versions"][1]["text"] == SETTINGS_PROMPT_BASELINE[key]
    assert all(row["created_at"] and row["restored_from_version"] is None for row in initial["versions"])

    changed = {**original, key: original[key] + "\nAdditional research instruction."}
    response = client.put("/api/settings", json=changed, headers=admin_headers)
    assert response.status_code == 200, response.text
    edited = history(client, admin_headers, key)
    assert edited["current_version"] == 3
    assert edited["versions"][1:] == initial["versions"]
    assert edited["versions"][0]["text"] == changed[key]
    for other in KEYS:
        if other != key:
            assert history(client, admin_headers, other)["current_version"] == 2

    response = restore(client, admin_headers, key, 1)
    assert response.status_code == 200, response.text
    restored = response.json()
    assert restored["current_version"] == 4
    assert restored["versions"][0]["restored_from_version"] == 1
    assert restored["versions"][0]["text"] == SETTINGS_PROMPT_BASELINE[key]
    assert restored["versions"][1:] == edited["versions"]
    assert settings(client, admin_headers) == {**original, key: SETTINGS_PROMPT_BASELINE[key]}


def test_unchanged_and_numeric_only_saves_do_not_create_prompt_revisions(client, admin_headers):
    original = settings(client, admin_headers)
    for payload in (
        original,
        {
            **original,
            "managed_allocation_policy": {"min_position_weight_pct": 5, "max_position_weight_pct": 20},
        },
    ):
        response = client.put("/api/settings", json=payload, headers=admin_headers)
        assert response.status_code == 200, response.text
    for key in KEYS:
        assert history(client, admin_headers, key)["current_version"] == 2


def test_validation_errors_are_atomic_for_values_and_history(client, admin_headers):
    original = settings(client, admin_headers)
    for key in KEYS:
        invalid = {**original, "long_direction_instructions": "Valid changed text", key: " "}
        response = client.put("/api/settings", json=invalid, headers=admin_headers)
        assert response.status_code == 422, response.text
        assert settings(client, admin_headers) == original
    for key in KEYS:
        assert history(client, admin_headers, key)["current_version"] == 2


def test_restore_validates_snapshot_and_rejects_unknown_keys_and_revisions(client, admin_headers):
    original = settings(client, admin_headers)
    key = "managed_wrapper_prompt"
    with session_factory()() as session:
        session.add(SettingPromptVersion(key=key, version=3, text="Invalid legacy wrapper"))
        session.commit()
    assert restore(client, admin_headers, key, 3).status_code == 422
    assert settings(client, admin_headers) == original
    assert history(client, admin_headers, key)["current_version"] == 3
    assert restore(client, admin_headers, key, 999).status_code == 404
    for unknown in ("unknown", "managed_min_position_weight_pct"):
        assert (
            client.get(f"/api/admin/settings/prompts/{unknown}/versions", headers=admin_headers).status_code
            == 404
        )
        assert restore(client, admin_headers, unknown, 1).status_code == 404


def test_history_and_restore_require_browser_session(client, mcp_headers):
    client.cookies.clear()
    key = KEYS[0]
    for headers in ({}, mcp_headers, {"Authorization": "Bearer test-internal-worker-token"}):
        assert client.get(f"/api/admin/settings/prompts/{key}/versions", headers=headers).status_code == 401
        assert restore(client, headers, key, 1).status_code == 401


def test_mcp_edits_are_versioned_and_repeated_seed_preserves_history(client, mcp_headers, admin_headers):
    payload = _call_tool(client, mcp_headers, "get_settings")
    payload["long_direction_instructions"] = "Custom long instruction through MCP."
    _call_tool(client, mcp_headers, "update_settings", payload)
    before = history(client, admin_headers, "long_direction_instructions")
    assert before["current_version"] == 3
    with session_factory()() as session:
        seed_settings(session)
        seed_settings(session)
    assert history(client, admin_headers, "long_direction_instructions") == before
    assert settings(client, admin_headers) == payload


def test_seed_initializes_missing_history_from_saved_values_and_missing_settings_from_defaults(client):
    key = "long_direction_instructions"
    missing = "short_direction_instructions"
    with session_factory()() as session:
        session.execute(delete(SettingPromptVersion))
        session.execute(delete(Setting).where(Setting.key == missing))
        session.get(Setting, key).value = "Preserve my custom saved instruction."
        session.commit()
        seed_settings(session)
        seed_settings(session)
        rows = session.scalars(select(SettingPromptVersion).where(SettingPromptVersion.key == key)).all()
        assert {r.version: r.text for r in rows} == {
            1: SETTINGS_PROMPT_BASELINE[key],
            2: "Preserve my custom saved instruction.",
        }
        assert session.get(Setting, missing).value == SETTINGS_PROMPT_DEFAULTS[missing]
        assert len(session.scalars(select(SettingPromptVersion)).all()) == 16


def test_concurrent_saves_and_restore_keep_sequential_complete_history(client, admin_headers):
    original = settings(client, admin_headers)
    key = "long_direction_instructions"
    barrier = Barrier(3)

    def write(value):
        with session_factory()() as session:
            barrier.wait(timeout=10)
            if value is None:
                admin_ops.restore_setting_prompt_version(session, key, 1)
            else:
                admin_ops.update_app_settings(session, **{**original, key: value})

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(write, value) for value in ("Concurrent A", "Concurrent B", None)]
        for future in futures:
            future.result(timeout=20)
    result = history(client, admin_headers, key)
    assert [row["version"] for row in result["versions"]] == [5, 4, 3, 2, 1]
    assert {row["text"] for row in result["versions"][:3]} == {
        "Concurrent A",
        "Concurrent B",
        SETTINGS_PROMPT_BASELINE[key],
    }
    assert settings(client, admin_headers)[key] == result["versions"][0]["text"]
    assert sum(row["restored_from_version"] == 1 for row in result["versions"]) == 1


def test_restore_updates_manual_preview_and_worker_claim_text(
    client, admin_headers, sample_portfolio, monkeypatch
):
    key = "managed_wrapper_prompt"
    response = restore(client, admin_headers, key, 1)
    assert response.status_code == 200
    from app.models import Portfolio

    with session_factory()() as session:
        portfolio = session.get(Portfolio, sample_portfolio["id"])
        preview = admin_ops.preview_execution_prompt(session, portfolio.id, automated=False)
        assert "Every\nholding must re-earn its place" in preview["execution_prompt"]
        monkeypatch.setattr(evaluator, "run_out", lambda run: {})
        run = SimpleNamespace(portfolio=portfolio, scheduled_for=None, execution_boundary="close")
        claimed = evaluator._claimed_run_out(session, run)
        assert "Every\nholding must re-earn its place" in claimed["execution_prompt"]


def test_migration_imports_git_baseline_and_preserves_custom_active_text(client):
    path = Path(__file__).parents[1] / "alembic/versions/0030_settings_prompt_history.py"
    spec = importlib.util.spec_from_file_location("settings_history_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.BASELINE == SETTINGS_PROMPT_BASELINE
    assert migration.DEFAULTS == SETTINGS_PROMPT_DEFAULTS
    with session_factory()() as session:
        connection = session.connection()
        connection.execute(text("CREATE SCHEMA settings_history_migration_test"))
        connection.execute(text("SET LOCAL search_path TO settings_history_migration_test"))
        connection.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"))
        connection.execute(
            text("INSERT INTO settings VALUES ('long_direction_instructions', 'Custom saved text')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            rows = connection.execute(text("SELECT key, version, text FROM setting_prompt_versions")).all()
            assert len(rows) == 16
            snapshots = {(key, version): value for key, version, value in rows}
            for key in KEYS:
                assert snapshots[key, 1] == SETTINGS_PROMPT_BASELINE[key]
                assert snapshots[key, 2] == (
                    "Custom saved text"
                    if key == "long_direction_instructions"
                    else SETTINGS_PROMPT_DEFAULTS[key]
                )
            migration.downgrade()
        assert (
            connection.scalar(text("SELECT value FROM settings WHERE key = 'long_direction_instructions'"))
            == "Custom saved text"
        )
        session.rollback()
