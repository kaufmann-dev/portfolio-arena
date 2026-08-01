"""MCP tools for the operational arena surface.

API-key management and archived prompt recovery remain browser-admin-only.
Reads use the shared serializers; writes call ``services.admin_ops`` so every
integrity rule is enforced exactly as it is for the REST admin panel.

Each tool opens its own session (FastMCP runs sync tools in a worker thread).
``AdminOpError`` / ``SymbolValidationError`` are surfaced as tool errors.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import session_factory
from ..models import Agent, ModelDefinition, Portfolio, Prompt
from ..schemas import AllocationPolicyIn, PositionIn
from ..services import admin_ops, evaluator
from ..services.admin_ops import AdminOpError
from ..services.arena import compute_rebuilt_arena, compute_valuations, load_portfolios
from ..services.harnesses import harnesses_out
from ..services.model_catalog import agent_out
from ..services.serialize import (
    rank_rows,
    serialize_rebuilt_summary,
    serialize_summary,
    synthetic_spy_row,
)
from ..services.symbols import SymbolValidationError, resolve_symbol, search_symbols_allowed
from ..services.trading_calendar import effective_date_for
from .server import mcp


@contextmanager
def _session() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AdminOpError as exc:
        raise ValueError(exc.message) from None


def _positions(positions: list[PositionIn]) -> list[dict]:
    return [{"symbol": p.symbol, "weight_pct": p.weight_pct, "note": p.note} for p in positions]


def _direction(value: str) -> str:
    if value not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    return value


def _resolve_portfolio(session: Session, slug_or_id: str) -> Portfolio:
    text = str(slug_or_id).strip()
    portfolio = None
    if text.isdigit():
        portfolio = session.get(Portfolio, int(text))
    if portfolio is None:
        portfolio = session.scalars(select(Portfolio).where(Portfolio.slug == text)).first()
    if portfolio is None:
        raise ValueError(f"Portfolio '{slug_or_id}' not found")
    return portfolio


# --- Flagship reads ---------------------------------------------------------


@mcp.tool()
def get_portfolio(slug_or_id: str) -> dict:
    """Everything needed to evaluate ONE portfolio. Managed mode includes
    drifted holdings, notes, allocation history, performance, and costs. Rebuilt
    mode intentionally excludes all prior portfolio state. Both modes include
    only the applicable mode-specific strategy text, allocation policy, prompt
    mode, and next effective date. Accepts a slug or a numeric id."""
    with _session() as session:
        portfolio = _resolve_portfolio(session, slug_or_id)
        settings = admin_ops.get_app_settings(session)
        if portfolio.prompt_mode == "rebuilt":
            now = datetime.now(UTC)
            return {
                "as_of": None,
                "market_data_status": None,
                "portfolio": {
                    "id": portfolio.id,
                    "slug": portfolio.slug,
                    "name": portfolio.name,
                    "agent": agent_out(portfolio.agent),
                    "prompt": _portfolio_prompt_out(
                        portfolio.prompt,
                        portfolio.prompt_mode,
                        portfolio.direction,
                        settings,
                    ),
                    "prompt_mode": "rebuilt",
                    "direction": portfolio.direction,
                    "status": portfolio.status,
                    "next_entry": {
                        "entered_at": now.isoformat(),
                        "effective_date": effective_date_for(now).isoformat(),
                    },
                },
            }
        portfolio_id = portfolio.id
        detail = _guard(admin_ops.portfolio_admin_detail, session, portfolio_id)
        payload = detail["portfolio"]
        # Drop presentation data and the caller-facing manual prompt. Automated
        # and manual callers already receive their own execution instructions.
        for key in ("execution_prompt", "series", "spy_series", "sparkline", "stale_days"):
            payload.pop(key, None)
        prompt_payload = payload.get("prompt")
        prompt_id = prompt_payload.get("id") if prompt_payload else None
        prompt = session.get(Prompt, prompt_id) if prompt_id is not None else None
        if prompt is not None:
            payload["prompt"] = _portfolio_prompt_out(
                prompt, portfolio.prompt_mode, portfolio.direction, settings
            )
        now = datetime.now(UTC)
        payload["next_entry"] = {
            "entered_at": now.isoformat(),
            "effective_date": effective_date_for(now).isoformat(),
        }
        return {
            "as_of": detail["as_of"],
            "market_data_status": detail["market_data_status"],
            "portfolio": payload,
        }


@mcp.tool()
def get_arena_overview(direction: str) -> dict:
    """Managed and default rebuilt (Common/Canonical/Net) summaries for one
    all-long or all-short direction."""
    selected_direction = _direction(direction)
    with _session() as session:
        settings = admin_ops.get_app_settings(session)
        managed_policy = settings["managed_allocation_policy"]
        rebuilt_policy = settings["rebuilt_allocation_policy"]
        portfolios = load_portfolios(session)
        selected = [portfolio for portfolio in portfolios if portfolio.direction == selected_direction]
        valuations = compute_valuations(session, selected)
        managed_rows = []
        for portfolio in selected:
            if portfolio.prompt_mode != "managed":
                continue
            valuation = valuations.by_portfolio_id.get(portfolio.id)
            if valuation is None:
                continue
            summary = serialize_summary(valuation, valuations, managed_policy)
            summary.pop("sparkline", None)
            managed_rows.append(summary)
        rank_rows(managed_rows)

        rebuilt = compute_rebuilt_arena(
            session,
            selected,
            view="common",
            objective="canonical",
            cost_basis="net",
        )
        rebuilt_rows = [
            serialize_rebuilt_summary(analysis, rebuilt, rebuilt_policy, view="common")
            for analysis in rebuilt.by_portfolio_id.values()
            if analysis.portfolio.direction == selected_direction
        ]
        for row in rebuilt_rows:
            row.pop("sparkline", None)
        rank_rows(rebuilt_rows)
        common = rebuilt.common_for(selected_direction)
        return {
            "direction": selected_direction,
            "managed": {
                "as_of": valuations.as_of,
                "market_data_status": valuations.market_data_status,
                "portfolios": [
                    synthetic_spy_row(
                        valuations.spy_series,
                        direction=selected_direction,
                    ),
                    *managed_rows,
                ],
            },
            "rebuilt": {
                "as_of": rebuilt.as_of,
                "market_data_status": rebuilt.market_data_status,
                "context": {
                    "view": "common",
                    "objective": "canonical",
                    "cost_basis": "net",
                    "horizon": None,
                },
                "common_policy": common.policy,
                "portfolios": [
                    (
                        synthetic_spy_row(
                            common.spy_series,
                            precomputed_nav=True,
                            direction=selected_direction,
                        )
                        if common.spy_series
                        else synthetic_spy_row(
                            rebuilt.spy_series,
                            direction=selected_direction,
                        )
                    ),
                    *rebuilt_rows,
                ],
            },
        }


@mcp.tool()
def get_rebuilt_analysis(
    direction: str,
    view: str = "common",
    objective: str = "canonical",
    cost_basis: str = "net",
    horizon: int | None = None,
) -> dict:
    """Analyze rebuilt portfolios in Common, Tuned, or direct Signal view.

    Signal view requires a 1-20 horizon, canonical objective, and gross basis.
    Common/Tuned do not accept a horizon.
    """
    selected_direction = _direction(direction)
    allowed_views = {"common", "tuned", "signal"}
    allowed_objectives = {
        "canonical",
        "max_alpha",
        "max_information_ratio",
        "max_sharpe",
    }
    if view not in allowed_views:
        raise ValueError("view must be common, tuned, or signal")
    if objective not in allowed_objectives:
        raise ValueError("objective must be canonical, max_alpha, max_information_ratio, or max_sharpe")
    if cost_basis not in {"net", "gross"}:
        raise ValueError("cost_basis must be net or gross")
    if view == "signal":
        if horizon is None or horizon < 1 or horizon > 20:
            raise ValueError("signal view requires horizon between 1 and 20")
        if objective != "canonical" or cost_basis != "gross":
            raise ValueError("signal view requires objective=canonical and cost_basis=gross")
    elif horizon is not None:
        raise ValueError("horizon is valid only for signal view")

    with _session() as session:
        allocation_policy = admin_ops.get_app_settings(session)["rebuilt_allocation_policy"]
        portfolios = load_portfolios(session)
        selected = [portfolio for portfolio in portfolios if portfolio.direction == selected_direction]
        arena = compute_rebuilt_arena(
            session,
            selected,
            view=view,
            objective=objective,
            cost_basis=cost_basis,
            horizon=horizon,
        )
        rows = [
            serialize_rebuilt_summary(
                analysis,
                arena,
                allocation_policy,
                view=view,
                horizon=horizon,
            )
            for analysis in arena.by_portfolio_id.values()
            if analysis.portfolio.direction == selected_direction
        ]
        for row in rows:
            row.pop("sparkline", None)
        rank_rows(rows)
        common = arena.common_for(selected_direction)
        spy_row = (
            synthetic_spy_row(
                common.spy_series,
                precomputed_nav=True,
                direction=selected_direction,
            )
            if view == "common" and common.spy_series
            else synthetic_spy_row(arena.spy_series, direction=selected_direction)
        )
        return {
            "direction": selected_direction,
            "as_of": arena.as_of,
            "market_data_status": arena.market_data_status,
            "context": {
                "view": view,
                "objective": objective,
                "cost_basis": cost_basis,
                "horizon": horizon,
            },
            "common_policy": common.policy,
            "portfolios": [
                spy_row,
                *rows,
            ],
        }


# --- Supporting reads -------------------------------------------------------


def _portfolio_counts(session: Session, column) -> dict[int, int]:
    rows = session.execute(select(column, func.count()).group_by(column)).all()
    return {key: count for key, count in rows}


def _portfolio_prompt_out(
    prompt: Prompt,
    prompt_mode: str,
    direction: str,
    settings: dict,
) -> dict:
    """Expose only the strategy text applicable to one portfolio context."""
    payload = admin_ops.prompt_out(prompt, settings)
    for field in (
        "managed_long_text",
        "managed_short_text",
        "rebuilt_long_text",
        "rebuilt_short_text",
    ):
        payload.pop(field, None)
    policies = payload.pop("allocation_policies")
    payload["allocation_policy"] = policies[prompt_mode]
    payload["text"] = prompt.text_for(prompt_mode, direction)
    return payload


@mcp.tool()
def list_agents() -> dict:
    """List generated model + harness + reasoning agent profiles."""
    with _session() as session:
        counts = _portfolio_counts(session, Portfolio.agent_id)
        agents = session.scalars(
            select(Agent)
            .options(selectinload(Agent.model).selectinload(ModelDefinition.capabilities))
            .order_by(Agent.slug)
        ).all()
        return {"agents": [agent_out(agent, portfolio_count=counts.get(agent.id, 0)) for agent in agents]}


@mcp.tool()
def list_harnesses() -> dict:
    """List integrated execution harnesses and their reasoning-effort vocabulary."""
    return harnesses_out()


@mcp.tool()
def list_models() -> dict:
    """List model definitions with harness capabilities and agent usage counts."""
    with _session() as session:
        return admin_ops.list_models(session)


@mcp.tool()
def list_prompts() -> dict:
    """List active prompts (names, modes, directions, and notes, without full text) with
    portfolio usage counts. Use `get_prompt` for current mode-specific text."""
    with _session() as session:
        counts = _portfolio_counts(session, Portfolio.prompt_id)
        prompts = session.scalars(select(Prompt).where(Prompt.status == "active").order_by(Prompt.slug)).all()
        return {
            "prompts": [
                {
                    "id": prompt.id,
                    "slug": prompt.slug,
                    "name": prompt.name,
                    "mode": prompt.mode,
                    "direction": prompt.direction,
                    "notes": prompt.notes,
                    "portfolio_count": counts.get(prompt.id, 0),
                }
                for prompt in prompts
            ]
        }


@mcp.tool()
def get_prompt(slug_or_id: str) -> dict:
    """Fetch an active prompt's current mode-specific text. Accepts a slug or id."""
    with _session() as session:
        text = str(slug_or_id).strip()
        prompt = None
        if text.isdigit():
            prompt = session.scalars(
                select(Prompt).where(
                    Prompt.id == int(text),
                    Prompt.status == "active",
                )
            ).first()
        if prompt is None:
            prompt = session.scalars(
                select(Prompt).where(
                    Prompt.slug == text,
                    Prompt.status == "active",
                )
            ).first()
        if prompt is None:
            raise ValueError(f"Prompt '{slug_or_id}' not found")
        return admin_ops.prompt_out(prompt, admin_ops.get_app_settings(session))


