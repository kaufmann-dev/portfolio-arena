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

        inventory = _rpc(
            client,
            headers,
            "tools/call",
            {"name": "list_portfolios", "arguments": {}},
        )
        assert inventory.status_code == 403


class TestMcpTools:
    def test_market_diagnostics_matches_api_and_validates_scope(self, client, mcp_headers):
        args = {"version_id": 1, "track": "rebuilt", "direction": "short"}
        data = _call_tool(client, mcp_headers, "get_market_data_diagnostics", args)
        response = client.get("/api/market-data/diagnostics", params=args)
        assert response.status_code == 200
        assert data == response.json()
        assert {item["symbol"] for item in data["symbols"]} == {"SPY"}
        for key, value in (("track", "invalid"), ("direction", "invalid"), ("version_id", 999999)):
            invalid = {**args, key: value}
            assert client.get("/api/market-data/diagnostics", params=invalid).status_code in {404, 422}
            result = _rpc(
                client,
                mcp_headers,
                "tools/call",
                {"name": "get_market_data_diagnostics", "arguments": invalid},
            ).json()["result"]
            assert result["isError"]

    def test_harness_registry_matches_api_with_opencode(self, client, mcp_headers, admin_headers):
        registry = _call_tool(client, mcp_headers, "list_harnesses")
        assert registry == client.get("/api/harnesses", headers=admin_headers).json()
        opencode = next(item for item in registry["harnesses"] if item["id"] == "opencode")
        assert opencode["reasoning_effort_mode"] == "custom"
        assert opencode["reasoning_efforts"] == []

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
            "list_portfolios",
            "get_arena_overview",
            "get_rebuilt_analysis",
            "get_market_data_diagnostics",
            "list_harnesses",
            "list_models",
            "create_model",
            "update_model",
            "delete_model",
            "create_allocation",
            "create_signal",
            "update_signal",
            "delete_signal",
            "reset_portfolio",
            "get_evaluator_dashboard",
            "configure_portfolio_evaluator",
            "run_evaluations",
            "cancel_evaluation_run",
            "delete_evaluation_run",
            "retry_evaluation_run",
            "list_evaluation_runs",
        } <= names
        assert {
            "delete_prompt",
            "list_versions",
            "create_version",
            "update_version",
            "delete_version",
        } <= names
        assert "list_prompt_versions" not in names
        assert "restore_prompt_version" not in names
        assert "unarchive_prompt" not in names
        # Key management is never exposed as a tool.
        assert not any("key" in name.lower() for name in names)

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
            {"direction": "long", "version_id": 1},
        )
        assert data["managed"]["portfolios"]
        assert data["managed"]["market_data_status"] == "fresh"
        assert data["rebuilt"]["portfolios"][0]["kind"] == "benchmark"
        assert data["rebuilt"]["objective"] == "signal_mean_daily_alpha"
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
                {"direction": "long", "version_id": 1},
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
            {"direction": "long", "version_id": 1},
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
            {"direction": "long", "version_id": 1},
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
                "version_id": 1,
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
                "version_id": 1,
                "direction": "long",
                "objective": "sharpe",
            },
        )
        assert analysis["objective"] == "sharpe"
        assert analysis["portfolios"][1]["optimization_objective"] == "sharpe"
        assert len(analysis["portfolios"][1]["signal_horizons"]) == 40
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
                "version_id": 1,
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

    def test_generic_prompt_exposes_both_fields_and_deletes_when_unused(self, client, mcp_headers):
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

        _call_tool(
            client,
            mcp_headers,
            "delete_prompt",
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


def test_rebuilt_mcp_default_matches_public_signal_alpha(
    client, mcp_headers, admin_headers, sample_agent, sample_prompt
):
    from datetime import date

    from .test_v2_public_api import _create_rebuilt, _insert_signals, _row, _weekdays

    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt, "MCP Signal Alpha")
    _insert_signals(portfolio["id"], _weekdays(date(2026, 1, 5), 30))
    default = _call_tool(client, mcp_headers, "get_rebuilt_analysis", {"version_id": 1, "direction": "long"})
    explicit = _call_tool(
        client,
        mcp_headers,
        "get_rebuilt_analysis",
        {"version_id": 1, "direction": "long", "objective": "signal_mean_daily_alpha"},
    )
    assert default == explicit
    assert default["objective"] == "signal_mean_daily_alpha"
    public = client.get("/api/arena/rebuilt?version_id=1&direction=long").json()
    row = _row(default, portfolio["slug"])
    assert row["selected_policy"] is not None
    assert row["metrics"] == _row(public, portfolio["slug"])["metrics"]
    selected_signal = next(
        item for item in row["signal_horizons"] if item["horizon"] == row["selected_policy"]["horizon"]
    )
    assert row["metrics"]["signal_mean_daily_alpha"] == selected_signal["mean_daily_alpha"]


