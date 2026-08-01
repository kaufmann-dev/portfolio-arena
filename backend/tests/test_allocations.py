"""Allocation entry rules: validation, no-backdating, lock enforcement."""

from datetime import UTC, datetime

from .util import backdate_allocation


def make_allocation_body(positions=None, **extra):
    return {
        "positions": positions
        or [
            {"symbol": "AAPL", "weight_pct": 60},
            {"symbol": "MSFT", "weight_pct": 40},
        ],
        **extra,
    }


class TestCreation:
    def test_portfolio_created_without_allocation(self, client, admin_headers, sample_agent, sample_prompt):
        created = client.post(
            "/api/portfolios",
            json={
                "name": "Empty Weekly",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
                "prompt_mode": "managed",
                "direction": "long",
            },
            headers=admin_headers,
        )
        assert created.status_code == 201, created.text
        portfolio = created.json()
        assert "allocation" not in portfolio

        # It shows up in the managed arena with no track record yet.
        rows = client.get("/api/arena/managed?direction=long").json()["portfolios"]
        row = next(p for p in rows if p["id"] == portfolio["id"])
        assert row["allocation_count"] == 0
        assert row["metrics"]["has_data"] is False

        # The first allocation is entered separately (Allocations tab).
        first = client.post(
            f"/api/portfolios/{portfolio['id']}/allocations",
            json=make_allocation_body(),
            headers=admin_headers,
        )
        assert first.status_code == 201, first.text
        assert not first.json()["locked"]

    def test_portfolio_with_first_allocation(self, sample_portfolio):
        allocation = sample_portfolio["allocation"]
        assert allocation["effective_date"] >= datetime.now(UTC).date().isoformat()
        assert not allocation["locked"]
        assert {position["symbol"] for position in allocation["positions"]} == {"AAPL", "MSFT"}
        assert all("instrument" not in position for position in allocation["positions"])

    def test_default_cost_bps_from_settings(self, sample_portfolio):
        assert sample_portfolio["cost_bps"] == 10

    def test_weights_must_sum_to_100(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "AAPL", "weight_pct": 99}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "sum to exactly 100" in response.json()["detail"]

    def test_duplicate_symbols_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(
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
            json=make_allocation_body([{"symbol": "^GSPC", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "ETF" in response.json()["detail"]

    def test_fx_pair_rejected_with_hint(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "EURUSD=X", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "USD-denominated" in response.json()["detail"]

    def test_futures_rejected_with_hint(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "GC=F", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "roll artifacts" in response.json()["detail"]

    def test_crypto_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "BTC-USD", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_unknown_symbol_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "NOPE", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "not found" in response.json()["detail"]

    def test_cash_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "CASH:USD", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "not supported" in response.json()["detail"]

    def test_non_usd_equity_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "SAP.DE", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "USD-denominated" in response.json()["detail"]

    def test_mutual_fund_rejected(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body([{"symbol": "VFIAX", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "equities and ETFs" in response.json()["detail"]

    def test_managed_mode_position_limits_enforced(self, client, admin_headers, sample_agent):
        settings = client.get("/api/settings", headers=admin_headers).json()
        settings["managed_allocation_policy"] = {
            "min_position_weight_pct": 40,
            "max_position_weight_pct": 60,
        }
        updated = client.put("/api/settings", json=settings, headers=admin_headers)
        assert updated.status_code == 200, updated.text
        prompt = client.post(
            "/api/admin/prompts",
            json={
                "name": "Concentrated",
                "mode": "managed",
                "direction": "long",
                "managed_text": "Own a focused portfolio.",
            },
            headers=admin_headers,
        ).json()
        portfolio = client.post(
            "/api/portfolios",
            json={
                "name": "Policy Test",
                "agent_id": sample_agent["id"],
                "prompt_id": prompt["id"],
                "prompt_mode": "managed",
                "direction": "long",
            },
            headers=admin_headers,
        ).json()
        response = client.post(
            f"/api/portfolios/{portfolio['id']}/allocations",
            json=make_allocation_body(
                [
                    {"symbol": "AAPL", "weight_pct": 70},
                    {"symbol": "MSFT", "weight_pct": 30},
                ]
            ),
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert "between 40% and 60%" in response.json()["detail"]

    def test_default_mode_policies_allow_concentrated_rebuilt_but_focus_managed(
        self,
        client,
        admin_headers,
        sample_agent,
    ):
        prompt_response = client.post(
            "/api/admin/prompts",
            json={
                "name": "Mode Policy Test",
                "mode": "both",
                "direction": "long",
                "managed_text": "Build a focused managed portfolio.",
                "rebuilt_text": "Select the strongest independent signal.",
            },
            headers=admin_headers,
        )
        assert prompt_response.status_code == 201, prompt_response.text
        prompt = prompt_response.json()

        def create_portfolio(name: str, prompt_mode: str) -> dict:
            response = client.post(
                "/api/portfolios",
                json={
                    "name": name,
                    "agent_id": sample_agent["id"],
                    "prompt_id": prompt["id"],
                    "prompt_mode": prompt_mode,
                    "direction": "long",
                },
                headers=admin_headers,
            )
            assert response.status_code == 201, response.text
            return response.json()

        managed = create_portfolio("Managed Mode Policy", "managed")
        rebuilt = create_portfolio("Rebuilt Mode Policy", "rebuilt")

        concentrated_managed = client.post(
            f"/api/portfolios/{managed['id']}/allocations",
            json=make_allocation_body([{"symbol": "AAPL", "weight_pct": 100}]),
            headers=admin_headers,
        )
        assert concentrated_managed.status_code == 422
        assert "between 10% and 25%" in concentrated_managed.json()["detail"]

        focused_managed = client.post(
            f"/api/portfolios/{managed['id']}/allocations",
            json=make_allocation_body(
                [
                    {"symbol": "AAPL", "weight_pct": 25},
                    {"symbol": "MSFT", "weight_pct": 25},
                    {"symbol": "SPY", "weight_pct": 25},
                    {"symbol": "RSP", "weight_pct": 25},
                ]
            ),
            headers=admin_headers,
        )
        assert focused_managed.status_code == 201, focused_managed.text

        concentrated_signal = client.post(
            f"/api/portfolios/{rebuilt['id']}/signals",
            json={"positions": [{"symbol": "AAPL", "weight_pct": 100}]},
            headers=admin_headers,
        )
        assert concentrated_signal.status_code == 201, concentrated_signal.text

    def test_same_effective_date_conflicts(self, client, admin_headers, sample_portfolio, sample_prompt):
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/allocations",
            json=make_allocation_body(),
            headers=admin_headers,
        )
        assert response.status_code == 409
        assert "edit it instead" in response.json()["detail"]

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
            json=make_allocation_body(),
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
                    {"symbol": "AAPL", "weight_pct": 30},
                ]
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["positions"][0]["symbol"] == "MSFT"

    def test_unlocked_allocation_deletable(self, client, admin_headers, sample_portfolio):
        allocation_id = sample_portfolio["allocation"]["id"]
        assert client.delete(f"/api/allocations/{allocation_id}", headers=admin_headers).status_code == 200

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

    def test_delete_last_pending_allocation_clears_managed_history(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        response = client.delete(
            f"/api/allocations/{sample_portfolio['allocation']['id']}",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

        rows = client.get("/api/arena/managed?direction=long").json()["portfolios"]
        portfolio = next(row for row in rows if row["id"] == sample_portfolio["id"])
        spy = next(row for row in rows if row["kind"] == "benchmark")
        assert portfolio["allocation_count"] == 0
        assert portfolio["metrics"]["has_data"] is False
        assert spy["id"] is None

    def test_locked_metadata_still_editable(self, client, admin_headers, sample_portfolio):
        allocation_id = sample_portfolio["allocation"]["id"]
        backdate_allocation(allocation_id)

        response = client.put(
            f"/api/allocations/{allocation_id}",
            json={"note": "regime call: risk-on"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["note"] == "regime call: risk-on"
        assert payload["locked"] is True


class TestSymbolEndpoint:
    def test_resolution(self, client, admin_headers):
        response = client.get("/api/symbols/AAPL", headers=admin_headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["security_type"] == "equity"
        assert payload["name"] == "Apple Inc."

    def test_cash_rejected(self, client, admin_headers):
        response = client.get("/api/symbols/CASH:EUR", headers=admin_headers)
        assert response.status_code == 422

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
