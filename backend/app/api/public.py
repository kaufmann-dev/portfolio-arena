"""Public read endpoints (no auth, rate-limited)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Agent, Allocation, Portfolio, Prompt
from ..ratelimit import limiter
from ..services.arena import compute_valuations, load_portfolios
from ..services.benchmarks import ensure_benchmark_allocations
from ..services.valuation import rebase_series
from .serialize import serialize_detail, serialize_summary

router = APIRouter(prefix="/api")


def _valuations(session: Session):
    """Benchmark seeding must precede loading so new allocations are included."""
    ensure_benchmark_allocations(session)
    portfolios = load_portfolios(session)
    return portfolios, compute_valuations(session, portfolios)


@router.get("/leaderboard")
@limiter.limit("30/minute")
def leaderboard(request: Request, session: Session = Depends(get_session)):
    portfolios, valuations = _valuations(session)
    rows = [
        serialize_summary(valuations.by_portfolio_id[portfolio.id], valuations)
        for portfolio in portfolios
        if portfolio.id in valuations.by_portfolio_id
    ]
    return {"as_of": valuations.as_of, "portfolios": rows}


@router.get("/portfolios/{slug}")
@limiter.limit("60/minute")
def portfolio_detail(slug: str, request: Request, session: Session = Depends(get_session)):
    portfolios, valuations = _valuations(session)
    match = next((p for p in portfolios if p.slug == slug), None)
    if match is None:
        raise HTTPException(404, "Portfolio not found")
    valuation = valuations.by_portfolio_id.get(match.id)
    if valuation is None:
        raise HTTPException(404, "Portfolio not found")
    return {"as_of": valuations.as_of, "portfolio": serialize_detail(valuation, valuations)}


@router.get("/compare")
@limiter.limit("30/minute")
def compare(slugs: str, request: Request, session: Session = Depends(get_session)):
    """Overlaid base-100 series, rebased to the latest common inception so
    every line starts at 100 on the same day."""
    wanted = [part.strip() for part in slugs.split(",") if part.strip()]
    if not wanted or len(wanted) > 8:
        raise HTTPException(422, "Pass 1-8 portfolio slugs.")

    portfolios, valuations = _valuations(session)
    selected = [p for p in portfolios if p.slug in wanted]
    if not selected:
        raise HTTPException(404, "No matching portfolios")

    with_data = [
        (p, valuations.by_portfolio_id[p.id].result)
        for p in selected
        if valuations.by_portfolio_id.get(p.id) and valuations.by_portfolio_id[p.id].result
    ]
    with_data = [(p, r) for p, r in with_data if r.series]
    if not with_data:
        return {"as_of": valuations.as_of, "start": None, "series": []}

    common_start = max(result.series[0]["date"] for _, result in with_data)
    out = []
    for portfolio, result in with_data:
        window = [point for point in result.series if point["date"] >= common_start]
        if not window or window[0]["nav"] <= 0:
            continue
        base = window[0]["nav"]
        out.append(
            {
                "slug": portfolio.slug,
                "name": portfolio.name,
                "is_benchmark": portfolio.is_benchmark,
                "series": [{"date": point["date"], "nav": point["nav"] / base * 100.0} for point in window],
            }
        )
    spy = rebase_series(valuations.spy_series, common_start, valuations.as_of or common_start)
    return {"as_of": valuations.as_of, "start": common_start, "series": out, "spy_series": spy}


@router.get("/prompts")
@limiter.limit("60/minute")
def list_prompts(request: Request, session: Session = Depends(get_session)):
    prompts = session.scalars(select(Prompt).order_by(Prompt.slug)).all()
    usage: dict[int, set[int]] = {}
    for allocation in session.scalars(select(Allocation)):
        usage.setdefault(allocation.prompt_id, set()).add(allocation.portfolio_id)
    return {
        "prompts": [
            {
                "id": prompt.id,
                "slug": prompt.slug,
                "name": prompt.name,
                "text": prompt.text,
                "notes": prompt.notes,
                "updated_at": prompt.updated_at.isoformat(),
                "portfolio_count": len(usage.get(prompt.id, ())),
            }
            for prompt in prompts
        ]
    }


@router.get("/prompts/{slug}")
@limiter.limit("60/minute")
def prompt_detail(slug: str, request: Request, session: Session = Depends(get_session)):
    prompt = session.scalars(select(Prompt).where(Prompt.slug == slug)).first()
    if prompt is None:
        raise HTTPException(404, "Prompt not found")

    portfolios, valuations = _valuations(session)
    users = [p for p in portfolios if any(a.prompt_id == prompt.id for a in p.allocations)]
    return {
        "as_of": valuations.as_of,
        "prompt": {
            "id": prompt.id,
            "slug": prompt.slug,
            "name": prompt.name,
            "text": prompt.text,
            "notes": prompt.notes,
            "created_at": prompt.created_at.isoformat(),
            "updated_at": prompt.updated_at.isoformat(),
        },
        "portfolios": [
            serialize_summary(valuations.by_portfolio_id[p.id], valuations)
            for p in users
            if p.id in valuations.by_portfolio_id
        ],
    }


@router.get("/agents")
@limiter.limit("60/minute")
def list_agents(request: Request, session: Session = Depends(get_session)):
    agents = session.scalars(select(Agent).order_by(Agent.slug)).all()
    portfolios = session.scalars(select(Portfolio)).all()
    by_agent: dict[int, list[Portfolio]] = {}
    for portfolio in portfolios:
        by_agent.setdefault(portfolio.agent_id, []).append(portfolio)
    return {
        "agents": [
            {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "notes": agent.notes,
                "portfolios": [
                    {"id": p.id, "slug": p.slug, "name": p.name, "status": p.status}
                    for p in by_agent.get(agent.id, [])
                ],
            }
            for agent in agents
        ]
    }


@router.get("/agents/{slug}")
@limiter.limit("60/minute")
def agent_detail(slug: str, request: Request, session: Session = Depends(get_session)):
    agent = session.scalars(select(Agent).where(Agent.slug == slug)).first()
    if agent is None:
        raise HTTPException(404, "Agent not found")

    portfolios, valuations = _valuations(session)
    own = [p for p in portfolios if p.agent_id == agent.id]
    return {
        "as_of": valuations.as_of,
        "agent": {
            "id": agent.id,
            "slug": agent.slug,
            "name": agent.name,
            "notes": agent.notes,
            "created_at": agent.created_at.isoformat(),
        },
        "portfolios": [
            serialize_summary(valuations.by_portfolio_id[p.id], valuations)
            for p in own
            if p.id in valuations.by_portfolio_id
        ],
    }
