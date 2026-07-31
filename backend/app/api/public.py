"""Public read endpoints (no auth, rate-limited)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import Agent, ModelDefinition, Portfolio, Prompt, Signal
from ..ratelimit import limiter
from ..services import admin_ops
from ..services.arena import compute_rebuilt_arena, compute_valuations, load_portfolios
from ..services.model_catalog import agent_out
from ..services.prompt_policy import allocation_policy_out
from ..services.serialize import (
    rank_rows,
    serialize_detail,
    serialize_rebuilt_detail,
    serialize_rebuilt_summary,
    serialize_signal,
    serialize_summary,
    synthetic_spy_row,
)
from ..services.valuation import rebase_series

router = APIRouter(prefix="/api")
RebuiltView = Literal["common", "tuned", "signal"]
Objective = Literal["canonical", "max_alpha", "max_information_ratio", "max_sharpe"]
CostBasis = Literal["net", "gross"]
Track = Literal["managed", "rebuilt"]


def _validate_rebuilt_context(
    view: RebuiltView,
    objective: Objective,
    cost_basis: CostBasis,
    horizon: int | None,
) -> None:
    if view == "signal":
        if horizon is None:
            raise HTTPException(422, "Signal view requires horizon=1..20.")
        if objective != "canonical":
            raise HTTPException(422, "Signal view supports objective=canonical only.")
        if cost_basis != "gross":
            raise HTTPException(422, "Signal alpha is cost-independent; use cost_basis=gross.")
    elif horizon is not None:
        raise HTTPException(422, "horizon is valid only for signal view.")


def _context(
    view: RebuiltView,
    objective: Objective,
    cost_basis: CostBasis,
    horizon: int | None,
) -> dict:
    return {
        "view": view,
        "objective": objective,
        "cost_basis": cost_basis,
        "horizon": horizon,
    }


@router.get("/arena/managed")
@limiter.limit("30/minute")
def managed_arena(request: Request, session: Session = Depends(get_session)):
    portfolios = load_portfolios(session)
    valuations = compute_valuations(session, portfolios)
    rows = [
        serialize_summary(valuations.by_portfolio_id[portfolio.id], valuations)
        for portfolio in portfolios
        if portfolio.prompt_mode == "managed" and portfolio.id in valuations.by_portfolio_id
    ]
    rank_rows(rows)
    return {
        "track": "managed",
        "as_of": valuations.as_of,
        "market_data_status": valuations.market_data_status,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_bandwidth": "automatic",
        },
        "portfolios": [synthetic_spy_row(valuations.spy_series), *rows],
    }


@router.get("/arena/rebuilt")
@limiter.limit("30/minute")
def rebuilt_arena(
    request: Request,
    view: RebuiltView = "common",
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    horizon: int | None = Query(default=None, ge=1, le=20),
    session: Session = Depends(get_session),
):
    _validate_rebuilt_context(view, objective, cost_basis, horizon)
    portfolios = load_portfolios(session)
    arena = compute_rebuilt_arena(
        session,
        portfolios,
        objective=objective,
        cost_basis=cost_basis,
    )
    rows = [
        serialize_rebuilt_summary(
            arena.by_portfolio_id[portfolio.id],
            arena,
            view=view,
            horizon=horizon,
        )
        for portfolio in portfolios
        if portfolio.prompt_mode == "rebuilt" and portfolio.id in arena.by_portfolio_id
    ]
    rank_rows(rows)
    if view == "common" and arena.common_spy_series:
        spy_row = synthetic_spy_row(arena.common_spy_series, precomputed_nav=True)
    else:
        spy_row = synthetic_spy_row(arena.spy_series)
    return {
        "track": "rebuilt",
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "context": _context(view, objective, cost_basis, horizon),
        "common_policy": arena.common_policy,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_lag": "holding_period_minus_one",
        },
        "portfolios": [spy_row, *rows],
    }


@router.get("/portfolios/{slug}")
@limiter.limit("60/minute")
def portfolio_detail(
    slug: str,
    request: Request,
    track: Track | None = None,
    view: RebuiltView = "common",
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    horizon: int | None = Query(default=None, ge=1, le=20),
    session: Session = Depends(get_session),
):
    portfolios = load_portfolios(session)
    match = next((portfolio for portfolio in portfolios if portfolio.slug == slug), None)
    if match is None:
        raise HTTPException(404, "Portfolio not found")
    selected_track = track or match.prompt_mode
    if selected_track != match.prompt_mode:
        raise HTTPException(422, f"{match.name} belongs to the {match.prompt_mode} track.")
    wrapper = admin_ops.wrapper_prompt_for_portfolio(session, match)
    if selected_track == "managed":
        if view != "common" or objective != "canonical" or cost_basis != "net" or horizon is not None:
            raise HTTPException(422, "Rebuilt analysis context is not valid for managed portfolios.")
        valuations = compute_valuations(session, portfolios)
        valuation = valuations.by_portfolio_id.get(match.id)
        if valuation is None:
            raise HTTPException(404, "Portfolio not found")
        return {
            "track": "managed",
            "as_of": valuations.as_of,
            "market_data_status": valuations.market_data_status,
            "context": None,
            "portfolio": serialize_detail(
                valuation,
                valuations,
                wrapper_prompt=wrapper,
            ),
        }

    _validate_rebuilt_context(view, objective, cost_basis, horizon)
    arena = compute_rebuilt_arena(
        session,
        portfolios,
        objective=objective,
        cost_basis=cost_basis,
    )
    analysis = arena.by_portfolio_id.get(match.id)
    if analysis is None:
        raise HTTPException(404, "Portfolio not found")
    return {
        "track": "rebuilt",
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "context": _context(view, objective, cost_basis, horizon),
        "common_policy": arena.common_policy,
        "portfolio": serialize_rebuilt_detail(
            analysis,
            arena,
            view=view,
            horizon=horizon,
            wrapper_prompt=wrapper,
        ),
    }


@router.get("/portfolios/{slug}/signals")
@limiter.limit("60/minute")
def signal_history(
    slug: str,
    request: Request,
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
):
    portfolio = session.scalar(select(Portfolio).where(Portfolio.slug == slug))
    if portfolio is None or portfolio.prompt_mode != "rebuilt":
        raise HTTPException(404, "Rebuilt portfolio not found")
    query = (
        select(Signal)
        .where(Signal.portfolio_id == portfolio.id)
        .options(selectinload(Signal.positions))
        .order_by(Signal.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(Signal.id < cursor)
    signals = list(session.scalars(query))
    has_more = len(signals) > limit
    page = signals[:limit]
    return {
        "signals": [serialize_signal(signal) for signal in page],
        "next_cursor": page[-1].id if has_more and page else None,
    }


@router.get("/compare")
@limiter.limit("30/minute")
def compare(
    slugs: str,
    request: Request,
    track: Track,
    view: RebuiltView = "common",
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    horizon: int | None = Query(default=None, ge=1, le=20),
    session: Session = Depends(get_session),
):
    wanted = list(dict.fromkeys(part.strip() for part in slugs.split(",") if part.strip()))
    if not wanted or len(wanted) > 8:
        raise HTTPException(422, "Pass 1-8 portfolio slugs.")
    portfolios = load_portfolios(session)
    by_slug = {portfolio.slug: portfolio for portfolio in portfolios}
    missing = [slug for slug in wanted if slug not in by_slug]
    if missing:
        raise HTTPException(404, f"Portfolio not found: {', '.join(missing)}")
    wrong_track = [slug for slug in wanted if by_slug[slug].prompt_mode != track]
    if wrong_track:
        raise HTTPException(422, f"Portfolio belongs to the other track: {', '.join(wrong_track)}")
    selected = [by_slug[slug] for slug in wanted]

    output = []
    spy_raw = []
    as_of = None
    status = "fresh"
    context = None
    spy_is_precomputed = False
    if track == "managed":
        if view != "common" or objective != "canonical" or cost_basis != "net" or horizon is not None:
            raise HTTPException(422, "Rebuilt analysis context is not valid for managed comparisons.")
        valuations = compute_valuations(session, portfolios)
        as_of = valuations.as_of
        status = valuations.market_data_status
        spy_raw = valuations.spy_series
        for portfolio in selected:
            result = valuations.by_portfolio_id[portfolio.id].result
            if result and result.series:
                output.append((portfolio, result.series))
    else:
        _validate_rebuilt_context(view, objective, cost_basis, horizon)
        context = _context(view, objective, cost_basis, horizon)
        arena = compute_rebuilt_arena(
            session,
            portfolios,
            objective=objective,
            cost_basis=cost_basis,
        )
        as_of = arena.as_of
        status = arena.market_data_status
        if view == "common" and arena.common_spy_series:
            spy_raw = arena.common_spy_series
            spy_is_precomputed = True
        else:
            spy_raw = arena.spy_series
        for portfolio in selected:
            analysis = arena.by_portfolio_id[portfolio.id]
            summary = serialize_rebuilt_summary(analysis, arena, view=view, horizon=horizon)
            policy = summary["selected_policy"]
            if policy:
                if view == "common":
                    series = arena.common_series_by_portfolio_id.get(portfolio.id, [])
                else:
                    result = analysis.policies[(policy["horizon"], policy["exposure_pct"])]
                    series = result.series
                if series:
                    output.append((portfolio, series))

    if not output:
        return {
            "track": track,
            "as_of": as_of,
            "market_data_status": status,
            "context": context,
            "start": None,
            "series": [],
            "spy_series": [],
        }
    common_start = max(series[0]["date"] for _, series in output)
    lines = []
    for portfolio, series in output:
        window = [point for point in series if point["date"] >= common_start]
        if not window or window[0]["nav"] <= 0:
            continue
        base = window[0]["nav"]
        lines.append(
            {
                "slug": portfolio.slug,
                "name": portfolio.name,
                "kind": track,
                "series": [{"date": point["date"], "nav": point["nav"] / base * 100.0} for point in window],
            }
        )
    spy_output = (
        [
            {"date": point["date"], "nav": point["nav"]}
            for point in spy_raw
            if common_start <= point["date"] <= (as_of or common_start)
        ]
        if spy_is_precomputed
        else rebase_series(spy_raw, common_start, as_of or common_start)
    )
    return {
        "track": track,
        "as_of": as_of,
        "market_data_status": status,
        "context": context,
        "start": common_start,
        "series": lines,
        "spy_series": spy_output,
    }


def _portfolio_refs(portfolios: list[Portfolio]) -> list[dict]:
    return [
        {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "status": portfolio.status,
            "prompt_mode": portfolio.prompt_mode,
        }
        for portfolio in portfolios
    ]


@router.get("/prompts")
@limiter.limit("60/minute")
def list_prompts(request: Request, session: Session = Depends(get_session)):
    prompts = session.scalars(select(Prompt).order_by(Prompt.slug)).all()
    portfolios = list(session.scalars(select(Portfolio)))
    usage: dict[int, int] = {}
    for portfolio in portfolios:
        usage[portfolio.prompt_id] = usage.get(portfolio.prompt_id, 0) + 1
    return {
        "prompts": [
            {
                "id": prompt.id,
                "slug": prompt.slug,
                "name": prompt.name,
                "text": prompt.text,
                "notes": prompt.notes,
                "allocation_policy": allocation_policy_out(prompt),
                "updated_at": prompt.updated_at.isoformat(),
                "portfolio_count": usage.get(prompt.id, 0),
            }
            for prompt in prompts
        ]
    }


@router.get("/prompts/{slug}")
@limiter.limit("60/minute")
def prompt_detail(slug: str, request: Request, session: Session = Depends(get_session)):
    prompt = session.scalar(select(Prompt).where(Prompt.slug == slug))
    if prompt is None:
        raise HTTPException(404, "Prompt not found")
    users = list(session.scalars(select(Portfolio).where(Portfolio.prompt_id == prompt.id)))
    return {
        "prompt": {
            "id": prompt.id,
            "slug": prompt.slug,
            "name": prompt.name,
            "text": prompt.text,
            "notes": prompt.notes,
            "allocation_policy": allocation_policy_out(prompt),
            "created_at": prompt.created_at.isoformat(),
            "updated_at": prompt.updated_at.isoformat(),
        },
        "portfolios": _portfolio_refs(users),
    }


@router.get("/agents")
@limiter.limit("60/minute")
def list_agents(request: Request, session: Session = Depends(get_session)):
    agents = session.scalars(
        select(Agent)
        .options(selectinload(Agent.model).selectinload(ModelDefinition.capabilities))
        .order_by(Agent.slug)
    ).all()
    portfolios = list(session.scalars(select(Portfolio)))
    by_agent: dict[int, list[Portfolio]] = {}
    for portfolio in portfolios:
        by_agent.setdefault(portfolio.agent_id, []).append(portfolio)
    return {
        "agents": [
            {
                **agent_out(agent),
                "portfolios": _portfolio_refs(by_agent.get(agent.id, [])),
            }
            for agent in agents
        ]
    }


@router.get("/agents/{slug}")
@limiter.limit("60/minute")
def agent_detail(slug: str, request: Request, session: Session = Depends(get_session)):
    agent = session.scalar(
        select(Agent)
        .where(Agent.slug == slug)
        .options(selectinload(Agent.model).selectinload(ModelDefinition.capabilities))
    )
    if agent is None:
        raise HTTPException(404, "Agent not found")
    own = list(session.scalars(select(Portfolio).where(Portfolio.agent_id == agent.id)))
    return {
        "agent": {**agent_out(agent), "created_at": agent.created_at.isoformat()},
        "portfolios": _portfolio_refs(own),
    }
