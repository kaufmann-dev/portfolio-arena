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
from ..services.market_refresh import market_snapshot
from ..services.meta import (
    control_history,
    is_meta_portfolio,
    is_normal_portfolio,
    latest_batch,
    public_batch,
    rebuilt_display,
    serialize_managed_control,
    serialize_rebuilt_control,
)
from ..services.model_catalog import agent_out
from ..services.prompt_policy import allocation_policies_out, allocation_policy_out
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
Direction = Literal["long", "short"]


@router.get("/market-data")
@limiter.limit("120/minute")
def market_data(request: Request, session: Session = Depends(get_session)):
    snapshot = market_snapshot(session)
    return {
        "as_of": snapshot.as_of,
        "target_as_of": snapshot.target_as_of,
        "market_data_status": snapshot.status,
    }


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
def managed_arena(
    request: Request,
    direction: Direction,
    session: Session = Depends(get_session),
):
    portfolios = load_portfolios(session)
    selected = [
        portfolio
        for portfolio in portfolios
        if is_normal_portfolio(portfolio)
        and portfolio.prompt_mode == "managed"
        and portfolio.direction == direction
    ]
    allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "managed")
    valuations = compute_valuations(session, selected)
    rows = [
        serialize_summary(
            valuations.by_portfolio_id[portfolio.id],
            valuations,
            allocation_policy,
        )
        for portfolio in selected
        if portfolio.id in valuations.by_portfolio_id
    ]
    rank_rows(rows)
    return {
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
            synthetic_spy_row(valuations.spy_series, direction=direction),
            *rows,
        ],
    }


@router.get("/arena/rebuilt")
@limiter.limit("30/minute")
def rebuilt_arena(
    request: Request,
    direction: Direction,
    view: RebuiltView = "common",
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    horizon: int | None = Query(default=None, ge=1, le=20),
    session: Session = Depends(get_session),
):
    _validate_rebuilt_context(view, objective, cost_basis, horizon)
    portfolios = load_portfolios(session)
    selected = [
        portfolio
        for portfolio in portfolios
        if is_normal_portfolio(portfolio)
        and portfolio.prompt_mode == "rebuilt"
        and portfolio.direction == direction
    ]
    arena = compute_rebuilt_arena(
        session,
        selected,
        view=view,
        objective=objective,
        cost_basis=cost_basis,
        horizon=horizon,
    )
    allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "rebuilt")
    rows = [
        serialize_rebuilt_summary(
            arena.by_portfolio_id[portfolio.id],
            arena,
            allocation_policy,
            view=view,
            horizon=horizon,
        )
        for portfolio in selected
        if portfolio.id in arena.by_portfolio_id
    ]
    rank_rows(rows)
    common = arena.common_for(direction)
    if view == "common" and common.spy_series:
        spy_row = synthetic_spy_row(
            common.spy_series,
            precomputed_nav=True,
            direction=direction,
        )
    else:
        spy_row = synthetic_spy_row(arena.spy_series, direction=direction)
    return {
        "track": "rebuilt",
        "direction": direction,
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "context": _context(view, objective, cost_basis, horizon),
        "common_policy": common.policy,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_lag": "holding_period_minus_one",
        },
        "portfolios": [spy_row, *rows],
    }


@router.get("/meta/managed")
@limiter.limit("30/minute")
def managed_meta_arena(
    request: Request,
    direction: Direction,
    session: Session = Depends(get_session),
):
    portfolios = load_portfolios(session)
    selected = [
        portfolio
        for portfolio in portfolios
        if is_meta_portfolio(portfolio)
        and portfolio.prompt_mode == "managed"
        and portfolio.direction == direction
    ]
    control_portfolio, control_session = control_history(session, "managed", direction)
    valuation_inputs = [*selected, *([control_portfolio] if control_portfolio else [])]
    valuations = compute_valuations(session, valuation_inputs)
    allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "managed")
    rows = [
        serialize_summary(
            valuations.by_portfolio_id[portfolio.id],
            valuations,
            allocation_policy,
        )
        for portfolio in selected
        if portfolio.id in valuations.by_portfolio_id
    ]
    rank_rows(rows)
    control = serialize_managed_control(
        valuations.by_portfolio_id.get(control_portfolio.id) if control_portfolio else None,
        valuations,
        control_session,
    )
    return {
        "track": "managed",
        "direction": direction,
        "as_of": valuations.as_of,
        "market_data_status": valuations.market_data_status,
        "batch": public_batch(latest_batch(session)),
        "control": control,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_bandwidth": "automatic",
        },
        "portfolios": [
            synthetic_spy_row(valuations.spy_series, direction=direction),
            *([control] if control else []),
            *rows,
        ],
    }