@mcp.tool()
def search_symbols(query: str) -> dict:
    """Search the investable universe (equities and ETFs) for tickers matching
    a query, filtered to instrument types the arena accepts."""
    return {"results": search_symbols_allowed(query)}


@mcp.tool()
def validate_symbol(symbol: str) -> dict:
    """Resolve and validate one symbol against Massive. Returns its
    security type/name/currency/exchange, or errors with a hint. Non-USD assets,
    mutual funds, indices, FX pairs, and futures are rejected."""
    try:
        resolved = resolve_symbol(symbol)
    except SymbolValidationError as exc:
        raise ValueError(exc.message) from None
    return {
        "symbol": resolved.symbol,
        "security_type": resolved.security_type,
        "name": resolved.name,
        "currency": resolved.currency,
        "exchange": resolved.exchange,
    }


@mcp.tool()
def get_effective_date() -> dict:
    """The effective market-close date a new allocation or signal entered right
    now would take (the no-backdating rule: first close strictly after entry)."""
    now = datetime.now(UTC)
    return {"entered_at": now.isoformat(), "effective_date": effective_date_for(now).isoformat()}


# --- Writes: models and agents ---------------------------------------------


@mcp.tool()
def create_model(
    name: str,
    capabilities: list[dict],
    notes: str = "",
) -> dict:
    """Create a model and its harness-specific execution capabilities."""
    with _session() as session:
        return _guard(
            admin_ops.create_model,
            session,
            name=name,
            notes=notes,
            capabilities=capabilities,
        )


