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
        assert {"get_portfolio", "get_arena_overview", "create_allocation"} <= names
        # Key management is never exposed as a tool.
        assert not any("key" in name.lower() for name in names)

    def test_arena_overview(self, client, mcp_headers, sample_portfolio):
        data = _call_tool(client, mcp_headers, "get_arena_overview")
        assert data["portfolios"]
        # Curated: the token-heavy sparkline is stripped.
        assert all("sparkline" not in row for row in data["portfolios"])

    def test_get_portfolio_is_curated(self, client, mcp_headers, sample_portfolio):
        data = _call_tool(client, mcp_headers, "get_portfolio", {"slug_or_id": sample_portfolio["slug"]})
        portfolio = data["portfolio"]
        assert portfolio["prompt"]["text"]  # full prompt text, for the rebalancing agent
        assert portfolio["allocations"]  # history with notes
        assert "next_entry" in portfolio
        for stripped in ("series", "spy_series", "sparkline", "stale_days"):
            assert stripped not in portfolio

    def test_write_roundtrip(self, client, mcp_headers, admin_headers):
        agent = _call_tool(client, mcp_headers, "create_agent", {"name": "MCP Agent"})
        prompt = _call_tool(client, mcp_headers, "create_prompt", {"name": "MCP Prompt", "text": "Beat SPY."})
        portfolio = _call_tool(
            client,
            mcp_headers,
            "create_portfolio",
            {"name": "MCP Portfolio", "agent_id": agent["id"], "prompt_id": prompt["id"]},
        )
        allocation = _call_tool(
            client,
            mcp_headers,
            "create_allocation",
            {
                "portfolio_id": portfolio["id"],
                "positions": [{"symbol": "AAPL", "weight_pct": 100}],
                "note": "entered via mcp",
            },
        )
        assert allocation["note"] == "entered via mcp"

        # The write is visible through the REST admin surface.
        detail = client.get(f"/api/portfolios/{portfolio['id']}/detail", headers=admin_headers).json()
        assert detail["portfolio"]["allocations"][0]["note"] == "entered via mcp"

    def test_tool_error_surfaces_message(self, client, mcp_headers):
        response = _rpc(
            client, mcp_headers, "tools/call", {"name": "get_portfolio", "arguments": {"slug_or_id": "nope"}}
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["isError"]
        assert "not found" in result["content"][0]["text"].lower()
