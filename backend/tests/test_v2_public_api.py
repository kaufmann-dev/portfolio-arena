"""Public v2 arena contracts and rebuilt-history pagination."""

from datetime import UTC, date, datetime, timedelta

import pytest


def _create_rebuilt(client, admin_headers, sample_agent, sample_prompt, name):
    response = client.post(
        "/api/portfolios",
        json={
            "name": name,
            "agent_id": sample_agent["id"],
            "prompt_id": sample_prompt["id"],
            "prompt_mode": "rebuilt",
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


def _set_founding(portfolio_id: int) -> None:
    from app.db import session_factory
    from app.models import Portfolio

    with session_factory()() as session:
        session.get(Portfolio, portfolio_id).founding_v2 = True
        session.commit()


def _row(payload: dict, slug: str) -> dict:
    return next(row for row in payload["portfolios"] if row["slug"] == slug)


def test_separate_track_routes_have_synthetic_spy_and_no_rsp(
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
        "Daily Rebuilt",
    )

    managed = client.get("/api/arena/managed")
    assert managed.status_code == 200, managed.text
    managed_payload = managed.json()
    assert managed_payload["track"] == "managed"
    assert managed_payload["portfolios"][0]["kind"] == "benchmark"
    assert managed_payload["portfolios"][0]["slug"] == "spy"
    assert sample_portfolio["slug"] in {row["slug"] for row in managed_payload["portfolios"]}

    response = client.get("/api/arena/rebuilt")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["track"] == "rebuilt"
    assert payload["context"] == {
        "view": "common",
        "objective": "canonical",
        "cost_basis": "net",
        "horizon": None,
    }
    assert payload["portfolios"][0]["kind"] == "benchmark"
    assert rebuilt["slug"] in {row["slug"] for row in payload["portfolios"]}
    assert all(row["slug"] != "rsp" for row in payload["portfolios"])


def test_rebuilt_query_combinations_are_explicitly_validated(client):
    assert client.get("/api/arena/rebuilt?view=signal").status_code == 422
    assert (
        client.get(
            "/api/arena/rebuilt?view=signal&horizon=5&cost_basis=gross&objective=max_alpha"
        ).status_code
        == 422
    )
    assert client.get("/api/arena/rebuilt?view=tuned&horizon=5").status_code == 422
    assert client.get("/api/arena/rebuilt?view=signal&horizon=5&cost_basis=gross").status_code == 200


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


def test_common_rows_and_spy_use_one_shared_scoring_window(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    first = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Common First",
    )
    second = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Common Second",
    )
    _insert_signals(first["id"], _weekdays(date(2026, 4, 1), 5), "AAPL")
    _insert_signals(second["id"], _weekdays(date(2026, 4, 8), 5), "MSFT")

    payload = client.get("/api/arena/rebuilt").json()
    scoring_start = payload["common_policy"]["scoring_start"]
    rows = [row for row in payload["portfolios"] if row["slug"] in {first["slug"], second["slug"]}]

    assert len(rows) == 2
    assert all(row["common_admitted"] for row in rows)
    assert {row["metrics"]["start_date"] for row in rows} == {scoring_start}
    assert payload["portfolios"][0]["metrics"]["start_date"] == scoring_start
    assert payload["common_policy"]["metrics"]["family_size"] == 20
    assert payload["portfolios"][0]["metrics"]["itd_return"] == pytest.approx(
        payload["common_policy"]["metrics"]["spy_return"]
    )
    assert all(
        row["selected_policy"]["objective_score"] == pytest.approx(row["metrics"]["ci_lower"]) for row in rows
    )

    detail = client.get(f"/api/portfolios/{first['slug']}").json()["portfolio"]
    assert detail["series"][0] == {"date": scoring_start, "nav": 100.0}
    assert detail["spy_series"][0] == {"date": scoring_start, "nav": 100.0}
    assert detail["spy_series"][-1]["nav"] / 100.0 - 1.0 == pytest.approx(detail["metrics"]["spy_return"])

    comparison = client.get(f"/api/compare?track=rebuilt&slugs={first['slug']},{second['slug']}").json()
    assert comparison["start"] == scoring_start
    assert comparison["spy_series"][0] == {"date": scoring_start, "nav": 100.0}
    assert all(line["series"][0] == {"date": scoring_start, "nav": 100.0} for line in comparison["series"])

    optimized = client.get("/api/arena/rebuilt?objective=max_alpha").json()
    assert optimized["common_policy"]["metrics"]["family_size"] == 200


def test_common_incubation_gate_disables_result_and_rank(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    founder = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Common Founder",
    )
    incubating = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Common Incubating",
    )
    _set_founding(founder["id"])
    _insert_signals(founder["id"], _weekdays(date(2026, 4, 1), 5), "MSFT")
    _insert_signals(incubating["id"], _weekdays(date(2026, 7, 20), 5), "AAPL")

    signal_payload = client.get("/api/arena/rebuilt?view=signal&horizon=1&cost_basis=gross").json()
    signal_row = _row(signal_payload, incubating["slug"])
    assert signal_row["metrics"]["eligible"] is True
    assert signal_row["selected_policy"]["objective_score"] == pytest.approx(
        signal_row["metrics"]["ci_lower"]
    )

    common_payload = client.get("/api/arena/rebuilt").json()
    founder_row = _row(common_payload, founder["slug"])
    incubating_row = _row(common_payload, incubating["slug"])
    assert founder_row["common_admitted"] is True
    assert incubating_row["common_admitted"] is False
    assert incubating_row["selected_policy"] is None
    assert incubating_row["rank"] is None
    assert incubating_row["rank_score"] is None
    assert incubating_row["metrics"]["eligible"] is False
    assert incubating_row["metrics"]["evidence"] == "pending"
    assert incubating_row["metrics"]["ci_lower"] is None
    assert incubating_row["metrics"]["ci_upper"] is None


