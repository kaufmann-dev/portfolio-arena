"""MCP server: API-key gate and tool round-trips over the mounted /mcp endpoint."""

import json

MCP_URL = "/mcp/"


def _rpc(client, headers, method, params=None, req_id=1):
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    return client.post(MCP_URL, json=payload, headers=headers)


def _call_tool(client, headers, name, arguments=None):
    response = _rpc(client, headers, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


class TestMcpAuth:
    def _headers(self, token: str | None) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def test_missing_key_rejected(self, client):
        assert _rpc(client, self._headers(None), "tools/list").status_code == 401

    def test_bad_key_rejected(self, client):
        assert _rpc(client, self._headers("arena_not-a-real-key"), "tools/list").status_code == 401

    def test_revoked_key_rejected(self, client, admin_headers):
        created = client.post("/api/keys", json={"name": "rev"}, headers=admin_headers).json()
        client.delete(f"/api/keys/{created['id']}", headers=admin_headers)
        assert _rpc(client, self._headers(created["key"]), "tools/list").status_code == 401

    def test_internal_worker_token_can_only_call_read_tools(self, client):
        headers = self._headers("test-internal-worker-token")
        allowed = _rpc(
            client,
            headers,
            "tools/call",
            {"name": "get_effective_date", "arguments": {}},
        )
        assert allowed.status_code == 200

        blocked = _rpc(
            client,
            headers,
            "tools/call",
            {"name": "create_agent", "arguments": {"name": "not allowed"}},
        )
        assert blocked.status_code == 403

        hidden_history = _rpc(
            client,
            headers,
            "tools/call",
            {"name": "list_evaluation_runs", "arguments": {}},
        )
        assert hidden_history.status_code == 403


class TestMcpTools:
    def test_endpoint_works_without_trailing_slash(self, client, mcp_headers):
        # Clients may hit /mcp or /mcp/; both must reach the MCP app, not the SPA.
        response = client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=mcp_headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["tools"]

    def test_tools_list(self, client, mcp_headers):
        response = _rpc(client, mcp_headers, "tools/list")
        assert response.status_code == 200, response.text
        names = {tool["name"] for tool in response.json()["result"]["tools"]}
        assert {
            "get_portfolio",
            "get_arena_overview",
            "get_rebuilt_analysis",
            "list_harnesses",
            "list_models",
            "create_model",
            "update_model",
            "delete_model",
            "archive_agent",
            "unarchive_agent",
            "create_allocation",
            "create_meta_portfolio_set",
            "update_meta_portfolio_set",
            "create_signal",
            "update_signal",
            "delete_signal",
            "archive_prompt",
            "reset_portfolio",
            "get_evaluator_dashboard",
            "configure_portfolio_evaluator",
            "run_evaluations",
            "cancel_evaluation_run",
            "retry_evaluation_run",
            "list_evaluation_runs",
        } <= names
        assert "delete_prompt" not in names
        assert "list_prompt_versions" not in names
        assert "restore_prompt_version" not in names
        assert "unarchive_prompt" not in names
        # Key management is never exposed as a tool.
        assert not any("key" in name.lower() for name in names)

    def test_agent_archive_roundtrip(self, client, mcp_headers, sample_agent):
        archived = _call_tool(
            client,
            mcp_headers,
            "archive_agent",
            {"agent_id": sample_agent["id"]},
        )
        assert archived["status"] == "archived"
        assert archived["can_delete"] is True

        active_listing = _call_tool(client, mcp_headers, "list_agents")
        assert all(agent["id"] != sample_agent["id"] for agent in active_listing["agents"])
        archived_listing = _call_tool(
            client,
            mcp_headers,
            "list_agents",
            {"status": "archived"},
        )
        assert [agent["id"] for agent in archived_listing["agents"]] == [sample_agent["id"]]

        restored = _call_tool(
            client,
            mcp_headers,
            "unarchive_agent",
            {"agent_id": sample_agent["id"]},
        )
        assert restored["status"] == "active"

    def test_evaluator_dashboard(self, client, mcp_headers):
        data = _call_tool(client, mcp_headers, "get_evaluator_dashboard")
        assert set(data) == {"settings", "portfolios", "runtime"}
        assert data["settings"]["enabled"] is True
        assert data["settings"]["queue_before_close_minutes"] == 90
        assert "cutoff_before_close_minutes" not in data["settings"]
        assert data["runtime"]["online"] is False

    def test_arena_overview(self, client, mcp_headers, sample_portfolio):
        from .util import backdate_allocation

        backdate_allocation(sample_portfolio["allocation"]["id"])
        data = _call_tool(
            client,
            mcp_headers,
            "get_arena_overview",
            {"direction": "long"},
        )
        assert data["managed"]["portfolios"]
        assert data["managed"]["market_data_status"] == "fresh"
        assert data["rebuilt"]["portfolios"][0]["kind"] == "benchmark"
        # Curated: the token-heavy sparkline is stripped.
        assert all(
            "sparkline" not in row for row in data["managed"]["portfolios"] if row["kind"] != "benchmark"
        )

    def test_valuation_tools_report_stale_cache_fallback(
        self,
        client,
        mcp_headers,
        sample_portfolio,
        monkeypatch,
    ):
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update

        from app.db import session_factory
        from app.models import PriceCache
        from app.services import massive

        from .util import backdate_allocation

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        assert (
            _call_tool(
                client,
                mcp_headers,
                "get_arena_overview",
                {"direction": "long"},
            )["managed"]["market_data_status"]
            == "fresh"
        )
        with session_factory()() as session:
            session.execute(update(PriceCache).values(fetched_at=datetime.now(UTC) - timedelta(hours=2)))
            session.commit()
        monkeypatch.setattr(
            massive,
            "download_prices",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("read performed I/O")),
        )

        overview = _call_tool(
            client,
            mcp_headers,
            "get_arena_overview",
            {"direction": "long"},
        )
        portfolio = _call_tool(
            client,
            mcp_headers,
            "get_portfolio",
            {"slug_or_id": sample_portfolio["slug"]},
        )

        assert overview["managed"]["market_data_status"] == "fresh"
        assert portfolio["market_data_status"] == "fresh"
        assert overview["managed"]["as_of"] is not None
        assert portfolio["as_of"] is not None

    def test_valuation_tools_report_unavailable_prices(
        self,
        client,
        mcp_headers,
        sample_portfolio,
        monkeypatch,
    ):
        from app.db import session_factory
        from app.services import price_cache

        from .util import backdate_allocation

        backdate_allocation(sample_portfolio["allocation"]["id"], days_back=45)
        with session_factory()() as session:
            price_cache.clear_cache(session)

        overview = _call_tool(
            client,
            mcp_headers,
            "get_arena_overview",
            {"direction": "long"},
        )
        portfolio = _call_tool(
            client,
            mcp_headers,
            "get_portfolio",
            {"slug_or_id": sample_portfolio["slug"]},
        )

        assert overview["managed"]["market_data_status"] == "unavailable"
        assert portfolio["market_data_status"] == "unavailable"
        assert overview["managed"]["as_of"] is None
        assert portfolio["as_of"] is None

    def test_get_portfolio_is_curated(self, client, mcp_headers, sample_portfolio, sample_prompt):
        from .util import backdate_allocation

        backdate_allocation(sample_portfolio["allocation"]["id"])
        data = _call_tool(client, mcp_headers, "get_portfolio", {"slug_or_id": sample_portfolio["slug"]})
        portfolio = data["portfolio"]
        assert data["market_data_status"] == "fresh"
        assert portfolio["prompt"]["mode"] == "both"
        assert portfolio["prompt"]["direction"] == "both"
        assert portfolio["prompt"]["text"] == sample_prompt["managed_long_text"]
        assert "managed_long_text" not in portfolio["prompt"]
        assert "managed_short_text" not in portfolio["prompt"]
        assert "rebuilt_long_text" not in portfolio["prompt"]
        assert "rebuilt_short_text" not in portfolio["prompt"]
        assert portfolio["prompt_mode"] == "managed"
        assert portfolio["allocations"]  # history with notes
        assert "next_entry" in portfolio
        for stripped in ("execution_prompt", "series", "spy_series", "sparkline", "stale_days"):
            assert stripped not in portfolio

    def test_get_rebuilt_portfolio_omits_prior_state(
        self,
        client,
        mcp_headers,
        admin_headers,
        sample_agent,
        sample_prompt,
    ):
        response = client.post(
            "/api/portfolios",
            json={
                "name": "MCP Rebuilt",
                "agent_id": sample_agent["id"],
                "prompt_id": sample_prompt["id"],
                "prompt_mode": "rebuilt",
                "direction": "long",
            },
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text
        rebuilt = response.json()

        signal = _call_tool(
            client,
            mcp_headers,
            "create_signal",
            {
                "portfolio_id": rebuilt["id"],
                "positions": [{"symbol": "AAPL", "weight_pct": 100}],
                "note": "independent signal",
            },
        )
        assert signal["provenance"] == "mcp"

        data = _call_tool(client, mcp_headers, "get_portfolio", {"slug_or_id": rebuilt["slug"]})
        portfolio = data["portfolio"]
        assert portfolio["prompt_mode"] == "rebuilt"
        assert portfolio["prompt"]["mode"] == "both"
        assert portfolio["prompt"]["direction"] == "both"
        assert portfolio["prompt"]["text"] == sample_prompt["rebuilt_long_text"]
        assert "managed_long_text" not in portfolio["prompt"]
        assert "managed_short_text" not in portfolio["prompt"]
        assert "rebuilt_long_text" not in portfolio["prompt"]
        assert "rebuilt_short_text" not in portfolio["prompt"]
        assert portfolio["prompt"]["allocation_policy"]
        assert "next_entry" in portfolio
        for hidden in (
            "cost_bps",
            "inception",
            "age_days",
            "too_early",
            "allocation_count",
            "metrics",
            "stale_data",
            "frozen_symbols",
            "error",
            "holdings",
            "allocations",
            "signals",
        ):
            assert hidden not in portfolio

        analysis = _call_tool(
            client,
            mcp_headers,
            "get_rebuilt_analysis",
            {
                "view": "signal",
                "objective": "canonical",
                "cost_basis": "gross",
                "horizon": 1,
                "direction": "long",
            },
        )
        assert analysis["context"]["horizon"] == 1
        assert analysis["portfolios"][0]["kind"] == "benchmark"

    def test_write_roundtrip(self, client, mcp_headers, admin_headers):
        model = _call_tool(
            client,
            mcp_headers,
            "create_model",
            {"name": "MCP Model", "capabilities": []},
        )
        agent = _call_tool(
            client,
            mcp_headers,
            "create_agent",
            {
                "model_id": model["id"],
                "harness": None,
                "reasoning_effort": None,
            },
        )
        prompt = _call_tool(
            client,
            mcp_headers,
            "create_prompt",
            {
                "name": "MCP Prompt",
                "mode": "managed",
                "direction": "long",
                "managed_long_text": "Beat SPY.",
            },
        )
        portfolio = _call_tool(
            client,
            mcp_headers,
            "create_portfolio",
            {
                "name": "MCP Portfolio",
                "agent_id": agent["id"],
                "prompt_id": prompt["id"],
                "prompt_mode": "managed",
                "direction": "long",
            },
        )
        allocation = _call_tool(
            client,
            mcp_headers,
            "create_allocation",
            {
                "portfolio_id": portfolio["id"],
                "positions": [
                    {"symbol": "AAPL", "weight_pct": 25},
                    {"symbol": "MSFT", "weight_pct": 25},
                    {"symbol": "SPY", "weight_pct": 25},
                    {"symbol": "RSP", "weight_pct": 25},
                ],
                "note": "entered via mcp",
            },
        )
        assert allocation["note"] == "entered via mcp"

        # The write is visible through the REST admin surface.
        detail = client.get(f"/api/portfolios/{portfolio['id']}/detail", headers=admin_headers).json()
        assert detail["portfolio"]["allocations"][0]["note"] == "entered via mcp"

        reset = _call_tool(
            client,
            mcp_headers,
            "reset_portfolio",
            {"portfolio_id": portfolio["id"]},
        )
        assert reset["deleted_allocations"] == 1
        detail = client.get(f"/api/portfolios/{portfolio['id']}/detail", headers=admin_headers).json()
        assert detail["portfolio"]["allocations"] == []

    def test_create_and_update_meta_portfolio_set(
        self,
        client,
        admin_headers,
        mcp_headers,
        sample_agent,
        sample_model,
    ):
        prompt = _call_tool(
            client,
            mcp_headers,
            "create_prompt",
            {
                "name": "MCP Arena Synthesis",
                "context_scope": "arena",
                "mode": "both",
                "direction": "both",
                "managed_long_text": "Managed long synthesis.",
                "managed_short_text": "Managed short synthesis.",
                "rebuilt_long_text": "Rebuilt long synthesis.",
                "rebuilt_short_text": "Rebuilt short synthesis.",
            },
        )
        assert prompt["context_scope"] == "arena"

        created = _call_tool(
            client,
            mcp_headers,
            "create_meta_portfolio_set",
            {
                "family_name": "MCP Confluence",
                "agent_id": sample_agent["id"],
                "prompt_id": prompt["id"],
            },
        )
        assert len(created["portfolios"]) == 4
        assert all(portfolio["evaluator"]["enabled"] for portfolio in created["portfolios"])

        replacement_response = client.post(
            "/api/agents",
            json={
                "model_id": sample_model["id"],
                "harness": "codex",
                "reasoning_effort": "high",
            },
            headers=admin_headers,
        )
        assert replacement_response.status_code == 201, replacement_response.text
        replacement = replacement_response.json()
        updated = _call_tool(
            client,
            mcp_headers,
            "update_meta_portfolio_set",
            {"meta_set_id": created["id"], "agent_id": replacement["id"]},
        )
        assert updated["agent_id"] == replacement["id"]

        variant = _call_tool(
            client,
            mcp_headers,
            "create_meta_portfolio_set",
            {
                "family_name": "MCP Confluence",
                "variant_label": "Ultra",
                "agent_id": sample_agent["id"],
                "prompt_id": prompt["id"],
            },
        )
        assert variant["slug"] == "mcp-confluence-ultra"
        assert variant["variant_label"] == "Ultra"
        assert [portfolio["name"] for portfolio in variant["portfolios"]] == [
            "MCP Confluence Core Ultra",
            "MCP Confluence Pulse Ultra",
            "MCP Confluence Shadow Ultra",
            "MCP Confluence Probe Ultra",
        ]

    def test_update_normal_portfolio_agent(
        self,
        client,
        admin_headers,
        mcp_headers,
        sample_portfolio,
        sample_model,
    ):
        replacement_response = client.post(
            "/api/agents",
            json={
                "model_id": sample_model["id"],
                "harness": "codex",
                "reasoning_effort": "high",
            },
            headers=admin_headers,
        )
        assert replacement_response.status_code == 201, replacement_response.text
        replacement = replacement_response.json()

        updated = _call_tool(
            client,
            mcp_headers,
            "update_portfolio",
            {"portfolio_id": sample_portfolio["id"], "agent_id": replacement["id"]},
        )
        assert updated["agent_id"] == replacement["id"]

        detail = _call_tool(
            client,
            mcp_headers,
            "get_portfolio",
            {"slug_or_id": str(sample_portfolio["id"])},
        )
        assert detail["portfolio"]["agent"]["id"] == replacement["id"]

    def test_generic_prompt_exposes_both_fields_but_archive_hides_it(self, client, mcp_headers):
        created = _call_tool(
            client,
            mcp_headers,
            "create_prompt",
            {
                "name": "MCP Both Prompt",
                "mode": "both",
                "direction": "both",
                "managed_long_text": "Managed Long MCP strategy.",
                "managed_short_text": "Managed Short MCP strategy.",
                "rebuilt_long_text": "Rebuilt Long MCP strategy.",
                "rebuilt_short_text": "Rebuilt Short MCP strategy.",
            },
        )

        generic = _call_tool(
            client,
            mcp_headers,
            "get_prompt",
            {"slug_or_id": str(created["id"])},
        )
        assert generic["mode"] == "both"
        assert generic["direction"] == "both"
        assert generic["managed_long_text"] == "Managed Long MCP strategy."
        assert generic["managed_short_text"] == "Managed Short MCP strategy."
        assert generic["rebuilt_long_text"] == "Rebuilt Long MCP strategy."
        assert generic["rebuilt_short_text"] == "Rebuilt Short MCP strategy."
        assert generic["allocation_policies"]["managed"]["max_position_weight_pct"] == 25
        assert generic["allocation_policies"]["rebuilt"]["max_position_weight_pct"] == 100
        assert "text" not in generic

        assert generic["context_scope"] == "portfolio"

        _call_tool(
            client,
            mcp_headers,
            "archive_prompt",
            {"prompt_id": created["id"]},
        )
        listing = _call_tool(client, mcp_headers, "list_prompts")
        assert all(prompt["id"] != created["id"] for prompt in listing["prompts"])

        response = _rpc(
            client,
            mcp_headers,
            "tools/call",
            {
                "name": "get_prompt",
                "arguments": {"slug_or_id": str(created["id"])},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["isError"] is True

    def test_tool_error_surfaces_message(self, client, mcp_headers):
        response = _rpc(
            client, mcp_headers, "tools/call", {"name": "get_portfolio", "arguments": {"slug_or_id": "nope"}}
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["isError"]
        assert "not found" in result["content"][0]["text"].lower()
