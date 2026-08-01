"""Direction isolation contracts for public arena and comparison endpoints."""

import json
from datetime import UTC, date, datetime, timedelta

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
            "agent_id": sample_agent["id"],
            "prompt_id": prompt["id"],
            "prompt_mode": prompt_mode,
            "direction": direction,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _trading_days(start: date, count: int) -> list[date]:
    from app.services.trading_calendar import is_trading_day

    days = []
    day = start
    while len(days) < count:
        if is_trading_day(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def _insert_signals(portfolio_id: int, effective_dates: list[date], symbol: str) -> None:
    from app.db import session_factory
    from app.models import Signal, SignalPosition

    with session_factory()() as session:
        for index, effective_date in enumerate(effective_dates):
            signal = Signal(
                portfolio_id=portfolio_id,
                entered_at=datetime.combine(
                    effective_date - timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                effective_date=effective_date,
                note=f"signal {index}",
                provenance="integrated",
            )
            signal.positions.append(
                SignalPosition(
                    symbol=symbol,
                    weight_pct=100,
                    note="private rationale",
                )
            )
            session.add(signal)
        session.commit()


def _set_founding(*portfolio_ids: int) -> None:
    from app.db import session_factory
    from app.models import Portfolio

    with session_factory()() as session:
        for portfolio_id in portfolio_ids:
            session.get(Portfolio, portfolio_id).founding_v2 = True
        session.commit()


def _portfolio_row(payload: dict, slug: str) -> dict:
    return next(row for row in payload["portfolios"] if row["slug"] == slug)


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

    long_payload = client.get("/api/arena/managed?direction=long").json()
    short_response = client.get("/api/arena/managed?direction=short")
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
            "track": "managed",
            "direction": "long",
            "slugs": f"{long['slug']},{short['slug']}",
        },
    )

    assert response.status_code == 422
    assert short["slug"] in response.json()["detail"]
    assert "other direction" in response.json()["detail"]


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
        {"direction": "short"},
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
        {"direction": "short"},
    )
    assert rebuilt["direction"] == "short"
    assert rebuilt["portfolios"][0]["name"] == "Short SPY"
    assert _contestant_slugs(rebuilt) == {portfolios[("rebuilt", "short")]["slug"]}


def test_short_rebuilt_common_is_isolated_and_requires_horizon_twenty(
    client,
    admin_headers,
    sample_agent,
):
    from .util import past_trading_day

    prompt = _create_prompt(client, admin_headers, "Direction Rebuilt")
    long = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        prompt,
        name="Founding Long Rebuilt",
        prompt_mode="rebuilt",
        direction="long",
    )
    short = _create_portfolio(
        client,
        admin_headers,
        sample_agent,
        prompt,
        name="Founding Short Rebuilt",
        prompt_mode="rebuilt",
        direction="short",
    )
    _set_founding(long["id"], short["id"])
    recent_dates = _trading_days(past_trading_day(14), 5)
    _insert_signals(long["id"], recent_dates, "AAPL")
    _insert_signals(short["id"], recent_dates, "MSFT")

    short_signal_response = client.get(
        "/api/arena/rebuilt",
        params={
            "direction": "short",
            "view": "signal",
            "horizon": 1,
            "cost_basis": "gross",
        },
    )
    assert short_signal_response.status_code == 200, short_signal_response.text
    short_signal = short_signal_response.json()
    short_signal_row = _portfolio_row(short_signal, short["slug"])
    assert short_signal_row["metrics"]["eligible"] is True
    assert short_signal_row["direction"] == "short"
    assert short_signal["portfolios"][0]["name"] == "Short SPY"
    assert short_signal["portfolios"][0]["direction"] == "short"

    long_common_response = client.get("/api/arena/rebuilt?direction=long")
    short_common_response = client.get("/api/arena/rebuilt?direction=short")
    assert long_common_response.status_code == 200, long_common_response.text
    assert short_common_response.status_code == 200, short_common_response.text
    long_common = long_common_response.json()
    short_common = short_common_response.json()

    assert _contestant_slugs(long_common) == {long["slug"]}
    assert _contestant_slugs(short_common) == {short["slug"]}
    assert long_common["common_policy"] is not None
    assert _portfolio_row(long_common, long["slug"])["common_admitted"] is True

    short_common_row = _portfolio_row(short_common, short["slug"])
    assert short_common["common_policy"] is None
    assert short_common_row["founding_v2"] is True
    assert short_common_row["common_admitted"] is False
    assert short_common_row["selected_policy"] is None
    assert short_common_row["direction"] == "short"
    assert short_common["portfolios"][0]["name"] == "Short SPY"
