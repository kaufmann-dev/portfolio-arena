"""Allocation entry rules: validation, no-backdating, lock enforcement."""
from datetime import UTC, datetime

from .util import backdate_allocation


def make_allocation_body(prompt_id, positions=None, **extra):
    return {
        "prompt_id": prompt_id,
        "positions": positions
        or [
            {"symbol": "AAPL", "weight_pct": 60},
            {"symbol": "CASH:USD", "weight_pct": 40},
        ],
        **extra,
    }


class TestCreation:
    def test_portfolio_with_first_allocation(self, sample_portfolio):
        allocation = sample_portfolio["allocation"]
        assert allocation["effective_date"] >= datetime.now(UTC).date().isoformat()
        assert not allocation["locked"]
        assert allocation["positions"][0]["instrument"] == "equity"
        assert allocation["positions"][1]["instrument"] == "cash"

    def test_default_cost_bps_from_settings(self, sample_portfolio):
        assert sample_portfolio["cost_bps"] == 10

    def test_weights_must_sum_to_100(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(
                sample_prompt["id"], [{"symbol": "AAPL", "weight_pct": 99}]
            ),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "sum to exactly 100" in response.json()["detail"]

    def test_duplicate_symbols_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(
                sample_prompt["id"],
                [
                    {"symbol": "AAPL", "weight_pct": 50},
                    {"symbol": "aapl", "weight_pct": 50},
                ],
            ),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "Duplicate" in response.json()["detail"]

    def test_index_symbol_rejected_with_hint(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"], [{"symbol": "^GSPC", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "ETF" in response.json()["detail"]

    def test_fx_pair_rejected_with_hint(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(
                sample_prompt["id"], [{"symbol": "EURUSD=X", "weight_pct": 100}]
            ),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "CASH:" in response.json()["detail"]

    def test_futures_rejected_with_hint(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"], [{"symbol": "GC=F", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "roll artifacts" in response.json()["detail"]

    def test_crypto_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"], [{"symbol": "BTC-USD", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_unknown_symbol_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"], [{"symbol": "NOPE", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "not found" in response.json()["detail"]

    def test_foreign_cash_resolves_via_fx_pair(self, client, admin_headers, sample_portfolio, sample_prompt):
        backdate_allocation(sample_portfolio["allocation"]["id"])
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(
                sample_prompt["id"],
                [
                    {"symbol": "MSFT", "weight_pct": 50},
                    {"symbol": "CASH:EUR", "weight_pct": 50},
                ],
            ),
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text

    def test_unknown_cash_currency_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"], [{"symbol": "CASH:ZZZ", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "FX rate" in response.json()["detail"]

    def test_same_effective_date_conflicts(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"]),
            headers=admin_headers,
        )
        assert response.status_code == 409
        assert "edit it instead" in response.json()["detail"]

    def test_missing_prompt_rejected(self, client, admin_headers, sample_portfolio):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(99999),
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_archived_portfolio_rejects_allocations(
        self, client, admin_headers, sample_portfolio, sample_prompt
    ):
        backdate_allocation(sample_portfolio["allocation"]["id"])
        client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"status": "archived"},
            headers=admin_headers,
        )
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(sample_prompt["id"]),
            headers=admin_headers,
        )
        assert response.status_code == 409


class TestLockEnforcement:
    def test_unlocked_positions_editable(self, client, admin_headers, sample_portfolio):
        allocation_id = sample_portfolio["allocation"]["id"]
        response = client.put(
            f"/api/allocations/{allocation_id}",
            json={
                "positions": [
                    {"symbol": "MSFT", "weight_pct": 70},
                    {"symbol": "CASH:USD", "weight_pct": 30},
                ]
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["positions"][0]["symbol"] == "MSFT"

    def test_unlocked_allocation_deletable(self, client, admin_headers, sample_portfolio):
        allocation_id = sample_portfolio["allocation"]["id"]
        assert (
            client.delete(f"/api/allocations/{allocation_id}", headers=admin_headers).status_code
            == 200
        )

    def test_locked_positions_frozen(self, client, admin_headers, sample_portfolio):
        allocation_id = sample_portfolio["allocation"]["id"]
        backdate_allocation(allocation_id)
        response = client.put(
            f"/api/allocations/{allocation_id}",
            json={"positions": [{"symbol": "MSFT", "weight_pct": 100}]},
            headers=admin_headers,
        )
        assert response.status_code == 403
        assert "frozen" in response.json()["detail"]

    def test_locked_allocation_not_deletable(self, client, admin_headers, sample_portfolio):
        allocation_id = sample_portfolio["allocation"]["id"]
        backdate_allocation(allocation_id)
        response = client.delete(f"/api/allocations/{allocation_id}", headers=admin_headers)
        assert response.status_code == 403

    def test_locked_metadata_still_editable(
        self, client, admin_headers, sample_portfolio, sample_prompt
    ):
        allocation_id = sample_portfolio["allocation"]["id"]
        backdate_allocation(allocation_id)

        second_prompt = client.post(
            "/api/prompts",
            json={"name": "weekly-manager-v2", "text": "Be bolder."},
            headers=admin_headers,
        ).json()

        response = client.put(
            f"/api/allocations/{allocation_id}",
            json={
                "prompt_id": second_prompt["id"],
                "note": "regime call: risk-on",
                "raw_response": "full model output",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["prompt"]["id"] == second_prompt["id"]
        assert payload["note"] == "regime call: risk-on"
        assert payload["locked"] is True

    def test_benchmark_allocations_untouchable(self, client, admin_headers, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"])
        client.get("/api/leaderboard")  # triggers benchmark allocation seeding

        from sqlalchemy import select

        from app.db import session_factory
        from app.models import Allocation, Portfolio

        with session_factory()() as session:
            benchmark_allocation = session.scalars(
                select(Allocation)
                .join(Portfolio)
                .where(Portfolio.is_benchmark.is_(True))
            ).first()
        assert benchmark_allocation is not None

        response = client.put(
            f"/api/allocations/{benchmark_allocation.id}",
            json={"note": "tamper"},
            headers=admin_headers,
        )
        assert response.status_code == 403
        assert (
            client.delete(
                f"/api/allocations/{benchmark_allocation.id}", headers=admin_headers
            ).status_code
            == 403
        )


class TestSymbolEndpoint:
    def test_resolution(self, client, admin_headers):
        response = client.get("/api/symbols/AAPL", headers=admin_headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["instrument"] == "equity"
        assert payload["name"] == "Apple Inc."

    def test_cash_resolution(self, client, admin_headers):
        response = client.get("/api/symbols/CASH:EUR", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["instrument"] == "cash"

    def test_rejection_carries_hint(self, client, admin_headers):
        response = client.get("/api/symbols/^GSPC", headers=admin_headers)
        assert response.status_code == 422
        assert "ETF" in response.json()["detail"]

    def test_requires_admin(self, client):
        assert client.get("/api/symbols/AAPL").status_code == 401

    def test_effective_date_preview(self, client, admin_headers):
        response = client.get("/api/effective-date", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["effective_date"] >= datetime.now(UTC).date().isoformat()
