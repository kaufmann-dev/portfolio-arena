"""Public reads: leaderboard, portfolio detail, compare, prompts, agents,
benchmark auto-seeding."""

from .util import backdate_allocation


def find(rows, slug):
    return next((row for row in rows if row["slug"] == slug), None)


class TestLeaderboard:
    def test_empty_arena(self, client):
        payload = client.get("/api/leaderboard").json()
        # Benchmarks exist but have no allocations until a real portfolio does.
        slugs = {row["slug"] for row in payload["portfolios"]}
        assert {"spy-buy-and-hold", "rsp-buy-and-hold"} <= slugs
        assert all(not row["metrics"]["has_data"] for row in payload["portfolios"])

    def test_portfolio_with_history(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/leaderboard").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row is not None
        assert row["metrics"]["has_data"]
        assert row["metrics"]["itd_return"] is not None
        assert row["metrics"]["vs_spy"] is not None
        assert row["too_early"] is True  # 45 days < 6 months
        assert len(row["sparkline"]) > 5
        assert row["prompt"]["slug"] == "weekly-manager-v1"

    def test_benchmarks_seeded_at_same_inception(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/leaderboard").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        spy = find(payload["portfolios"], "spy-buy-and-hold")
        assert spy["is_benchmark"] is True
        assert spy["metrics"]["has_data"]
        assert spy["inception"] == row["inception"]
        # Benchmarks are cost-free: SPY tracks itself exactly.
        assert spy["metrics"]["vs_spy"] == 0 or abs(spy["metrics"]["vs_spy"]) < 1e-9
        assert spy["cost_bps"] == 0

    def test_pending_allocation_has_no_data(self, client, sample_portfolio):
        payload = client.get("/api/leaderboard").json()
        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row["metrics"]["has_data"] is False


class TestPortfolioDetail:
    def test_detail_shape(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get(f"/api/portfolios/{sample_portfolio['slug']}").json()

        portfolio = payload["portfolio"]
        assert portfolio["series"], "expected a NAV series"
        assert portfolio["spy_series"][0]["nav"] == 100.0
        assert portfolio["series"][0]["date"] == portfolio["spy_series"][0]["date"]

        symbols = {holding["symbol"] for holding in portfolio["holdings"]}
        assert symbols == {"AAPL", "MSFT"}

        assert portfolio["prompt"]["slug"] == "weekly-manager-v1"
        assert portfolio["execution_prompt"].startswith("Evaluate and rebalance")

        allocation = portfolio["allocations"][0]
        assert allocation["locked"] is True
        assert allocation["applied_date"] is not None
        assert allocation["cost"] is not None

    def test_404(self, client):
        assert client.get("/api/portfolios/nope").status_code == 404


class TestCompare:
    def test_overlay_rebased_to_common_start(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        response = client.get(f"/api/compare?slugs={sample_portfolio['slug']},spy-buy-and-hold")
        payload = response.json()
        assert len(payload["series"]) == 2
        for entry in payload["series"]:
            assert entry["series"][0]["nav"] == 100.0
            assert entry["series"][0]["date"] == payload["start"]

    def test_bad_params(self, client):
        assert client.get("/api/compare?slugs=").status_code == 422


class TestPromptsAndAgents:
    def test_benchmark_identity_and_strategy_are_fully_hardcoded(self, client, admin_headers):
        prompts = client.get("/api/prompts").json()["prompts"]
        assert all(prompt["slug"] != "buy-and-hold" for prompt in prompts)
        assert client.get("/api/prompts/buy-and-hold").status_code == 404
        assert client.get("/api/agents/benchmark").status_code == 404
        assert all(agent["slug"] != "benchmark" for agent in client.get("/api/agents").json()["agents"])
        assert all(
            model["slug"] != "benchmark"
            for model in client.get("/api/models", headers=admin_headers).json()["models"]
        )

        benchmark = find(client.get("/api/leaderboard").json()["portfolios"], "spy-buy-and-hold")
        assert benchmark["agent"] == {
            "id": None,
            "slug": "benchmark",
            "name": "Benchmark",
            "model": None,
            "harness": None,
            "execution_model_id": None,
            "reasoning_effort": None,
        }
        assert benchmark["prompt"] == {
            "id": None,
            "slug": "buy-and-hold",
            "name": "Buy & Hold",
            "configurable": False,
            "allocation_policy": {
                "min_position_weight_pct": 100,
                "max_position_weight_pct": 100,
                "derived_min_positions": 1,
                "derived_max_positions": 1,
            },
        }

    def test_buy_and_hold_prompt_name_is_reserved(self, client, admin_headers):
        response = client.post(
            "/api/prompts",
            json={
                "name": "Buy & Hold",
                "text": "Configurable copy.",
                "allocation_policy": {
                    "min_position_weight_pct": 100,
                    "max_position_weight_pct": 100,
                },
            },
            headers=admin_headers,
        )
        assert response.status_code == 409

    def test_benchmark_model_and_agent_slugs_are_reserved(self, client, admin_headers):
        model_response = client.post(
            "/api/models",
            json={"name": "Benchmark", "capabilities": []},
            headers=admin_headers,
        )
        assert model_response.status_code == 409

        model = client.post(
            "/api/models",
            json={"name": "Manual model", "capabilities": []},
            headers=admin_headers,
        ).json()
        agent_response = client.post(
            "/api/agents",
            json={
                "model_id": model["id"],
                "harness": None,
                "reasoning_effort": None,
                "slug": "benchmark",
            },
            headers=admin_headers,
        )
        assert agent_response.status_code == 409

    def test_prompt_detail_lists_portfolios(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/prompts/weekly-manager-v1").json()
        assert payload["prompt"]["text"].startswith("Manage a portfolio")
        assert payload["prompt"]["allocation_policy"]["derived_max_positions"] == 100
        assert find(payload["portfolios"], sample_portfolio["slug"]) is not None

    def test_agent_detail_lists_portfolios(self, client, sample_portfolio, sample_agent):
        payload = client.get(f"/api/agents/{sample_agent['slug']}").json()
        assert find(payload["portfolios"], sample_portfolio["slug"]) is not None
        assert payload["agent"]["name"] == "GPT-5.6 Sol (Codex, Extra high)"
        assert payload["agent"]["model"]["name"] == "GPT-5.6 Sol"
        assert payload["agent"]["harness"] == {"id": "codex", "name": "Codex"}
        assert payload["agent"]["execution_model_id"] == "gpt-5.6-sol"
        assert payload["agent"]["reasoning_effort"] == "xhigh"

    def test_prompt_editing_reflected_publicly(self, client, admin_headers, sample_prompt):
        client.patch(
            f"/api/prompts/{sample_prompt['id']}",
            json={"text": "Updated instructions."},
            headers=admin_headers,
        )
        payload = client.get(f"/api/prompts/{sample_prompt['slug']}").json()
        assert payload["prompt"]["text"] == "Updated instructions."


class TestAdminMisc:
    def test_settings_roundtrip(self, client, admin_headers):
        assert client.get("/api/settings", headers=admin_headers).json() == {"default_cost_bps": 10}
        client.put("/api/settings", json={"default_cost_bps": 25}, headers=admin_headers)
        assert client.get("/api/settings", headers=admin_headers).json() == {"default_cost_bps": 25}

    def test_clear_price_cache(self, client, admin_headers, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"])
        client.get("/api/leaderboard")  # populates the cache
        response = client.delete("/api/prices/cache", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["deleted"] >= 1

    def test_portfolio_rename_and_archive(self, client, admin_headers, sample_portfolio):
        response = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"name": "Renamed", "status": "archived"},
            headers=admin_headers,
        )
        assert response.json()["name"] == "Renamed"
        assert response.json()["status"] == "archived"

    def test_symbol_search(self, client, admin_headers):
        response = client.get("/api/symbols/search?q=AAP", headers=admin_headers)
        assert response.status_code == 200
        assert any(item["symbol"] == "AAPL" for item in response.json()["results"])