@mcp.tool()
def update_model(
    model_id: int,
    name: str | None = None,
    notes: str | None = None,
    capabilities: list[dict] | None = None,
) -> dict:
    """Edit a model and replace its capabilities when supplied."""
    with _session() as session:
        return _guard(
            admin_ops.update_model,
            session,
            model_id,
            name=name,
            notes=notes,
            capabilities=capabilities,
        )


@mcp.tool()
def delete_model(model_id: int) -> dict:
    """Delete an unused model definition."""
    with _session() as session:
        return _guard(admin_ops.delete_model, session, model_id)


@mcp.tool()
def create_agent(
    model_id: int,
    harness: str | None,
    reasoning_effort: str | None,
    notes: str = "",
) -> dict:
    """Create a unique generated execution profile."""
    with _session() as session:
        return _guard(
            admin_ops.create_agent,
            session,
            model_id=model_id,
            harness=harness,
            reasoning_effort=reasoning_effort,
            notes=notes,
        )


@mcp.tool()
def update_agent(
    agent_id: int,
    model_id: int,
    harness: str | None,
    reasoning_effort: str | None,
    notes: str | None = None,
) -> dict:
    """Change an execution profile globally for future runs."""
    with _session() as session:
        return _guard(
            admin_ops.update_agent,
            session,
            agent_id,
            model_id=model_id,
            harness=harness,
            reasoning_effort=reasoning_effort,
            notes=notes,
        )


