"""Public v2 arena contracts and rebuilt-history pagination."""

from datetime import UTC, date, datetime, timedelta


def _create_rebuilt(client, admin_headers, sample_agent, sample_prompt, name):
    response = client.post(
        "/api/portfolios",
        json={
            "name": name,
            "version_id": 1,
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "rebuilt",
            "direction": "long",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _weekdays(start: date, count: int) -> list[date]:
    from app.services.trading_calendar import is_trading_day

    result = []
    day = start
    while len(result) < count:
        if is_trading_day(day):
            result.append(day)
        day += timedelta(days=1)
    return result


def _insert_signals(portfolio_id: int, effective_dates: list[date], symbol: str = "AAPL"):
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
            signal.positions.append(SignalPosition(symbol=symbol, weight_pct=100, note="private rationale"))
            session.add(signal)
        session.commit()
    from app.services.market_refresh import refresh_market_data_once

    refresh_market_data_once()


def _row(payload: dict, slug: str) -> dict:
    return next(row for row in payload["portfolios"] if row["slug"] == slug)


def test_rebuilt_detail_bounds_recent_signals_and_public_payload_hides_provenance(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    portfolio = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Signal Pagination",
    )
    _insert_signals(portfolio["id"], _weekdays(date(2026, 4, 1), 21))
    # A late scheduled result can have a newer id but an earlier effective date.
    _insert_signals(portfolio["id"], [date(2026, 3, 31)])

    response = client.get(f"/api/portfolios/{portfolio['slug']}")
    assert response.status_code == 200, response.text
    detail = response.json()["portfolio"]
    assert len(detail["signals"]) == 20
    assert detail["signals_next_cursor"] == detail["signals"][-1]["id"]
    assert [signal["id"] for signal in detail["signals"]] == sorted(
        (signal["id"] for signal in detail["signals"]),
        reverse=True,
    )
    assert "provenance" not in detail["signals"][0]
    assert "note" not in detail["signals"][0]["positions"][0]

    first_page = client.get(f"/api/portfolios/{portfolio['slug']}/signals?limit=5").json()
    assert len(first_page["signals"]) == 5
    assert first_page["next_cursor"] is not None
    assert "provenance" not in first_page["signals"][0]
    second_page = client.get(
        f"/api/portfolios/{portfolio['slug']}/signals?limit=5&cursor={first_page['next_cursor']}"
    ).json()
    assert {signal["id"] for signal in first_page["signals"]}.isdisjoint(
        {signal["id"] for signal in second_page["signals"]}
    )
    assert [signal["id"] for signal in detail["signals"][:5]] == [
        signal["id"] for signal in first_page["signals"]
    ]


def test_compare_rejects_missing_and_wrong_track_slugs(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    sample_portfolio,
):
    rebuilt = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Compare Rebuilt",
    )

    missing = client.get(
        f"/api/compare?version_id=1&direction=long&track=rebuilt&slugs={rebuilt['slug']},missing"
    )
    assert missing.status_code == 404
    wrong_track = client.get(
        f"/api/compare?version_id=1&direction=long&track=rebuilt&slugs={rebuilt['slug']},{sample_portfolio['slug']}"
    )
    assert wrong_track.status_code == 422


def test_versions_scope_readiness_and_keep_paused_history_visible(
    client, admin_headers, sample_portfolio, sample_agent, sample_prompt
):
    from app.db import session_factory
    from app.models import Allocation, Portfolio, Position
    from tests.util import backdate_allocation

    effective_date = backdate_allocation(sample_portfolio["allocation"]["id"], 30)
    created = client.post("/api/admin/versions", json={"name": "v2"}, headers=admin_headers)
    assert created.status_code == 201, created.text
    version = created.json()
    assert version["evaluation_enabled"] is False
    newer = client.post(
        "/api/portfolios",
        json={
            "name": "New version",
            "version_id": version["id"],
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "managed",
            "direction": "long",
            "execution_boundary": "open",
        },
        headers=admin_headers,
    )
    assert newer.status_code == 201, newer.text
    newer = newer.json()
    # This version needs a symbol without cached prices; it must not hold back v1.
    with session_factory()() as session:
        decision = Allocation(
            portfolio_id=newer["id"],
            entered_at=datetime.now(UTC),
            effective_date=effective_date,
            note="missing market data",
        )
        decision.positions.append(Position(symbol="NO_DATA", weight_pct=100, note="private"))
        session.add(decision)
        session.get(Portfolio, newer["id"]).execution_locked = True
        session.commit()
    paused = client.patch("/api/admin/versions/1", json={"evaluation_enabled": False}, headers=admin_headers)
    assert paused.status_code == 200, paused.text
    payload = client.get("/api/arena/managed?version_id=1&direction=long").json()
    row = _row(payload, sample_portfolio["slug"])
    assert row["version"]["evaluation_enabled"] is False
    assert row["metrics"]["has_data"] is True
    assert payload["market_data_status"] == "fresh"
    assert all(item["slug"] != newer["slug"] for item in payload["portfolios"])
    assert client.get("/api/market-data?version_id=1").json()["market_data_status"] == "fresh"
    assert (
        client.get(f"/api/market-data?version_id={version['id']}").json()["market_data_status"]
        == "unavailable"
    )
    detail = client.get(f"/api/portfolios/{sample_portfolio['slug']}").json()
    assert detail["version_id"] == 1
    assert detail["portfolio"]["series"]
    cross_version = client.get(
        "/api/compare",
        params={
            "version_id": 1,
            "track": "managed",
            "direction": "long",
            "slugs": f"{sample_portfolio['slug']},{newer['slug']}",
        },
    )
    assert cross_version.status_code == 404
    versions = client.get("/api/versions").json()["versions"]
    assert versions[0]["id"] == version["id"]
    assert {item["id"] for item in versions} == {1, version["id"]}


def test_version_required_for_arena_compare_and_snapshot(client):
    for url in (
        "/api/arena/managed?direction=long",
        "/api/arena/rebuilt?direction=long",
        "/api/compare?track=managed&direction=long&slugs=one",
        "/api/market-data",
    ):
        assert client.get(url).status_code == 422, url
    assert client.get("/api/arena/managed?version_id=999&direction=long").status_code == 404
    assert client.get("/api/meta/managed?direction=long").status_code == 404
    assert client.get("/api/meta/rebuilt?direction=long").status_code == 404


def test_rebuilt_api_exposes_only_canonical_horizons(client, admin_headers, sample_agent, sample_prompt):
    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt, "Canonical Horizons")
    _insert_signals(portfolio["id"], _weekdays(date(2026, 4, 1), 8))
    payload = client.get("/api/arena/rebuilt?version_id=1&direction=long").json()
    row = _row(payload, portfolio["slug"])
    assert set(row["selected_policy"]) == {"horizon"}
    assert row["selected_policy"]["horizon"] in [step / 2 for step in range(1, 41)]
    assert [item["horizon"] for item in row["signal_horizons"]] == [step / 2 for step in range(1, 41)]
    assert row["metrics"]["family_size"] == 40
    assert not {"common_policy", "context"}.intersection(payload)
    assert not {"cost_bps", "founding_v2", "common_admitted", "status"}.intersection(row)
    detail = client.get(f"/api/portfolios/{portfolio['slug']}").json()["portfolio"]
    assert not {"policy_matrix", "aggregate_policy"}.intersection(detail)
    assert all(set(point) == {"timestamp", "phase", "nav"} for point in detail["series"])
    assert detail["signals"][0]["effective_at"]["phase"] == "close"
    assert "effective_date" not in detail["signals"][0]


def test_empty_rebuilt_has_forty_pending_cells_without_price_cache(
    client, admin_headers, sample_agent, sample_prompt
):
    portfolio = _create_rebuilt(client, admin_headers, sample_agent, sample_prompt, "Empty horizons")
    row = _row(client.get("/api/arena/rebuilt?version_id=1&direction=long").json(), portfolio["slug"])
    assert [item["horizon"] for item in row["signal_horizons"]] == [step / 2 for step in range(1, 41)]
    assert all(item["evidence"] == "pending" and item["has_data"] is False for item in row["signal_horizons"])
    assert row["selected_policy"] is None
