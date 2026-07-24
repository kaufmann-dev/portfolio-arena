"""MCP tools — the full app surface an admin/visitor has, minus API-key
management. Reads use the shared serializers; writes call ``services.admin_ops``
so every integrity rule is enforced exactly as it is for the REST admin panel.

Each tool opens its own session (FastMCP runs sync tools in a worker thread).
``AdminOpError`` / ``SymbolValidationError`` are surfaced as tool errors.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..config import BENCHMARK_STRATEGY
from ..db import session_factory
from ..models import Agent, ModelDefinition, Portfolio, Prompt
from ..schemas import AllocationPolicyIn, PositionIn
from ..services import admin_ops, evaluator
from ..services.admin_ops import AdminOpError
from ..services.arena import compute_valuations, load_portfolios
from ..services.benchmarks import reconcile_benchmark_allocations
from ..services.harnesses import harnesses_out
from ..services.model_catalog import agent_out
from ..services.serialize import serialize_summary
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
    the canonical strategy, allocation policy, prompt mode, and next effective
    date. Accepts a slug or a numeric id."""
    with _session() as session:
        portfolio_id = _resolve_portfolio(session, slug_or_id).id
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
            payload["prompt"] = admin_ops.prompt_out(prompt)
        elif payload["is_benchmark"]:
            payload["prompt"] = {
                **prompt_payload,
                "text": BENCHMARK_STRATEGY["text"],
                "notes": "Hardcoded benchmark strategy.",
            }
        if payload.get("prompt_mode") == "rebuilt":
            for key in (
                "cost_bps",
                "inception",
                "age_days",
                "too_early",
                "allocation_count",
                "metrics",
                "stale_data",
                "frozen_symbols",
                "error",
                "holdings",
                "allocations",
            ):
                payload.pop(key, None)
        now = datetime.now(UTC)
        payload["next_entry"] = {
            "entered_at": now.isoformat(),
            "effective_date": effective_date_for(now).isoformat(),
        }
        return {"as_of": detail["as_of"], "portfolio": payload}


@mcp.tool()
def get_arena_overview() -> dict:
    """Compare every portfolio (contestants and benchmarks) at once: performance
    metrics, return vs SPY, volatility, age, and allocation count — the
    leaderboard view, for judging which portfolios are performing."""
    with _session() as session:
        if reconcile_benchmark_allocations(session):
            session.commit()
        portfolios = load_portfolios(session)
        valuations = compute_valuations(session, portfolios)
        rows = []
        for portfolio in portfolios:
            valuation = valuations.by_portfolio_id.get(portfolio.id)
            if valuation is None:
                continue
            summary = serialize_summary(valuation, valuations)
            summary.pop("sparkline", None)
            rows.append(summary)
        return {"as_of": valuations.as_of, "portfolios": rows}


# --- Supporting reads -------------------------------------------------------


def _portfolio_counts(session: Session, column) -> dict[int, int]:
    rows = session.execute(select(column, func.count()).group_by(column)).all()
    return {key: count for key, count in rows}


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
    """List prompts (names and notes, without full text) with portfolio usage
    counts. Use `get_prompt` for a prompt's full text."""
    with _session() as session:
        counts = _portfolio_counts(session, Portfolio.prompt_id)
        prompts = session.scalars(select(Prompt).order_by(Prompt.slug)).all()
        return {
            "prompts": [
                {
                    "id": prompt.id,
                    "slug": prompt.slug,
                    "name": prompt.name,
                    "notes": prompt.notes,
                    "portfolio_count": counts.get(prompt.id, 0),
                }
                for prompt in prompts
            ]
        }


@mcp.tool()
def get_prompt(slug_or_id: str) -> dict:
    """Fetch a prompt's full text (plus name and notes). Accepts a slug or id."""
    with _session() as session:
        text = str(slug_or_id).strip()
        prompt = None
        if text.isdigit():
            prompt = session.get(Prompt, int(text))
        if prompt is None:
            prompt = session.scalars(select(Prompt).where(Prompt.slug == text)).first()
        if prompt is None:
            raise ValueError(f"Prompt '{slug_or_id}' not found")
        return admin_ops.prompt_out(prompt)


@mcp.tool()
def search_symbols(query: str) -> dict:
    """Search the investable universe (equities and ETFs) for tickers matching
    a query, filtered to instrument types the arena accepts."""
    return {"results": search_symbols_allowed(query)}