def test_founding_common_admission_is_known_before_a_policy_can_be_selected(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
):
    founder = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Progressive Founder",
    )
    _set_founding(founder["id"])

    payload = client.get("/api/arena/rebuilt").json()
    row = _row(payload, founder["slug"])
    assert payload["common_policy"] is None
    assert row["common_admitted"] is True
    assert row["selected_policy"] is None
    assert row["rank_score"] is None


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

    missing = client.get(f"/api/compare?track=rebuilt&slugs={rebuilt['slug']},missing")
    assert missing.status_code == 404
    wrong_track = client.get(f"/api/compare?track=rebuilt&slugs={rebuilt['slug']},{sample_portfolio['slug']}")
    assert wrong_track.status_code == 422


def test_rebuilt_rows_flag_stale_and_frozen_symbol_coverage(
    client,
    admin_headers,
    sample_agent,
    sample_prompt,
    monkeypatch,
):
    from app.services import massive

    portfolio = _create_rebuilt(
        client,
        admin_headers,
        sample_agent,
        sample_prompt,
        "Frozen Rebuilt",
    )
    _insert_signals(portfolio["id"], _weekdays(date(2026, 4, 1), 5), "AAPL")

    original_download = massive.download_prices

    def download_with_frozen_aapl(symbols, start, end=None):
        result = original_download(symbols, start, end)
        if result.get("AAPL"):
            result["AAPL"] = result["AAPL"][:-6]
        return result

    monkeypatch.setattr(massive, "download_prices", download_with_frozen_aapl)
    response = client.get("/api/arena/rebuilt?view=signal&horizon=1&cost_basis=gross")
    assert response.status_code == 200, response.text
    payload = response.json()
    row = _row(payload, portfolio["slug"])
    assert payload["market_data_status"] == "stale"
    assert row["stale_data"] is True
    assert row["frozen_symbols"] == ["AAPL"]
