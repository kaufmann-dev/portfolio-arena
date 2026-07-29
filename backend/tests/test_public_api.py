"""Public reads: leaderboard, portfolio detail, compare, prompts, agents,
benchmark auto-seeding."""

from datetime import UTC, datetime, timedelta

from app.services.prompt_policy import (
    DEFAULT_MANAGED_WRAPPER_PROMPT,
    DEFAULT_REBUILT_WRAPPER_PROMPT,
)

from .util import backdate_allocation


def find(rows, slug):
    return next((row for row in rows if row["slug"] == slug), None)


class TestLeaderboard:
    def test_empty_arena(self, client):
        payload = client.get("/api/leaderboard").json()
        assert payload["market_data_status"] == "fresh"
        # Benchmarks exist but have no allocations until a real portfolio does.
        slugs = {row["slug"] for row in payload["portfolios"]}
        assert {"spy-buy-and-hold", "rsp-buy-and-hold"} <= slugs
        assert all(not row["metrics"]["has_data"] for row in payload["portfolios"])

    def test_portfolio_with_history(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/leaderboard").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row is not None
        assert row["prompt_mode"] == "managed"
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

    def test_benchmarks_move_to_later_surviving_inception(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        sample_portfolio,
    ):
        later_portfolio = client.post(
            "/api/portfolios",
            json={
                "name": "Later Portfolio",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
                "prompt_mode": "managed",
            },
            headers=admin_headers,
        ).json()
        later_allocation = client.post(
            f"/api/portfolios/{later_portfolio['id']}/allocations",
            json={"positions": [{"symbol": "AAPL", "weight_pct": 100}], "note": "later"},
            headers=admin_headers,
        ).json()
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        later_date = backdate_allocation(later_allocation["id"], days_back=20)

        client.get("/api/leaderboard")
        response = client.delete(
            f"/api/portfolios/{sample_portfolio['id']}",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

        rows = client.get("/api/leaderboard").json()["portfolios"]
        benchmarks = [row for row in rows if row["is_benchmark"]]
        assert len(benchmarks) == 2
        assert all(row["inception"] == later_date.isoformat() for row in benchmarks)
        assert all(row["allocation_count"] == 1 for row in benchmarks)

    def test_archived_history_keeps_benchmark_inception(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        inception = backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        response = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"status": "archived"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

        benchmarks = [
            row for row in client.get("/api/leaderboard").json()["portfolios"] if row["is_benchmark"]
        ]
        assert all(row["inception"] == inception.isoformat() for row in benchmarks)

    def test_benchmark_reconciliation_repairs_duplicates_and_holdings(
        self,
        client,
        sample_portfolio,
    ):
        inception = backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        client.get("/api/leaderboard")

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db import session_factory
        from app.models import Allocation, Portfolio, Position
        from app.services.benchmarks import BENCHMARK_ALLOCATION_NOTE

        with session_factory()() as session:
            spy = session.scalars(
                select(Portfolio)
                .where(Portfolio.slug == "spy-buy-and-hold")
                .options(selectinload(Portfolio.allocations).selectinload(Allocation.positions))
            ).one()
            spy.allocations[0].note = "corrupt"
            spy.allocations[0].positions[0].symbol = "AAPL"
            duplicate = Allocation(
                portfolio_id=spy.id,
                entered_at=datetime.now(UTC) + timedelta(seconds=1),
                effective_date=inception,
                note="duplicate",
            )
            duplicate.positions.append(Position(symbol="SPY", weight_pct=100))
            session.add(duplicate)
            session.commit()

        client.get("/api/leaderboard")

        with session_factory()() as session:
            spy = session.scalars(
                select(Portfolio)
                .where(Portfolio.slug == "spy-buy-and-hold")
                .options(selectinload(Portfolio.allocations).selectinload(Allocation.positions))
            ).one()
            assert len(spy.allocations) == 1
            assert spy.allocations[0].effective_date == inception
            assert spy.allocations[0].note == BENCHMARK_ALLOCATION_NOTE
            assert len(spy.allocations[0].positions) == 1
            assert spy.allocations[0].positions[0].symbol == "SPY"
            assert float(spy.allocations[0].positions[0].weight_pct) == 100

    def test_pending_allocation_has_no_data(self, client, sample_portfolio):
        payload = client.get("/api/leaderboard").json()
        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row["metrics"]["has_data"] is False

    def test_expired_cache_fallback_reports_stale_on_valuation_routes(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_portfolio,
        sample_prompt,
        monkeypatch,
    ):
        from sqlalchemy import update

        from app.db import session_factory
        from app.models import PriceCache
        from app.services import massive

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        assert client.get("/api/leaderboard").json()["market_data_status"] == "fresh"
        with session_factory()() as session:
            session.execute(update(PriceCache).values(fetched_at=datetime.now(UTC) - timedelta(hours=2)))
            session.commit()
        monkeypatch.setattr(
            massive,
            "download_prices",
            lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: None for symbol in symbols}),
        )

        requests = [
            ("/api/leaderboard", {}),
            (f"/api/portfolios/{sample_portfolio['slug']}", {}),
            (f"/api/compare?slugs={sample_portfolio['slug']}", {}),
            (f"/api/prompts/{sample_prompt['slug']}", {}),
            (f"/api/agents/{sample_agent['slug']}", {}),
            (f"/api/portfolios/{sample_portfolio['id']}/detail", admin_headers),
        ]
        for url, headers in requests:
            payload = client.get(url, headers=headers).json()
            assert payload["market_data_status"] == "stale", url
            assert payload["as_of"] is not None, url

    def test_missing_prices_report_unavailable_on_valuation_routes(
        self,
        client,
        admin_headers,
        sample_agent,
        sample_portfolio,
        sample_prompt,
        monkeypatch,
    ):
        from app.services import massive

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        monkeypatch.setattr(
            massive,
            "download_prices",
            lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: None for symbol in symbols}),
        )

        requests = [
            ("/api/leaderboard", {}),
            (f"/api/portfolios/{sample_portfolio['slug']}", {}),
            (f"/api/compare?slugs={sample_portfolio['slug']}", {}),
            (f"/api/prompts/{sample_prompt['slug']}", {}),
            (f"/api/agents/{sample_agent['slug']}", {}),
            (f"/api/portfolios/{sample_portfolio['id']}/detail", admin_headers),
        ]
        for url, headers in requests:
            payload = client.get(url, headers=headers).json()
            assert payload["market_data_status"] == "unavailable", url
            assert payload["as_of"] is None, url

    def test_carried_forward_price_promotes_fresh_load_to_stale(
        self,
        client,
        sample_portfolio,
        monkeypatch,
    ):
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import PriceCache
        from app.services import arena

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        assert client.get("/api/leaderboard").json()["market_data_status"] == "fresh"
        with session_factory()() as session:
            cached = {
                row.symbol: row.series
                for row in session.scalars(select(PriceCache).order_by(PriceCache.symbol)).all()
            }
        aapl = cached["AAPL"]
        missing_index = len(aapl) // 2
        cached["AAPL"] = aapl[:missing_index] + aapl[missing_index + 1 :]
        monkeypatch.setattr(
            arena,
            "load_price_series",
            lambda *_args, **_kwargs: arena.PriceSeriesLoad(series=cached, status="fresh"),
        )

        payload = client.get("/api/leaderboard").json()
        row = find(payload["portfolios"], sample_portfolio["slug"])

        assert payload["market_data_status"] == "stale"
        assert row["stale_data"] is True

    def test_missing_inception_price_promotes_fresh_load_to_unavailable(
        self,
        client,
        sample_portfolio,
        monkeypatch,
    ):
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import PriceCache
        from app.services import arena

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        assert client.get("/api/leaderboard").json()["market_data_status"] == "fresh"
        with session_factory()() as session:
            cached = {
                row.symbol: row.series
                for row in session.scalars(select(PriceCache).order_by(PriceCache.symbol)).all()
            }
        cached["AAPL"] = [cached["AAPL"][-1]]
        monkeypatch.setattr(
            arena,
            "load_price_series",
            lambda *_args, **_kwargs: arena.PriceSeriesLoad(series=cached, status="fresh"),
        )

        payload = client.get("/api/leaderboard").json()
        row = find(payload["portfolios"], sample_portfolio["slug"])

        assert payload["market_data_status"] == "unavailable"
        assert payload["as_of"] is not None
        assert row["metrics"]["has_data"] is False
        assert row["error"]


