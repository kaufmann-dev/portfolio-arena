"""Direction isolation contracts for public arena and comparison endpoints."""

import json

MCP_URL = "/mcp/"


def _call_tool(client, headers, name: str, arguments: dict) -> dict:
    response = client.post(
        MCP_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def _create_prompt(client, admin_headers, name: str) -> dict:
    response = client.post(
        "/api/admin/prompts",
        json={
            "name": name,
            "mode": "both",
            "direction": "both",
            "managed_long_text": "Manage evidence-backed long opportunities.",
            "managed_short_text": "Manage evidence-backed short opportunities.",
            "rebuilt_long_text": "Select fresh evidence-backed long opportunities.",
            "rebuilt_short_text": "Select fresh evidence-backed short opportunities.",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_portfolio(
    client,
    admin_headers,
    sample_agent,
    prompt: dict,
    *,
    name: str,
    prompt_mode: str,
    direction: str,
) -> dict:
    response = client.post(
        "/api/portfolios",
        json={
            "name": name,
            "version_id": 1,
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
            "prompt_mode": prompt_mode,
            "direction": direction,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _contestant_slugs(payload: dict) -> set[str]:
    return {row["slug"] for row in payload["portfolios"] if row["kind"] != "benchmark"}


def test_managed_arenas_filter_directions_and_expose_direction_fields(
    client,
    admin_headers,
    sample_agent,
):
    prompt = _create_prompt(client, admin_headers, "Direction Managed")
    long = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        prompt,
        name="Long Managed",
        prompt_mode="managed",
        direction="long",
    )
    short = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        prompt,
        name="Short Managed",
        prompt_mode="managed",
        direction="short",
    )

    long_payload = client.get("/api/arena/managed?version_id=1&direction=long").json()
    short_response = client.get("/api/arena/managed?version_id=1&direction=short")
    assert short_response.status_code == 200, short_response.text
    short_payload = short_response.json()

    assert long_payload["direction"] == "long"
    assert short_payload["direction"] == "short"
    assert _contestant_slugs(long_payload) == {long["slug"]}
    assert _contestant_slugs(short_payload) == {short["slug"]}
    assert all(row["direction"] == "long" for row in long_payload["portfolios"])
    assert all(row["direction"] == "short" for row in short_payload["portfolios"])

    short_benchmark = short_payload["portfolios"][0]
    assert short_benchmark["kind"] == "benchmark"
    assert short_benchmark["slug"] == "spy"
    assert short_benchmark["name"] == "Short SPY"
    assert short_benchmark["direction"] == "short"
    assert short_benchmark["is_liquidated"] is False
    assert short_benchmark["liquidated_at"] is None

    detail = client.get(f"/api/portfolios/{short['slug']}").json()
    assert detail["direction"] == "short"
    assert detail["portfolio"]["direction"] == "short"
    assert detail["portfolio"]["is_liquidated"] is False
    assert detail["portfolio"]["liquidated_at"] is None
    execution_prompt = " ".join(detail["portfolio"]["execution_prompt"].split())
    assert "direction-matched SPY reference" in execution_prompt
    assert "prices are expected to underperform SPY" in execution_prompt
    assert prompt["managed_short_text"] in execution_prompt
    assert prompt["managed_long_text"] not in execution_prompt

    prompt_detail = client.get(f"/api/prompts/{prompt['slug']}").json()
    assert {portfolio["slug"]: portfolio["direction"] for portfolio in prompt_detail["portfolios"]} == {
        long["slug"]: "long",
        short["slug"]: "short",
    }
    agent_detail = client.get(f"/api/agents/{sample_agent['slug']}").json()
    assert {portfolio["slug"]: portfolio["direction"] for portfolio in agent_detail["portfolios"]} == {
        long["slug"]: "long",
        short["slug"]: "short",
    }


def test_compare_rejects_a_portfolio_from_the_other_direction(
    client,
    admin_headers,
    sample_agent,
):
    prompt = _create_prompt(client, admin_headers, "Direction Compare")
    long = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        prompt,
        name="Compare Long",
        prompt_mode="managed",
        direction="long",
    )
    short = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        prompt,
        name="Compare Short",
        prompt_mode="managed",
        direction="short",
    )

    response = client.get(
        "/api/compare",
        params={
            "version_id": 1,
            "track": "managed",
            "direction": "long",
            "slugs": f"{long['slug']},{short['slug']}",
        },
    )

    assert response.status_code == 422
    assert "track and direction" in response.json()["detail"]


def test_mcp_arena_reads_filter_short_rows_and_use_short_benchmarks(
    client,
    admin_headers,
    mcp_headers,
    sample_agent,
):
    prompt = _create_prompt(client, admin_headers, "Direction MCP")
    portfolios = {
        (mode, direction): _create_portfolio(
            client,
            admin_headers,
            sample_agent,
            prompt,
            name=f"{direction.title()} {mode.title()} MCP",
            prompt_mode=mode,
            direction=direction,
        )
        for mode in ("managed", "rebuilt")
        for direction in ("long", "short")
    }

    overview = _call_tool(
        client,
        mcp_headers,
        "get_arena_overview",
        {"version_id": 1, "direction": "short"},
    )

    assert overview["direction"] == "short"
    for track in ("managed", "rebuilt"):
        payload = overview[track]
        assert payload["portfolios"][0]["name"] == "Short SPY"
        assert payload["portfolios"][0]["direction"] == "short"
        assert _contestant_slugs(payload) == {portfolios[(track, "short")]["slug"]}
        assert all(row["direction"] == "short" for row in payload["portfolios"])

    rebuilt = _call_tool(
        client,
        mcp_headers,
        "get_rebuilt_analysis",
        {"version_id": 1, "direction": "short"},
    )
    assert rebuilt["direction"] == "short"
    assert rebuilt["portfolios"][0]["name"] == "Short SPY"
    assert _contestant_slugs(rebuilt) == {portfolios[("rebuilt", "short")]["slug"]}
