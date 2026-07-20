"""Server-owned automation leases, cutoffs, retries, submission, and history."""

from datetime import date, timedelta

import pytest

from app.services import admin_ops
from app.services.admin_ops import AdminOpError
from app.services.trading_calendar import close_at

SCHEDULED_FOR = date(2026, 7, 20)


def _inside_window():
    return close_at(SCHEDULED_FOR) - timedelta(minutes=60)


def _begin(session, portfolio, now=None):
    return admin_ops.begin_evaluation_run(
        session,
        portfolio_slug=portfolio["slug"],
        scheduled_for=SCHEDULED_FOR,
        model="gpt-5.6-sol",
        codex_version="codex-cli 0.144.5",
        now=now or _inside_window(),
    )


def test_evaluation_submission_is_atomic_and_idempotent(sample_portfolio):
    from app.db import session_factory

    with session_factory()() as session:
        begun = _begin(session, sample_portfolio)
        assert begun["action"] == "run"
        assert begun["run"]["attempt_count"] == 1

        submitted = admin_ops.submit_evaluation_allocation(
            session,
            run_id=begun["run"]["id"],
            positions=[
                {"symbol": "AAPL", "weight_pct": 55, "note": "consumer resilience"},
                {"symbol": "MSFT", "weight_pct": 45, "note": "cloud growth"},
            ],
            note="Automated rebalance",
            report="Both theses remain intact.",
            now=_inside_window() + timedelta(minutes=5),
        )
        assert submitted["run"]["status"] == "succeeded"
        assert submitted["allocation"]["effective_date"] == SCHEDULED_FOR.isoformat()

        repeated = _begin(session, sample_portfolio, _inside_window() + timedelta(minutes=6))
        assert repeated["action"] == "skip"
        assert repeated["run"]["allocation_id"] == submitted["allocation"]["id"]


def test_evaluation_allows_two_attempts_then_exhausts(sample_portfolio):
    from app.db import session_factory

    with session_factory()() as session:
        first = _begin(session, sample_portfolio)
        admin_ops.fail_evaluation_run(session, run_id=first["run"]["id"], error="first")
        second = _begin(session, sample_portfolio, _inside_window() + timedelta(minutes=1))
        assert second["action"] == "run"
        assert second["run"]["attempt_count"] == 2
        admin_ops.fail_evaluation_run(session, run_id=second["run"]["id"], error="second")
        exhausted = _begin(session, sample_portfolio, _inside_window() + timedelta(minutes=2))
        assert exhausted["action"] == "exhausted"


def test_evaluation_window_is_server_enforced(sample_portfolio):
    from app.db import session_factory

    with session_factory()() as session:
        with pytest.raises(AdminOpError, match="opens"):
            _begin(session, sample_portfolio, close_at(SCHEDULED_FOR) - timedelta(minutes=91))
        with pytest.raises(AdminOpError, match="cutoff"):
            _begin(session, sample_portfolio, close_at(SCHEDULED_FOR) - timedelta(minutes=10))


def test_evaluation_history_is_admin_only_and_cursor_paginated(client, admin_headers, sample_portfolio):
    from app.db import session_factory

    with session_factory()() as session:
        begun = _begin(session, sample_portfolio)
        admin_ops.fail_evaluation_run(session, run_id=begun["run"]["id"], error="test failure")

    assert client.get("/api/evaluation-runs").status_code == 401
    first = client.get("/api/evaluation-runs?limit=1", headers=admin_headers)
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["items"][0]["status"] == "failed"
    assert payload["items"][0]["error"] == "test failure"
    assert payload["next_cursor"] is None


def test_evaluation_rejects_policy_violating_proposal(sample_portfolio):
    from app.db import session_factory

    with session_factory()() as session:
        portfolio = admin_ops.writable_portfolio(session, sample_portfolio["id"])
        portfolio.prompt.min_position_weight_pct = 40
        portfolio.prompt.max_position_weight_pct = 60
        session.commit()
        begun = _begin(session, sample_portfolio)
        with pytest.raises(AdminOpError, match="between 40% and 60%"):
            admin_ops.submit_evaluation_allocation(
                session,
                run_id=begun["run"]["id"],
                positions=[
                    {"symbol": "AAPL", "weight_pct": 70, "note": ""},
                    {"symbol": "MSFT", "weight_pct": 30, "note": ""},
                ],
                note="bad",
                report="bad",
                now=_inside_window() + timedelta(minutes=1),
            )
