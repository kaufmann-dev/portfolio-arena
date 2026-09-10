"""Public, version-scoped read endpoints (no auth, rate-limited)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_session
from ..models import Agent, ArenaVersion, ModelDefinition, Portfolio, Prompt, Signal
from ..ratelimit import limiter
from ..services import admin_ops
from ..services.arena import compute_rebuilt_arena, compute_valuations, load_portfolios
from ..services.market_refresh import market_snapshot
from ..services.model_catalog import agent_out
from ..services.prompt_policy import allocation_policies_out, allocation_policy_out
from ..services.rebuilt import HorizonObjective
from ..services.serialize import (
    rank_rows,
    serialize_detail,
    serialize_rebuilt_detail,
    serialize_rebuilt_summary,
    serialize_signal,
    serialize_summary,
    synthetic_spy_row,
    version_ref,
)
from ..services.valuation import point_boundary, rebase_series

router = APIRouter(prefix="/api")
Track = Literal["managed", "rebuilt"]
Direction = Literal["long", "short"]


def _version(session: Session, version_id: int) -> ArenaVersion:
    version = session.get(ArenaVersion, version_id)
    if version is None:
        raise HTTPException(404, "Arena version not found")
    return version


@router.get("/versions")
@limiter.limit("60/minute")
def list_versions(request: Request, session: Session = Depends(get_session)):
    return {
        "versions": [
            {
                "id": version.id,
                "name": version.name,
                "evaluation_enabled": version.evaluation_enabled,
                "created_at": version.created_at.isoformat(),
            }
            for version in session.scalars(
                select(ArenaVersion).order_by(ArenaVersion.created_at.desc(), ArenaVersion.id.desc())
            )
        ]
    }


@router.get("/market-data")
@limiter.limit("120/minute")
def market_data(request: Request, version_id: int, session: Session = Depends(get_session)):
    _version(session, version_id)
    snapshot = market_snapshot(session, version_id=version_id)
    return {
        "version_id": version_id,
        "as_of": snapshot.as_of,
        "target_as_of": snapshot.target_as_of,
        "market_data_status": snapshot.status,
    }


@router.get("/arena/managed")
@limiter.limit("30/minute")
def managed_arena(
    request: Request, version_id: int, direction: Direction, session: Session = Depends(get_session)
):
    _version(session, version_id)
    portfolios = [
        portfolio
        for portfolio in load_portfolios(session, version_id)
        if portfolio.prompt_mode == "managed" and portfolio.direction == direction
    ]
    allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "managed")
    valuations = compute_valuations(session, portfolios)
    rows = [
        serialize_summary(valuations.by_portfolio_id[portfolio.id], valuations, allocation_policy)
        for portfolio in portfolios
    ]
    rank_rows(rows)
    start = min(
        (row["inception"] for row in rows if row["inception"]),
        key=lambda value: value["timestamp"],
        default=None,
    )
    return {
        "version_id": version_id,
        "track": "managed",
        "direction": direction,
        "as_of": valuations.as_of,
        "market_data_status": valuations.market_data_status,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_bandwidth": "automatic",
        },
        "portfolios": [
            synthetic_spy_row(valuations.spy_series, start, direction=direction, as_of=valuations.as_of),
            *rows,
        ],
    }


@router.get("/arena/rebuilt")
@limiter.limit("30/minute")
def rebuilt_arena(
    request: Request,
    version_id: int,
    direction: Direction,
    objective: HorizonObjective = "ci_lower",
    session: Session = Depends(get_session),
):
    _version(session, version_id)
    portfolios = [
        portfolio
        for portfolio in load_portfolios(session, version_id)
        if portfolio.prompt_mode == "rebuilt" and portfolio.direction == direction
    ]
    arena = compute_rebuilt_arena(session, portfolios, objective=objective)
    allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "rebuilt")
    rows = [
        serialize_rebuilt_summary(arena.by_portfolio_id[portfolio.id], arena, allocation_policy)
        for portfolio in portfolios
    ]
    rank_rows(rows)
    start = min(
        (row["inception"] for row in rows if row["inception"]),
        key=lambda value: value["timestamp"],
        default=None,
    )
    return {
        "version_id": version_id,
        "track": "rebuilt",
        "objective": objective,
        "direction": direction,
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_lag": "ceil_holding_period_minus_one",
            "family_size": 40,
        },
        "portfolios": [
            synthetic_spy_row(arena.spy_series, start, direction=direction, as_of=arena.as_of),
            *rows,
        ],
    }


@router.get("/portfolios/{slug}")
@limiter.limit("60/minute")
def portfolio_detail(
    slug: str,
    request: Request,
    objective: HorizonObjective = "ci_lower",
    session: Session = Depends(get_session),
):
    record = session.scalar(select(Portfolio).where(Portfolio.slug == slug))
    if record is None:
        raise HTTPException(404, "Portfolio not found")
    match = next(
        portfolio for portfolio in load_portfolios(session, record.version_id) if portfolio.id == record.id
    )
    settings = admin_ops.get_app_settings(session)
    policy = allocation_policy_out(settings, match.prompt_mode)
    direction_instructions = settings[f"{match.direction}_direction_instructions"]
    wrapper = settings[f"{match.prompt_mode}_wrapper_prompt"]
    if match.prompt_mode == "managed":
        arena = compute_valuations(session, [match])
        detail = serialize_detail(
            arena.by_portfolio_id[match.id], arena, policy, direction_instructions, wrapper_prompt=wrapper
        )
    else:
        arena = compute_rebuilt_arena(session, [match], objective=objective)
        detail = serialize_rebuilt_detail(
            arena.by_portfolio_id[match.id], arena, policy, direction_instructions, wrapper_prompt=wrapper
        )
    return {
        "version_id": match.version_id,
        "track": match.prompt_mode,
        "direction": match.direction,
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "portfolio": detail,
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
    return {
        "signals": [serialize_signal(signal) for signal in signals[:limit]],
        "next_cursor": signals[limit - 1].id if len(signals) > limit else None,
    }


@router.get("/compare")
@limiter.limit("30/minute")
def compare(
    slugs: str,
    request: Request,
    version_id: int,
    track: Track,
    direction: Direction,
    objective: HorizonObjective = "ci_lower",
    session: Session = Depends(get_session),
):
    _version(session, version_id)
    wanted = list(dict.fromkeys(part.strip() for part in slugs.split(",") if part.strip()))
    if not wanted or len(wanted) > 8:
        raise HTTPException(422, "Pass 1-8 portfolio slugs.")
    by_slug = {portfolio.slug: portfolio for portfolio in load_portfolios(session, version_id)}
    if missing := [slug for slug in wanted if slug not in by_slug]:
        raise HTTPException(404, f"Portfolio not found in this version: {', '.join(missing)}")
    selected = [by_slug[slug] for slug in wanted]
    if any(portfolio.prompt_mode != track or portfolio.direction != direction for portfolio in selected):
        raise HTTPException(422, "Compared portfolios must have the requested track and direction.")
    if track == "managed":
        arena = compute_valuations(session, selected)
        results = [(portfolio, arena.by_portfolio_id[portfolio.id].result) for portfolio in selected]
    else:
        arena = compute_rebuilt_arena(session, selected, objective=objective)
        results = [(portfolio, arena.by_portfolio_id[portfolio.id].selected) for portfolio in selected]
    output = [(portfolio, result.series) for portfolio, result in results if result and result.series]
    payload = {
        **({"objective": objective} if track == "rebuilt" else {}),
        "version_id": version_id,
        "track": track,
        "direction": direction,
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "start": None,
        "series": [],
        "spy_series": [],
    }
    if not output:
        return payload
    common_timestamps = set.intersection(*({point["timestamp"] for point in series} for _, series in output))
    if not common_timestamps:
        return payload
    common_start = min(common_timestamps)
    start = point_boundary(next(point for point in output[0][1] if point["timestamp"] == common_start))
    lines = []
    for portfolio, series in output:
        window = [point for point in series if point["timestamp"] in common_timestamps]
        base = window[0]["nav"]
        if base <= 0:
            continue
        lines.append(
            {
                "slug": portfolio.slug,
                "name": portfolio.name,
                "kind": track,
                "execution_boundary": portfolio.execution_boundary,
                "series": [{**point_boundary(point), "nav": point["nav"] / base * 100} for point in window],
            }
        )
    return {
        **payload,
        "start": start,
        "series": lines,
        "spy_series": [
            point
            for point in rebase_series(arena.spy_series, start, arena.as_of, direction)
            if point["timestamp"] in common_timestamps
        ],
    }


def _portfolio_refs(portfolios: list[Portfolio]) -> list[dict]:
    return [
        {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "version_id": portfolio.version_id,
            "version": version_ref(portfolio),
            "execution_boundary": portfolio.execution_boundary,
            "prompt_mode": portfolio.prompt_mode,
            "direction": portfolio.direction,
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
    settings = admin_ops.get_app_settings(session)
    return {
        "prompts": [
            {
                "id": prompt.id,
                "slug": prompt.slug,
                "name": prompt.name,
                "mode": prompt.mode,
                "direction": prompt.direction,
                "managed_long_text": prompt.managed_long_text,
                "managed_short_text": prompt.managed_short_text,
                "rebuilt_long_text": prompt.rebuilt_long_text,
                "rebuilt_short_text": prompt.rebuilt_short_text,
                "notes": prompt.notes,
                "allocation_policies": allocation_policies_out(settings, prompt),
                "updated_at": prompt.updated_at.isoformat(),
                "portfolio_count": usage.get(prompt.id, 0),
            }
            for prompt in prompts
        ]
    }


@router.get("/prompts/{slug}")
@limiter.limit("60/minute")
def prompt_detail(slug: str, request: Request, session: Session = Depends(get_session)):
    prompt = session.scalar(
        select(Prompt).where(
            Prompt.slug == slug,
        )
    )
    if prompt is None:
        raise HTTPException(404, "Prompt not found")
    users = list(session.scalars(select(Portfolio).where(Portfolio.prompt_id == prompt.id)))
    settings = admin_ops.get_app_settings(session)
    return {
        "prompt": {
            "id": prompt.id,
            "slug": prompt.slug,
            "name": prompt.name,
            "mode": prompt.mode,
            "direction": prompt.direction,
            "managed_long_text": prompt.managed_long_text,
            "managed_short_text": prompt.managed_short_text,
            "rebuilt_long_text": prompt.rebuilt_long_text,
            "rebuilt_short_text": prompt.rebuilt_short_text,
            "notes": prompt.notes,
            "allocation_policies": allocation_policies_out(settings, prompt),
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