@mcp.tool()
def validate_symbol(symbol: str) -> dict:
    """Resolve and validate one symbol against Yahoo Finance. Returns its
    security type/name/currency/exchange, or errors with a hint. Non-USD assets,
    funds, indices, FX pairs, and futures are rejected."""
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
    """The effective market-close date a new allocation entered right now would
    take (the no-backdating rule: first close strictly after entry)."""
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
def create_prompt(name: str, text: str, allocation_policy: AllocationPolicyIn, notes: str = "") -> dict:
    """Create strategy text with a server-enforced position-sizing policy."""
    with _session() as session:
        return _guard(
            admin_ops.create_prompt,
            session,
            name=name,
            text=text,
            notes=notes,
            allocation_policy=allocation_policy.model_dump(),
        )


@mcp.tool()
def update_prompt(
    prompt_id: int,
    name: str | None = None,
    text: str | None = None,
    notes: str | None = None,
    allocation_policy: AllocationPolicyIn | None = None,
) -> dict:
    """Edit strategy text, notes, or its position-sizing policy."""
    with _session() as session:
        return _guard(
            admin_ops.update_prompt,
            session,
            prompt_id,
            name=name,
            text=text,
            notes=notes,
            allocation_policy=allocation_policy.model_dump() if allocation_policy else None,
        )


@mcp.tool()
def delete_prompt(prompt_id: int) -> dict:
    """Delete a prompt. Fails if any portfolio still uses it."""
    with _session() as session:
        return _guard(admin_ops.delete_prompt, session, prompt_id)


# --- Writes: portfolios -----------------------------------------------------


@mcp.tool()
def create_portfolio(
    name: str,
    agent_id: int,
    prompt_id: int,
    prompt_mode: str,
    cost_bps: int | None = None,
) -> dict:
    """Create a portfolio bound to an agent, canonical prompt, and prompt mode
    (`managed` or `rebuilt`). `cost_bps` defaults to the configured default."""
    with _session() as session:
        return _guard(
            admin_ops.create_portfolio,
            session,
            name=name,
            agent_id=agent_id,
            prompt_id=prompt_id,
            prompt_mode=prompt_mode,
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
    cost_bps: int | None = None,
) -> dict:
    """Edit a portfolio: rename, archive/unarchive (`status` = "active" |
    "archived"), reassign agent/prompt, select `managed` or `rebuilt` prompt
    mode, or change cost_bps. Omitted fields are left unchanged."""
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
            cost_bps=cost_bps,
        )


@mcp.tool()
def delete_portfolio(portfolio_id: int) -> dict:
    """Delete a portfolio and all of its allocations. Irreversible."""
    with _session() as session:
        return _guard(admin_ops.delete_portfolio, session, portfolio_id)


@mcp.tool()
def reset_portfolio(portfolio_id: int) -> dict:
    """Reset a portfolio to its never-started state by deleting all allocations
    and performance history. The portfolio identity, evaluator configuration,
    and evaluator audit records are preserved. Irreversible."""
    with _session() as session:
        return _guard(admin_ops.reset_portfolio, session, portfolio_id)


# --- Writes: allocations ----------------------------------------------------


@mcp.tool()
def create_allocation(portfolio_id: int, positions: list[PositionIn], note: str = "") -> dict:
    """Enter a rebalance (or the first allocation). Weights must sum to exactly
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
    start_before_close_minutes: int,
    cutoff_before_close_minutes: int,
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
            start_before_close_minutes=start_before_close_minutes,
            cutoff_before_close_minutes=cutoff_before_close_minutes,
        )


@mcp.tool()
def configure_portfolio_evaluator(
    portfolio_id: int,
    enabled: bool,
    weekdays: list[int],
) -> dict:
    """Enable or disable one eligible portfolio and choose weekdays 0-4."""
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
    """Read the default cost and managed/rebuilt wrapper prompt templates."""
    with _session() as session:
        return admin_ops.get_app_settings(session)


@mcp.tool()
def update_settings(
    default_cost_bps: int,
    managed_wrapper_prompt: str,
    rebuilt_wrapper_prompt: str,
) -> dict:
    """Atomically update the default cost and both wrapper prompt templates."""
    with _session() as session:
        return _guard(
            admin_ops.update_app_settings,
            session,
            default_cost_bps=default_cost_bps,
            managed_wrapper_prompt=managed_wrapper_prompt,
            rebuilt_wrapper_prompt=rebuilt_wrapper_prompt,
        )