@mcp.tool()
def delete_agent(agent_id: int) -> dict:
    """Delete an agent. Fails if any portfolio still uses it (reassign first)."""
    with _session() as session:
        return _guard(admin_ops.delete_agent, session, agent_id)


# --- Writes: prompts --------------------------------------------------------


@mcp.tool()
def create_prompt(
    name: str,
    mode: str,
    direction: str,
    managed_long_text: str | None = None,
    managed_short_text: str | None = None,
    rebuilt_long_text: str | None = None,
    rebuilt_short_text: str | None = None,
    notes: str = "",
) -> dict:
    """Create mode- and direction-specific strategy text."""
    with _session() as session:
        created = _guard(
            admin_ops.create_prompt,
            session,
            name=name,
            mode=mode,
            direction=direction,
            managed_long_text=managed_long_text,
            managed_short_text=managed_short_text,
            rebuilt_long_text=rebuilt_long_text,
            rebuilt_short_text=rebuilt_short_text,
            notes=notes,
        )
        prompt = session.get(Prompt, created["id"])
        if prompt is None:
            raise ValueError("Prompt creation did not return a prompt")
        return admin_ops.prompt_out(prompt, admin_ops.get_app_settings(session))


@mcp.tool()
def update_prompt(
    prompt_id: int,
    name: str | None = None,
    mode: str | None = None,
    direction: str | None = None,
    managed_long_text: str | None = None,
    managed_short_text: str | None = None,
    rebuilt_long_text: str | None = None,
    rebuilt_short_text: str | None = None,
    notes: str | None = None,
) -> dict:
    """Edit mode- and direction-specific strategy text, support, or notes."""
    with _session() as session:
        updated = _guard(
            admin_ops.update_prompt,
            session,
            prompt_id,
            name=name,
            mode=mode,
            direction=direction,
            managed_long_text=managed_long_text,
            managed_short_text=managed_short_text,
            rebuilt_long_text=rebuilt_long_text,
            rebuilt_short_text=rebuilt_short_text,
            notes=notes,
        )
        prompt = session.get(Prompt, updated["id"])
        if prompt is None or prompt.status != "active":
            raise ValueError("Prompt not found")
        return admin_ops.prompt_out(prompt, admin_ops.get_app_settings(session))


