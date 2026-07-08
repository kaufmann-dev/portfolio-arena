"""Per-position notes and the admin handoff view: notes round-trip for admins,
never leak on public payloads, and holdings carry buy/current prices."""

from .util import backdate_allocation


def _create_with_notes(client, admin_headers, agent_id, prompt_id):
    return client.post(
        "/api/portfolios",
        json={
            "name": "Notes Weekly",
            "agent_id": agent_id,
            "allocation": {
                "prompt_id": prompt_id,
                "positions": [
                    {"symbol": "AAPL", "weight_pct": 60, "note": "earnings 08/01, trimming"},
                    {"symbol": "CASH:USD", "weight_pct": 40, "note": "dry powder"},
                ],
                "note": "risk-off tilt",
            },
        },
        headers=admin_headers,
    )


class TestPositionNotes:
    def test_note_round_trips_via_admin_detail(self, client, admin_headers, sample_agent, sample_prompt):
        created = _create_with_notes(client, admin_headers, sample_agent["id"], sample_prompt["id"])
        assert created.status_code == 201, created.text
        # The admin write response echoes the notes it just saved.
        assert created.json()["allocation"]["positions"][0]["note"] == "earnings 08/01, trimming"

        detail = client.get(f"/api/portfolios/{created.json()['id']}/detail", headers=admin_headers)
        assert detail.status_code == 200, detail.text
        positions = detail.json()["portfolio"]["allocations"][0]["positions"]
        notes = {p["symbol"]: p["note"] for p in positions}
        assert notes == {"AAPL": "earnings 08/01, trimming", "CASH:USD": "dry powder"}

    def test_public_detail_hides_notes(self, client, admin_headers, sample_agent, sample_prompt):
        created = _create_with_notes(client, admin_headers, sample_agent["id"], sample_prompt["id"])
        public = client.get(f"/api/portfolios/{created.json()['slug']}")
        assert public.status_code == 200
        for allocation in public.json()["portfolio"]["allocations"]:
            for position in allocation["positions"]:
                assert "note" not in position

    def test_admin_detail_requires_auth(self, client, sample_portfolio):
        assert client.get(f"/api/portfolios/{sample_portfolio['id']}/detail").status_code == 401

    def test_admin_detail_missing_portfolio(self, client, admin_headers):
        assert client.get("/api/portfolios/999999/detail", headers=admin_headers).status_code == 404


class TestHandoffHoldings:
    def test_holdings_carry_prices_and_notes(self, client, admin_headers, sample_agent, sample_prompt):
        created = _create_with_notes(client, admin_headers, sample_agent["id"], sample_prompt["id"])
        backdate_allocation(created.json()["allocation"]["id"])

        detail = client.get(f"/api/portfolios/{created.json()['id']}/detail", headers=admin_headers)
        assert detail.status_code == 200, detail.text
        holdings = {h["symbol"]: h for h in detail.json()["portfolio"]["holdings"]}
        assert holdings, "expected drifted holdings after backdating"
        aapl = holdings["AAPL"]
        assert aapl["entry_price"] and aapl["current_price"]
        assert aapl["note"] == "earnings 08/01, trimming"

    def test_public_holdings_omit_handoff_fields(self, client, admin_headers, sample_agent, sample_prompt):
        created = _create_with_notes(client, admin_headers, sample_agent["id"], sample_prompt["id"])
        backdate_allocation(created.json()["allocation"]["id"])
        public = client.get(f"/api/portfolios/{created.json()['slug']}").json()["portfolio"]
        assert public["holdings"], "expected holdings after backdating"
        for holding in public["holdings"]:
            assert "entry_price" not in holding
            assert "current_price" not in holding
            assert "note" not in holding