@router.get("/meta/rebuilt")
@limiter.limit("30/minute")
def rebuilt_meta_arena(
    request: Request,
    direction: Direction,
    view: RebuiltView = "common",
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    horizon: int | None = Query(default=None, ge=1, le=20),
    session: Session = Depends(get_session),
):
    _validate_rebuilt_context(view, objective, cost_basis, horizon)
    portfolios = load_portfolios(session)
    sources = [
        portfolio
        for portfolio in portfolios
        if is_normal_portfolio(portfolio)
        and portfolio.prompt_mode == "rebuilt"
        and portfolio.direction == direction
    ]
    selected = [
        portfolio
        for portfolio in portfolios
        if is_meta_portfolio(portfolio)
        and portfolio.prompt_mode == "rebuilt"
        and portfolio.direction == direction
    ]
    control_portfolio, control_session = control_history(session, "rebuilt", direction)
    analysis_inputs = [
        *sources,
        *selected,
        *([control_portfolio] if control_portfolio else []),
    ]
    arena = compute_rebuilt_arena(
        session,
        analysis_inputs,
        view=view,
        objective=objective,
        cost_basis=cost_basis,
        horizon=horizon,
        common_source_ids={portfolio.id for portfolio in sources},
    )
    allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "rebuilt")
    rows = [
        serialize_rebuilt_summary(
            arena.by_portfolio_id[portfolio.id],
            arena,
            allocation_policy,
            view=view,
            horizon=horizon,
        )
        for portfolio in selected
        if portfolio.id in arena.by_portfolio_id
    ]
    rank_rows(rows)
    control = serialize_rebuilt_control(
        arena.by_portfolio_id.get(control_portfolio.id) if control_portfolio else None,
        arena,
        control_session,
        view=view,
        horizon=horizon,
    )
    common = arena.common_for(direction)
    if view == "common" and common.spy_series:
        spy_row = synthetic_spy_row(common.spy_series, precomputed_nav=True, direction=direction)
    else:
        spy_row = synthetic_spy_row(arena.spy_series, direction=direction)
    return {
        "track": "rebuilt",
        "direction": direction,
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "batch": public_batch(latest_batch(session)),
        "control": control,
        "context": _context(view, objective, cost_basis, horizon),
        "common_policy": common.policy,
        "ranking": {
            "metric": "search_adjusted_lower_95_ci",
            "alpha": "daily_excess_vs_spy",
            "hac_lag": "holding_period_minus_one",
        },
        "portfolios": [spy_row, *([control] if control else []), *rows],
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
    settings = admin_ops.get_app_settings(session)
    wrapper = settings[f"{match.prompt_mode}_wrapper_prompt"]
    direction_instructions = settings[f"{match.direction}_direction_instructions"]
    allocation_policy = allocation_policy_out(settings, match.prompt_mode)
    if selected_track == "managed":
        if view != "common" or objective != "canonical" or cost_basis != "net" or horizon is not None:
            raise HTTPException(422, "Rebuilt analysis context is not valid for managed portfolios.")
        valuations = compute_valuations(session, [match])
        valuation = valuations.by_portfolio_id.get(match.id)
        if valuation is None:
            raise HTTPException(404, "Portfolio not found")
        detail = serialize_detail(
            valuation,
            valuations,
            allocation_policy,
            direction_instructions,
            wrapper_prompt=wrapper,
        )
        if is_meta_portfolio(match):
            detail["execution_prompt"] = None
            detail["execution_context_notice"] = (
                "Arena synthesis context is supplied only by the integrated evaluator; "
                "the public execution prompt does not include source reasoning."
            )
        return {
            "track": "managed",
            "direction": match.direction,
            "as_of": valuations.as_of,
            "market_data_status": valuations.market_data_status,
            "context": None,
            "portfolio": detail,
        }

    _validate_rebuilt_context(view, objective, cost_basis, horizon)
    normal_same_direction = [
        portfolio
        for portfolio in portfolios
        if is_normal_portfolio(portfolio)
        and portfolio.prompt_mode == "rebuilt"
        and portfolio.direction == match.direction
    ]
    same_direction = normal_same_direction
    common_source_ids = None
    if is_meta_portfolio(match):
        same_direction = [*normal_same_direction, match]
        common_source_ids = {portfolio.id for portfolio in normal_same_direction}
    arena = compute_rebuilt_arena(
        session,
        same_direction,
        view=view,
        objective=objective,
        cost_basis=cost_basis,
        horizon=horizon,
        include_policy_matrix=True,
        common_source_ids=common_source_ids,
    )
    analysis = arena.by_portfolio_id.get(match.id)
    if analysis is None:
        raise HTTPException(404, "Portfolio not found")
    detail = serialize_rebuilt_detail(
        analysis,
        arena,
        allocation_policy,
        direction_instructions,
        view=view,
        horizon=horizon,
        wrapper_prompt=wrapper,
    )
    if is_meta_portfolio(match):
        detail["execution_prompt"] = None
        detail["execution_context_notice"] = (
            "Arena synthesis context is supplied only by the integrated evaluator; "
            "the public execution prompt does not include source reasoning."
        )
    return {
        "track": "rebuilt",
        "direction": match.direction,
        "as_of": arena.as_of,
        "market_data_status": arena.market_data_status,
        "context": _context(view, objective, cost_basis, horizon),
        "common_policy": arena.common_for(match.direction).policy,
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
    direction: Direction,
    view: RebuiltView = "common",
    objective: Objective = "canonical",
    cost_basis: CostBasis = "net",
    horizon: int | None = Query(default=None, ge=1, le=20),
    session: Session = Depends(get_session),
):
    wanted = list(dict.fromkeys(part.strip() for part in slugs.split(",") if part.strip()))
    if not wanted or len(wanted) > 8:
        raise HTTPException(422, "Pass 1-8 portfolio slugs.")
    portfolios = [portfolio for portfolio in load_portfolios(session) if is_normal_portfolio(portfolio)]
    by_slug = {portfolio.slug: portfolio for portfolio in portfolios}
    missing = [slug for slug in wanted if slug not in by_slug]
    if missing:
        raise HTTPException(404, f"Portfolio not found: {', '.join(missing)}")
    wrong_track = [slug for slug in wanted if by_slug[slug].prompt_mode != track]
    if wrong_track:
        raise HTTPException(422, f"Portfolio belongs to the other track: {', '.join(wrong_track)}")
    wrong_direction = [slug for slug in wanted if by_slug[slug].direction != direction]
    if wrong_direction:
        raise HTTPException(
            422,
            f"Portfolio belongs to the other direction: {', '.join(wrong_direction)}",
        )
    selected = [by_slug[slug] for slug in wanted]
    direction_universe = [
        portfolio
        for portfolio in portfolios
        if portfolio.prompt_mode == track and portfolio.direction == direction
    ]

    output = []
    spy_raw = []
    as_of = None
    status = "fresh"
    context = None
    spy_is_precomputed = False
    if track == "managed":
        if view != "common" or objective != "canonical" or cost_basis != "net" or horizon is not None:
            raise HTTPException(422, "Rebuilt analysis context is not valid for managed comparisons.")
        valuations = compute_valuations(session, direction_universe)
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
            direction_universe,
            view=view,
            objective=objective,
            cost_basis=cost_basis,
            horizon=horizon,
        )
        as_of = arena.as_of
        status = arena.market_data_status
        common = arena.common_for(direction)
        if view == "common" and common.spy_series:
            spy_raw = common.spy_series
            spy_is_precomputed = True
        else:
            spy_raw = arena.spy_series
        for portfolio in selected:
            analysis = arena.by_portfolio_id[portfolio.id]
            allocation_policy = allocation_policy_out(admin_ops.get_app_settings(session), "rebuilt")
            summary = serialize_rebuilt_summary(
                analysis,
                arena,
                allocation_policy,
                view=view,
                horizon=horizon,
            )
            policy = summary["selected_policy"]
            if policy:
                if view == "common":
                    series = common.member_series.get(portfolio.id, [])
                else:
                    result = analysis.policies[(policy["horizon"], policy["exposure_pct"])]
                    series = result.series
                if series:
                    output.append((portfolio, series))

    if not output:
        return {
            "track": track,
            "direction": direction,
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
        else rebase_series(
            spy_raw,
            common_start,
            as_of or common_start,
            direction=direction,
        )
    )
    return {
        "track": track,
        "direction": direction,
        "as_of": as_of,
        "market_data_status": status,
        "context": context,
        "start": common_start,
        "series": lines,
        "spy_series": spy_output,
    }