@mcp.tool()
def archive_prompt(prompt_id: int) -> dict:
    """Archive an active prompt. Fails while any portfolio still uses it.
    Archived content and version history are browser-admin-only."""
    with _session() as session:
        prompt = session.scalars(
            select(Prompt).where(
                Prompt.id == prompt_id,
                Prompt.status == "active",
            )
        ).first()
        if prompt is None:
            raise ValueError("Prompt not found")
        _guard(admin_ops.archive_prompt, session, prompt_id)
        return {"ok": True, "prompt_id": prompt_id}


# --- Writes: portfolios -----------------------------------------------------


@mcp.tool()
def create_portfolio(
    name: str,
    agent_id: int,
    prompt_id: int,
    prompt_mode: str,
    direction: str,
    cost_bps: int | None = None,
) -> dict:
    """Create a portfolio bound to an agent, canonical prompt, and prompt mode
    (`managed` or `rebuilt`) and whole-book direction (`long` or `short`).
    `cost_bps` defaults to the configured default."""
    with _session() as session:
        return _guard(
            admin_ops.create_portfolio,
            session,
            name=name,
            agent_id=agent_id,
            prompt_id=prompt_id,
            prompt_mode=prompt_mode,
            direction=_direction(direction),
            cost_bps=cost_bps,
        )


@mcp.tool()
def update_portfolio(
    portfolio_id: int,
    name: str | None = None,
    status: str | None = None,
    agent_id: int | None = None,
    prompt_id: int | None = None,
    prompt_mode: str | None = None,
    direction: str | None = None,
    cost_bps: int | None = None,
) -> dict:
    """Edit a portfolio: rename, archive/unarchive (`status` = "active" |
    "archived"), reassign agent/prompt, select `managed` or `rebuilt` prompt
    mode, direction, or cost_bps. Omitted fields are left unchanged."""
    with _session() as session:
        return _guard(
            admin_ops.update_portfolio,
            session,
            portfolio_id,
            name=name,
            status=status,
            agent_id=agent_id,
            prompt_id=prompt_id,
            prompt_mode=prompt_mode,
            direction=_direction(direction) if direction is not None else None,
            cost_bps=cost_bps,
        )


@mcp.tool()
def delete_portfolio(portfolio_id: int) -> dict:
    """Delete a portfolio and all of its mode-specific history. Irreversible."""
    with _session() as session:
        return _guard(admin_ops.delete_portfolio, session, portfolio_id)


@mcp.tool()
def reset_portfolio(portfolio_id: int) -> dict:
    """Delete the portfolio's mode-specific managed allocations or rebuilt
    signals. Identity, evaluator configuration, and evaluator audit remain."""
    with _session() as session:
        return _guard(admin_ops.reset_portfolio, session, portfolio_id)


# --- Writes: allocations ----------------------------------------------------


@mcp.tool()
def create_allocation(portfolio_id: int, positions: list[PositionIn], note: str = "") -> dict:
    """Enter a managed rebalance (or first allocation). Weights must sum to exactly
    100 and satisfy the portfolio prompt's position limits. Each position's
    `note` and the general `note` are the handoff to the next rebalance. Entry
    time is server-set; the allocation freezes after its effective close."""
    with _session() as session:
        return _guard(admin_ops.create_allocation, session, portfolio_id, _positions(positions), note)


@mcp.tool()
def update_allocation(
    allocation_id: int, positions: list[PositionIn] | None = None, note: str | None = None
) -> dict:
    """Edit a pending allocation. The general `note` is always editable;
    `positions` can only be changed before the effective close (afterwards, enter
    a new rebalance instead)."""
    with _session() as session:
        pos = _positions(positions) if positions is not None else None
        return _guard(admin_ops.update_allocation, session, allocation_id, pos, note)


@mcp.tool()
def delete_allocation(allocation_id: int) -> dict:
    """Delete a pending (unlocked) allocation. Locked allocations cannot be deleted."""
    with _session() as session:
        return _guard(admin_ops.delete_allocation, session, allocation_id)


# --- Writes: rebuilt signals -----------------------------------------------


@mcp.tool()
def create_signal(portfolio_id: int, positions: list[PositionIn], note: str = "") -> dict:
    """Enter one independent rebuilt signal portfolio for the next effective
    close. Weights must total 100 and satisfy the prompt policy. The server sets
    entry/effective time; do not use this for managed portfolios."""
    with _session() as session:
        return _guard(
            admin_ops.create_signal,
            session,
            portfolio_id,
            _positions(positions),
            note,
            provenance="mcp",
        )