class TestPortfolioDetail:
    def test_detail_shape(self, client, sample_portfolio, sample_prompt):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get(f"/api/portfolios/{sample_portfolio['slug']}").json()

        portfolio = payload["portfolio"]
        assert portfolio["series"], "expected a NAV series"
        assert portfolio["spy_series"][0]["nav"] == 100.0
        assert portfolio["series"][0]["date"] == portfolio["spy_series"][0]["date"]

        symbols = {holding["symbol"] for holding in portfolio["holdings"]}
        assert symbols == {"AAPL", "MSFT"}

        assert portfolio["prompt"]["slug"] == "weekly-manager-v1"
        assert portfolio["prompt_mode"] == "managed"
        execution_prompt = portfolio["execution_prompt"]
        assert execution_prompt.startswith("Evaluate the Portfolio Arena portfolio")
        assert sample_portfolio["slug"] in execution_prompt
        assert sample_prompt["text"] in execution_prompt
        assert execution_prompt.count("If the returned allocation history is empty") == 1
        assert "construct the portfolio's initial allocation" in execution_prompt
        assert "do not rebuild it from scratch" in execution_prompt
        assert "after\ntransaction costs" in execution_prompt
        assert "prefer retaining the existing allocation" in execution_prompt
        assert "call `create_allocation` exactly once" in execution_prompt
        assert "{{" not in execution_prompt

        allocation = portfolio["allocations"][0]
        assert allocation["locked"] is True
        assert allocation["applied_date"] is not None
        assert allocation["cost"] is not None

    def test_404(self, client):
        assert client.get("/api/portfolios/nope").status_code == 404

    def test_rebuilt_execution_prompt_reconstructs_from_scratch(
        self,
        client,
        admin_headers,
        sample_portfolio,
        sample_prompt,
    ):
        reconstruction_strategy = (
            "At every evaluation, reconstruct the target portfolio independently from scratch. "
            "Do not prefer current holdings or account for turnover or transaction costs."
        )
        response = client.patch(
            f"/api/prompts/{sample_prompt['id']}",
            json={"text": reconstruction_strategy},
            headers=admin_headers,
        )
        assert response.status_code == 200
        response = client.patch(
            f"/api/portfolios/{sample_portfolio['id']}",
            json={"prompt_mode": "rebuilt"},
            headers=admin_headers,
        )
        assert response.status_code == 200

        payload = client.get(f"/api/portfolios/{sample_portfolio['slug']}").json()
        execution_prompt = payload["portfolio"]["execution_prompt"]

        assert payload["portfolio"]["prompt_mode"] == "rebuilt"
        assert reconstruction_strategy in execution_prompt
        assert "rebuild the complete target portfolio independently from scratch" in execution_prompt
        assert "continuity is useful" not in execution_prompt
        assert "prefer retaining the existing allocation" not in execution_prompt

    def test_strategy_placeholders_are_not_expanded(
        self,
        client,
        admin_headers,
        sample_portfolio,
        sample_prompt,
    ):
        strategy = "Keep this literal token in the strategy: {{portfolio_slug}}."
        response = client.patch(
            f"/api/prompts/{sample_prompt['id']}",
            json={"text": strategy},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

        payload = client.get(f"/api/portfolios/{sample_portfolio['slug']}").json()
        execution_prompt = payload["portfolio"]["execution_prompt"]
        assert strategy in execution_prompt
        assert execution_prompt.count("{{portfolio_slug}}") == 1


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
        original = client.get("/api/settings", headers=admin_headers).json()
        assert original == {
            "default_cost_bps": 10,
            "managed_wrapper_prompt": DEFAULT_MANAGED_WRAPPER_PROMPT,
            "rebuilt_wrapper_prompt": DEFAULT_REBUILT_WRAPPER_PROMPT,
        }
        updated_managed = DEFAULT_MANAGED_WRAPPER_PROMPT.replace(
            "produce its next allocation",
            "produce a carefully reviewed next allocation",
        )
        response = client.put(
            "/api/settings",
            json={
                "default_cost_bps": 25,
                "managed_wrapper_prompt": updated_managed,
                "rebuilt_wrapper_prompt": DEFAULT_REBUILT_WRAPPER_PROMPT,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/settings", headers=admin_headers).json() == {
            "default_cost_bps": 25,
            "managed_wrapper_prompt": updated_managed,
            "rebuilt_wrapper_prompt": DEFAULT_REBUILT_WRAPPER_PROMPT,
        }

    def test_settings_reject_invalid_wrapper_placeholders(self, client, admin_headers):
        for invalid in (
            DEFAULT_MANAGED_WRAPPER_PROMPT.replace("{{strategy_text}}", ""),
            DEFAULT_MANAGED_WRAPPER_PROMPT.replace("{{strategy_text}}", "{{unknown}}"),
            f"{DEFAULT_MANAGED_WRAPPER_PROMPT}\n{{{{malformed",
        ):
            response = client.put(
                "/api/settings",
                json={
                    "default_cost_bps": 10,
                    "managed_wrapper_prompt": invalid,
                    "rebuilt_wrapper_prompt": DEFAULT_REBUILT_WRAPPER_PROMPT,
                },
                headers=admin_headers,
            )
            assert response.status_code == 422, response.text

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