@router.get("/meta/compare")
@limiter.limit("30/minute")
def compare_meta(
    slugs: str,
    request: Request,
    track: Track,
    direction: Direction,
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
    meta_portfolios = [portfolio for portfolio in portfolios if is_meta_portfolio(portfolio)]
    by_slug = {portfolio.slug: portfolio for portfolio in meta_portfolios}
    missing = [slug for slug in wanted if slug not in by_slug]
    if missing:
        raise HTTPException(404, f"Meta portfolio not found: {', '.join(missing)}")
    wrong_track = [slug for slug in wanted if by_slug[slug].prompt_mode != track]
    if wrong_track:
        raise HTTPException(422, f"Portfolio belongs to the other track: {', '.join(wrong_track)}")
    wrong_direction = [slug for slug in wanted if by_slug[slug].direction != direction]
    if wrong_direction:
        raise HTTPException(
            422,
            f"Portfolio belongs to the other direction: {', '.join(wrong_direction)}",
        )
    selected = [by_slug[slug] for slug in wanted]
    meta_universe = [
        portfolio
        for portfolio in meta_portfolios
        if portfolio.prompt_mode == track and portfolio.direction == direction
    ]
    control_portfolio, control_session = control_history(session, track, direction)

    output: list[tuple[Portfolio, list[dict]]] = []
    control = None
    control_raw: list[dict] = []
    spy_raw = []
    as_of = None
    status = "fresh"
    context = None
    spy_is_precomputed = False
    if track == "managed":
        if view != "common" or objective != "canonical" or cost_basis != "net" or horizon is not None:
            raise HTTPException(422, "Rebuilt analysis context is not valid for managed comparisons.")
        inputs = [*meta_universe, *([control_portfolio] if control_portfolio else [])]
        valuations = compute_valuations(session, inputs)
        as_of = valuations.as_of
        status = valuations.market_data_status
        spy_raw = valuations.spy_series
        for portfolio in selected:
            result = valuations.by_portfolio_id[portfolio.id].result
            if result and result.series:
                output.append((portfolio, result.series))
        control_valuation = (
            valuations.by_portfolio_id.get(control_portfolio.id) if control_portfolio else None
        )
        control = serialize_managed_control(control_valuation, valuations, control_session)
        if control_valuation and control_valuation.result:
            control_raw = control_valuation.result.series
    else:
        _validate_rebuilt_context(view, objective, cost_basis, horizon)
        context = _context(view, objective, cost_basis, horizon)
        sources = [
            portfolio
            for portfolio in portfolios
            if is_normal_portfolio(portfolio)
            and portfolio.prompt_mode == "rebuilt"
            and portfolio.direction == direction
        ]
        inputs = [*sources, *meta_universe, *([control_portfolio] if control_portfolio else [])]
        arena = compute_rebuilt_arena(
            session,
            inputs,
            view=view,
            objective=objective,
            cost_basis=cost_basis,
            horizon=horizon,
            common_source_ids={portfolio.id for portfolio in sources},
        )
        as_of = arena.as_of
        status = arena.market_data_status
        common = arena.common_for(direction)
        if view == "common" and common.spy_series:
            spy_raw = common.spy_series
            spy_is_precomputed = True
        else:
            spy_raw = arena.spy_series
        for portfolio in selected:
            analysis = arena.by_portfolio_id[portfolio.id]
            _, _, series, _ = rebuilt_display(analysis, arena, view=view, horizon=horizon)
            if series:
                output.append((portfolio, series))
        control_analysis = arena.by_portfolio_id.get(control_portfolio.id) if control_portfolio else None
        control = serialize_rebuilt_control(
            control_analysis,
            arena,
            control_session,
            view=view,
            horizon=horizon,
        )
        if control_analysis:
            _, _, control_raw, _ = rebuilt_display(
                control_analysis,
                arena,
                view=view,
                horizon=horizon,
            )

    empty = {
        "track": track,
        "direction": direction,
        "as_of": as_of,
        "market_data_status": status,
        "batch": public_batch(latest_batch(session)),
        "context": context,
        "start": None,
        "series": [],
        "control_series": None,
        "spy_series": [],
    }
    if not output:
        return empty

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
    control_line = None
    control_window = [point for point in control_raw if point["date"] >= common_start]
    if control and control_window and control_window[0]["nav"] > 0:
        control_base = control_window[0]["nav"]
        control_line = {
            "slug": control["slug"],
            "name": control["name"],
            "kind": "control",
            "series": [
                {"date": point["date"], "nav": point["nav"] / control_base * 100.0}
                for point in control_window
            ],
        }
    spy_output = (
        [
            {"date": point["date"], "nav": point["nav"]}
            for point in spy_raw
            if common_start <= point["date"] <= (as_of or common_start)
        ]
        if spy_is_precomputed
        else rebase_series(spy_raw, common_start, as_of or common_start, direction=direction)
    )
    return {
        **empty,
        "start": common_start,
        "series": lines,
        "control_series": control_line,
        "spy_series": spy_output,
    }


def _portfolio_refs(portfolios: list[Portfolio]) -> list[dict]:
    return [
        {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "status": portfolio.status,
            "context_scope": portfolio.prompt.context_scope,
            "prompt_mode": portfolio.prompt_mode,
            "direction": portfolio.direction,
        }
        for portfolio in portfolios
    ]


@router.get("/prompts")
@limiter.limit("60/minute")
def list_prompts(request: Request, session: Session = Depends(get_session)):
    prompts = session.scalars(select(Prompt).where(Prompt.status == "active").order_by(Prompt.slug)).all()
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
                "context_scope": prompt.context_scope,
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
            Prompt.status == "active",
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
            "context_scope": prompt.context_scope,
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