def test_prompt_settings_roundtrip_preview_and_seed_preservation(
    client, mcp_headers, admin_headers, sample_agent, sample_prompt
):
    from app.db import session_factory
    from app.seed import seed_settings

    original = _call_tool(client, mcp_headers, "get_settings")
    updated = {
        **original,
        "allocation_policy_instructions": "Limits {{derived_max_positions}}: "
        "{{min_position_weight_pct}}–{{max_position_weight_pct}}%.",
        "automated_submission_instructions": "Worker output marker; keep {{strategy_text}} literal.",
        "managed_manual_submission_instructions": "Managed submission marker.",
        "rebuilt_manual_submission_instructions": "Rebuilt submission marker.",
    }
    assert _call_tool(client, mcp_headers, "update_settings", updated) == updated
    with session_factory()() as session:
        seed_settings(session)
    assert _call_tool(client, mcp_headers, "get_settings") == updated
    assert client.get("/api/settings", headers=admin_headers).json() == updated

    for mode in ("managed", "rebuilt"):
        for direction in ("long", "short"):
            portfolio = _call_tool(
                client,
                mcp_headers,
                "create_portfolio",
                {
                    "name": f"Preview {mode} {direction}",
                    "version_id": 1,
                    "agent_id": sample_agent["id"],
                    "prompt_id": sample_prompt["id"],
                    "prompt_mode": mode,
                    "direction": direction,
                },
            )
            for automated in (False, True):
                preview = _call_tool(
                    client,
                    mcp_headers,
                    "preview_execution_prompt",
                    {"portfolio_id": portfolio["id"], "automated": automated},
                )
                rendered = preview["execution_prompt"]
                assert preview["prompt_mode"] == mode
                assert preview["direction"] == direction
                assert preview["automated"] == automated
                assert "Limits 10: 10–100%." in rendered
                assert sample_prompt[f"{mode}_{direction}_text"] in rendered
                assert updated[f"{direction}_direction_instructions"] in rendered
                if automated:
                    assert updated["automated_submission_instructions"] in rendered
                    assert "Managed submission marker" not in rendered
                    assert "Rebuilt submission marker" not in rendered
                else:
                    assert updated[f"{mode}_manual_submission_instructions"] in rendered
                    assert "Worker output marker" not in rendered
                    public = client.get(f"/api/portfolios/{portfolio['slug']}")
                    assert public.status_code == 200, public.text
                    assert public.json()["portfolio"]["execution_prompt"] == rendered
                    admin = client.get(f"/api/portfolios/{portfolio['id']}/detail", headers=admin_headers)
                    assert admin.status_code == 200, admin.text
                    assert admin.json()["portfolio"]["execution_prompt"] == rendered


def test_prompt_settings_invalid_edits_are_atomic_in_mcp_and_rest(client, mcp_headers, admin_headers):
    original = _call_tool(client, mcp_headers, "get_settings")
    template = original["allocation_policy_instructions"]
    invalid = [
        ("allocation_policy_instructions", " "),
        ("allocation_policy_instructions", template.replace("{{derived_max_positions}}", "")),
        ("allocation_policy_instructions", template + " {{unknown}}"),
        ("allocation_policy_instructions", template + " {{unclosed"),
        ("automated_submission_instructions", "\n"),
        ("managed_manual_submission_instructions", " "),
        ("rebuilt_manual_submission_instructions", "\t"),
    ]
    for field, value in invalid:
        arguments = {
            **original,
            "long_direction_instructions": "This must not be saved.",
            field: value,
        }
        response = _rpc(
            client, mcp_headers, "tools/call", {"name": "update_settings", "arguments": arguments}
        )
        assert response.json()["result"]["isError"], response.text
        assert _call_tool(client, mcp_headers, "get_settings") == original
        response = client.put("/api/settings", json=arguments, headers=admin_headers)
        assert response.status_code == 422, response.text
        assert _call_tool(client, mcp_headers, "get_settings") == original


def test_execution_prompt_preview_is_admin_only_and_rejects_missing_portfolio(client, mcp_headers):
    headers = {**mcp_headers, "Authorization": "Bearer test-internal-worker-token"}
    arguments = {"portfolio_id": 999999}
    response = _rpc(
        client, headers, "tools/call", {"name": "preview_execution_prompt", "arguments": arguments}
    )
    assert response.status_code == 403
    response = _rpc(
        client, mcp_headers, "tools/call", {"name": "preview_execution_prompt", "arguments": arguments}
    )
    assert response.json()["result"]["isError"]
    assert "Portfolio not found" in response.json()["result"]["content"][0]["text"]


def test_registered_database_tool_does_not_block_event_loop(sample_portfolio, monkeypatch):
    import asyncio
    import threading

    from app.mcp_server import tools
    from app.mcp_server.server import mcp

    entered = threading.Event()
    release = threading.Event()
    original = tools._resolve_portfolio

    def blocking_lookup(*args, **kwargs):
        entered.set()
        assert release.wait(2), "MCP database work blocked the event loop"
        return original(*args, **kwargs)

    monkeypatch.setattr(tools, "_resolve_portfolio", blocking_lookup)

    async def exercise():
        task = asyncio.create_task(
            mcp.call_tool("get_portfolio", {"slug_or_id": str(sample_portfolio["id"])})
        )
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            assert not task.done()
            release.set()
            result = await task
            assert result
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
