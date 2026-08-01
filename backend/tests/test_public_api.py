"""Public managed-arena reads, detail, compare, prompts, and agents."""

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
        payload = client.get("/api/arena/managed?direction=long").json()
        assert payload["market_data_status"] == "fresh"
        assert payload["portfolios"] == [
            {
                "kind": "benchmark",
                "id": None,
                "slug": "spy",
                "name": "SPY",
                "direction": "long",
                "status": "reference",
                "rank": None,
                "evidence": "pending",
                "rank_score": None,
                "metrics": {
                    "has_data": False,
                    "itd_return": None,
                    "spy_return": None,
                    "cumulative_excess": None,
                    "mean_daily_alpha": None,
                    "ci_lower": None,
                    "ci_upper": None,
                    "evidence": "pending",
                    "liquidated_at": None,
                },
                "sparkline": [],
                "is_liquidated": False,
                "liquidated_at": None,
            }
        ]

    def test_portfolio_with_history(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/arena/managed?direction=long").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        assert row is not None
        assert row["prompt_mode"] == "managed"
        assert row["metrics"]["has_data"]
        assert row["metrics"]["itd_return"] is not None
        assert row["metrics"]["cumulative_excess"] is not None
        assert row["evidence"] in {"pending", "inconclusive", "positive", "negative"}
        assert "too_early" not in row
        assert len(row["sparkline"]) > 5
        assert row["prompt"]["slug"] == "weekly-manager-v1"

    def test_synthetic_spy_uses_arena_window_without_database_identity(
        self,
        client,
        sample_portfolio,
    ):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/arena/managed?direction=long").json()

        row = find(payload["portfolios"], sample_portfolio["slug"])
        spy = find(payload["portfolios"], "spy")
        assert spy["kind"] == "benchmark"
        assert spy["id"] is None
        assert spy["metrics"]["has_data"]
        assert spy["metrics"]["start_date"] == row["inception"]
        assert spy["metrics"]["cumulative_excess"] == 0

        from sqlalchemy import func, select

        from app.db import session_factory
        from app.models import Portfolio

        with session_factory()() as session:
            assert session.scalar(select(func.count()).select_from(Portfolio)) == 1

    def test_pending_allocation_has_no_data(self, client, sample_portfolio):
        payload = client.get("/api/arena/managed?direction=long").json()
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
        assert client.get("/api/arena/managed?direction=long").json()["market_data_status"] == "fresh"
        with session_factory()() as session:
            session.execute(update(PriceCache).values(fetched_at=datetime.now(UTC) - timedelta(hours=2)))
            session.commit()
        monkeypatch.setattr(
            massive,
            "download_prices",
            lambda symbols, _start, _end: massive.PriceDownloadResult({symbol: None for symbol in symbols}),
        )

        requests = [
            ("/api/arena/managed?direction=long", {}),
            (f"/api/portfolios/{sample_portfolio['slug']}", {}),
            (f"/api/compare?direction=long&track=managed&slugs={sample_portfolio['slug']}", {}),
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
            ("/api/arena/managed?direction=long", {}),
            (f"/api/portfolios/{sample_portfolio['slug']}", {}),
            (f"/api/compare?direction=long&track=managed&slugs={sample_portfolio['slug']}", {}),
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
        assert client.get("/api/arena/managed?direction=long").json()["market_data_status"] == "fresh"
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

        payload = client.get("/api/arena/managed?direction=long").json()
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
        assert client.get("/api/arena/managed?direction=long").json()["market_data_status"] == "fresh"
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

        payload = client.get("/api/arena/managed?direction=long").json()
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
        assert portfolio["prompt"]["mode"] == "both"
        assert "managed_text" not in portfolio["prompt"]
        assert "rebuilt_text" not in portfolio["prompt"]
        assert "text" not in portfolio["prompt"]
        assert portfolio["prompt_mode"] == "managed"
        execution_prompt = portfolio["execution_prompt"]
        assert execution_prompt.startswith("Evaluate the Portfolio Arena portfolio")
        assert sample_portfolio["slug"] in execution_prompt
        assert sample_prompt["managed_text"] in execution_prompt
        assert sample_prompt["rebuilt_text"] not in execution_prompt
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
            f"/api/admin/prompts/{sample_prompt['id']}",
            json={"rebuilt_text": reconstruction_strategy},
            headers=admin_headers,
        )
        assert response.status_code == 200
        response = client.post(
            f"/api/portfolios/{sample_portfolio['id']}/reset",
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
        assert "construct the complete signal independently from scratch" in execution_prompt
        assert "without regard to previous signals" in execution_prompt
        assert "continuity is useful" not in execution_prompt
        assert "prefer retaining the existing allocation" not in execution_prompt
        assert "call `create_signal` exactly once" in execution_prompt

    def test_strategy_placeholders_are_not_expanded(
        self,
        client,
        admin_headers,
        sample_portfolio,
        sample_prompt,
    ):
        strategy = "Keep this literal token in the strategy: {{portfolio_slug}}."
        response = client.patch(
            f"/api/admin/prompts/{sample_prompt['id']}",
            json={"managed_text": strategy},
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
        response = client.get(f"/api/compare?direction=long&track=managed&slugs={sample_portfolio['slug']}")
        payload = response.json()
        assert len(payload["series"]) == 1
        for entry in payload["series"]:
            assert entry["series"][0]["nav"] == 100.0
            assert entry["series"][0]["date"] == payload["start"]
        assert payload["spy_series"][0]["nav"] == 100.0
        assert payload["spy_series"][0]["date"] == payload["start"]

    def test_bad_params(self, client):
        assert client.get("/api/compare?direction=long&track=managed&slugs=").status_code == 422


class TestPromptsAndAgents:
    def test_synthetic_spy_has_no_prompt_agent_or_model_identity(self, client, admin_headers):
        prompts = client.get("/api/prompts").json()["prompts"]
        assert all(prompt["slug"] != "buy-and-hold" for prompt in prompts)
        assert client.get("/api/prompts/buy-and-hold").status_code == 404
        assert client.get("/api/agents/benchmark").status_code == 404
        assert all(agent["slug"] != "benchmark" for agent in client.get("/api/agents").json()["agents"])
        assert all(
            model["slug"] != "benchmark"
            for model in client.get("/api/models", headers=admin_headers).json()["models"]
        )

        benchmark = find(client.get("/api/arena/managed?direction=long").json()["portfolios"], "spy")
        assert set(benchmark) == {
            "kind",
            "id",
            "slug",
            "name",
            "direction",
            "status",
            "rank",
            "evidence",
            "rank_score",
            "metrics",
            "sparkline",
            "is_liquidated",
            "liquidated_at",
        }

    def test_prompt_detail_lists_portfolios(self, client, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        payload = client.get("/api/prompts/weekly-manager-v1").json()
        assert payload["prompt"]["mode"] == "both"
        assert payload["prompt"]["managed_text"].startswith("Manage a portfolio")
        assert payload["prompt"]["rebuilt_text"].startswith("Select a fresh portfolio")
        assert "text" not in payload["prompt"]
        assert payload["prompt"]["allocation_policies"]["managed"]["derived_min_positions"] == 1
        assert payload["prompt"]["allocation_policies"]["rebuilt"]["derived_min_positions"] == 1
        assert find(payload["portfolios"], sample_portfolio["slug"]) is not None

        row = next(
            prompt
            for prompt in client.get("/api/prompts").json()["prompts"]
            if prompt["id"] == payload["prompt"]["id"]
        )
        assert row["mode"] == "both"
        assert row["managed_text"] == payload["prompt"]["managed_text"]
        assert row["rebuilt_text"] == payload["prompt"]["rebuilt_text"]
        assert "text" not in row

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
            f"/api/admin/prompts/{sample_prompt['id']}",
            json={"managed_text": "Updated instructions."},
            headers=admin_headers,
        )
        payload = client.get(f"/api/prompts/{sample_prompt['slug']}").json()
        assert payload["prompt"]["managed_text"] == "Updated instructions."
        assert payload["prompt"]["rebuilt_text"] == sample_prompt["rebuilt_text"]


class TestAdminMisc:
    def test_settings_roundtrip(self, client, admin_headers):
        original = client.get("/api/settings", headers=admin_headers).json()
        assert original == {
            "default_cost_bps": 10,
            "managed_allocation_policy": {
                "min_position_weight_pct": 10.0,
                "max_position_weight_pct": 25.0,
                "derived_min_positions": 4,
                "derived_max_positions": 10,
            },
            "rebuilt_allocation_policy": {
                "min_position_weight_pct": 10.0,
                "max_position_weight_pct": 100.0,
                "derived_min_positions": 1,
                "derived_max_positions": 10,
            },
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
                "managed_allocation_policy": {
                    "min_position_weight_pct": 5,
                    "max_position_weight_pct": 20,
                },
                "rebuilt_allocation_policy": {
                    "min_position_weight_pct": 20,
                    "max_position_weight_pct": 100,
                },
                "managed_wrapper_prompt": updated_managed,
                "rebuilt_wrapper_prompt": DEFAULT_REBUILT_WRAPPER_PROMPT,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/settings", headers=admin_headers).json() == {
            "default_cost_bps": 25,
            "managed_allocation_policy": {
                "min_position_weight_pct": 5.0,
                "max_position_weight_pct": 20.0,
                "derived_min_positions": 5,
                "derived_max_positions": 20,
            },
            "rebuilt_allocation_policy": {
                "min_position_weight_pct": 20.0,
                "max_position_weight_pct": 100.0,
                "derived_min_positions": 1,
                "derived_max_positions": 5,
            },
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
                    "managed_allocation_policy": {
                        "min_position_weight_pct": 10,
                        "max_position_weight_pct": 25,
                    },
                    "rebuilt_allocation_policy": {
                        "min_position_weight_pct": 10,
                        "max_position_weight_pct": 100,
                    },
                    "managed_wrapper_prompt": invalid,
                    "rebuilt_wrapper_prompt": DEFAULT_REBUILT_WRAPPER_PROMPT,
                },
                headers=admin_headers,
            )
            assert response.status_code == 422, response.text

    def test_settings_reject_infeasible_allocation_policy_atomically(self, client, admin_headers):
        original = client.get("/api/settings", headers=admin_headers).json()
        payload = {
            **original,
            "managed_allocation_policy": {
                "min_position_weight_pct": 34,
                "max_position_weight_pct": 40,
            },
        }

        response = client.put("/api/settings", json=payload, headers=admin_headers)

        assert response.status_code == 422, response.text
        assert client.get("/api/settings", headers=admin_headers).json() == original

    def test_mode_settings_control_portfolio_policy_and_execution_prompt(
        self,
        client,
        admin_headers,
        sample_portfolio,
    ):
        settings = client.get("/api/settings", headers=admin_headers).json()
        settings["managed_allocation_policy"] = {
            "min_position_weight_pct": 20,
            "max_position_weight_pct": 50,
        }
        response = client.put("/api/settings", json=settings, headers=admin_headers)
        assert response.status_code == 200, response.text

        detail_response = client.get(f"/api/portfolios/{sample_portfolio['slug']}")
        assert detail_response.status_code == 200, detail_response.text
        portfolio = detail_response.json()["portfolio"]
        assert portfolio["prompt"]["allocation_policy"] == {
            "min_position_weight_pct": 20.0,
            "max_position_weight_pct": 50.0,
            "derived_min_positions": 2,
            "derived_max_positions": 5,
        }
        assert "Use between 2 and 5 positions." in portfolio["execution_prompt"]
        assert "between 20% and 50% of NAV" in portfolio["execution_prompt"]

    def test_clear_price_cache(self, client, admin_headers, sample_portfolio):
        backdate_allocation(sample_portfolio["allocation"]["id"])
        client.get("/api/arena/managed?direction=long")  # populates the cache
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
