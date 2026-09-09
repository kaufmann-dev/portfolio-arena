"""Muse catalog import preserves provider capabilities and administrator edits."""

import pytest

from app.services.errors import AdminOpError
from app.services.muse_catalog import parse_muse_catalog

INTERNAL_HEADERS = {"Authorization": "Bearer test-internal-worker-token"}
IMPORT_PATH = "/api/internal/evaluator/models/import-muse"


def catalog_row(model_id="muse-spark-test", **metadata):
    return {
        "id": model_id,
        "object": "model",
        "owned_by": "meta",
        "metadata": {
            "muse-code": {
                "name": "Muse Spark Test",
                "is_hidden": False,
                "tool_call": True,
                "variants": {
                    "minimal": {"reasoningEffort": "minimal", "description": "Faster responses"},
                    "high": {"reasoningEffort": "high"},
                },
                **metadata,
            }
        },
    }


def test_muse_catalog_uses_declared_execution_efforts_and_skips_unavailable_models():
    parsed = parse_muse_catalog(
        [
            catalog_row(
                variants={
                    "high": {"reasoningEffort": "minimal"},
                    "minimal": {},
                    "low": None,
                    "medium": "medium",
                    "unsupported-future-tier": {},
                }
            ),
            catalog_row("hidden-model", is_hidden=True),
            catalog_row("no-tools", tool_call=False),
            {"id": "generic-api-model", "metadata": {}},
        ]
    )

    assert len(parsed) == 1
    assert parsed[0].execution_model_id == "muse-spark-test"
    assert parsed[0].reasoning_efforts == ("minimal", "high")


@pytest.mark.parametrize(
    "rows",
    [
        [{"metadata": {"muse-code": {}}}],
        [catalog_row(variants=["low"])],
        [catalog_row(), catalog_row()],
        [catalog_row(is_hidden=True)],
    ],
)
def test_muse_catalog_rejects_malformed_or_empty_catalogs(rows):
    with pytest.raises(AdminOpError):
        parse_muse_catalog(rows)


def test_muse_catalog_import_requires_internal_worker_authentication(client, admin_headers):
    body = {"data": [catalog_row()]}
    assert client.post(IMPORT_PATH, json=body).status_code == 401
    assert client.post(IMPORT_PATH, json=body, headers=admin_headers).status_code == 401


def test_muse_catalog_import_creates_usable_profiles_and_preserves_admin_edits(client, admin_headers):
    body = {"object": "list", "data": [catalog_row()]}
    imported = client.post(IMPORT_PATH, json=body, headers=INTERNAL_HEADERS)

    assert imported.status_code == 200
    assert imported.json() == {"models_seen": 1, "models_added": 1, "capabilities_added": 1}
    models = client.get("/api/models", headers=admin_headers).json()["models"]
    model = next(model for model in models if model["slug"] == "muse-spark-test")
    capability = model["capabilities"][0]
    assert capability == {
        "harness": "muse",
        "harness_name": "Muse Code",
        "execution_model_id": "muse-spark-test",
        "reasoning_efforts": ["minimal", "high"],
    }
    created = client.post(
        "/api/agents",
        json={"model_id": model["id"], "harness": "muse", "reasoning_effort": "high"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Muse Spark Test (Muse Code, High)"
    assert created.json()["execution_model_id"] == "muse-spark-test"
    updated = client.patch(
        f"/api/models/{model['id']}",
        json={
            "name": "Our Meta Model",
            "notes": "Reviewed by admin",
            "capabilities": [{**capability, "reasoning_efforts": ["high"]}],
        },
        headers=admin_headers,
    )
    assert updated.status_code == 200

    repeated = client.post(IMPORT_PATH, json=body, headers=INTERNAL_HEADERS)
    assert repeated.json() == {"models_seen": 1, "models_added": 0, "capabilities_added": 0}
    models = client.get("/api/models", headers=admin_headers).json()["models"]
    kept = next(item for item in models if item["id"] == model["id"])
    assert kept["name"] == "Our Meta Model"
    assert kept["notes"] == "Reviewed by admin"
    assert kept["capabilities"][0]["reasoning_efforts"] == ["high"]


def test_muse_catalog_reuses_model_definition_and_leaves_undeclared_effort_at_default(client, admin_headers):
    model = client.post(
        "/api/models",
        json={"name": "Existing Meta Model", "slug": "muse-spark-test", "capabilities": []},
        headers=admin_headers,
    ).json()
    row = catalog_row()
    del row["metadata"]["muse-code"]["variants"]

    imported = client.post(IMPORT_PATH, json={"data": [row]}, headers=INTERNAL_HEADERS)
    assert imported.json() == {"models_seen": 1, "models_added": 0, "capabilities_added": 1}
    created = client.post(
        "/api/agents",
        json={"model_id": model["id"], "harness": "muse", "reasoning_effort": None},
        headers=admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Existing Meta Model (Muse Code)"
    assert created.json()["reasoning_effort"] is None