@mcp.tool()
def update_signal(
    signal_id: int,
    positions: list[PositionIn] | None = None,
    note: str | None = None,
) -> dict:
    """Edit a pending rebuilt signal. A signal is wholly immutable after its
    effective close."""
    with _session() as session:
        pos = _positions(positions) if positions is not None else None
        return _guard(admin_ops.update_signal, session, signal_id, pos, note)


@mcp.tool()
def delete_signal(signal_id: int) -> dict:
    """Delete a pending rebuilt signal. Locked signals cannot be deleted."""
    with _session() as session:
        return _guard(admin_ops.delete_signal, session, signal_id)


# --- Evaluator control -----------------------------------------------------


@mcp.tool()
def get_evaluator_dashboard() -> dict:
    """Read evaluator settings, per-portfolio configuration, and live worker status."""
    with _session() as session:
        return evaluator.get_dashboard(session)


@mcp.tool()
def update_evaluator_settings(
    enabled: bool,
    max_concurrency: int,
    poll_seconds: int,
    attempt_timeout_seconds: int,
    max_attempts: int,
    queue_before_close_minutes: int,
) -> dict:
    """Update global evaluator scheduling and execution settings."""
    with _session() as session:
        return _guard(
            evaluator.update_settings,
            session,
            enabled=enabled,
            max_concurrency=max_concurrency,
            poll_seconds=poll_seconds,
            attempt_timeout_seconds=attempt_timeout_seconds,
            max_attempts=max_attempts,
            queue_before_close_minutes=queue_before_close_minutes,
        )


@mcp.tool()
def configure_portfolio_evaluator(
    portfolio_id: int,
    enabled: bool,
    weekdays: list[int],
) -> dict:
    """Enable or disable one eligible portfolio. Managed portfolios accept
    selected weekdays 0-4; rebuilt portfolios always run Monday-Friday."""
    with _session() as session:
        return _guard(
            evaluator.update_portfolio_config,
            session,
            portfolio_id=portfolio_id,
            enabled=enabled,
            weekdays=weekdays,
        )


@mcp.tool()
def run_evaluations(portfolio_ids: list[int]) -> dict:
    """Queue immediate evaluations for enabled portfolios."""
    with _session() as session:
        return _guard(evaluator.enqueue_manual_runs, session, portfolio_ids=portfolio_ids)


@mcp.tool()
def cancel_evaluation_run(run_id: int) -> dict:
    """Cancel queued work or request cancellation of a running Codex process."""
    with _session() as session:
        return _guard(evaluator.cancel_run, session, run_id=run_id)


@mcp.tool()
def retry_evaluation_run(run_id: int) -> dict:
    """Queue a fresh immediate retry linked to a failed evaluation run."""
    with _session() as session:
        return _guard(evaluator.retry_run, session, run_id=run_id)


@mcp.tool()
def list_evaluation_runs(
    portfolio_id: int | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    """List evaluation history newest-first with optional filters and cursor pagination."""
    with _session() as session:
        return _guard(
            evaluator.list_runs,
            session,
            portfolio_id=portfolio_id,
            status=status,
            cursor=cursor,
            limit=limit,
        )


# --- Writes: settings -------------------------------------------------------


@mcp.tool()
def get_settings() -> dict:
    """Read costs, allocation policies, wrappers, and long/short direction instructions."""
    with _session() as session:
        return admin_ops.get_app_settings(session)


@mcp.tool()
def update_settings(
    default_cost_bps: int,
    managed_allocation_policy: AllocationPolicyIn,
    rebuilt_allocation_policy: AllocationPolicyIn,
    managed_wrapper_prompt: str,
    rebuilt_wrapper_prompt: str,
    long_direction_instructions: str,
    short_direction_instructions: str,
) -> dict:
    """Atomically update costs, sizing, wrappers, and direction instructions."""
    with _session() as session:
        return _guard(
            admin_ops.update_app_settings,
            session,
            default_cost_bps=default_cost_bps,
            managed_allocation_policy=managed_allocation_policy.model_dump(),
            rebuilt_allocation_policy=rebuilt_allocation_policy.model_dump(),
            managed_wrapper_prompt=managed_wrapper_prompt,
            rebuilt_wrapper_prompt=rebuilt_wrapper_prompt,
            long_direction_instructions=long_direction_instructions,
            short_direction_instructions=short_direction_instructions,
        )
