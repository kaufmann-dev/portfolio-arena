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
from sqlalchemy.orm import Session

from ..db import session_factory
from ..models import Agent, Portfolio, Prompt
from ..schemas import PositionIn
from ..services import admin_ops
from ..services.admin_ops import AdminOpError
from ..services.arena import compute_valuations, load_portfolios
from ..services.benchmarks import ensure_benchmark_allocations
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
    """Everything needed to rebalance ONE portfolio: its prompt text, current
    drifted holdings (entry vs current price, per-position notes), the full
    allocation history with general and per-position notes, performance metrics,
    and the effective date a new allocation entered now would take. Accepts a
    slug or a numeric id."""
    with _session() as session:
        portfolio_id = _resolve_portfolio(session, slug_or_id).id
        detail = _guard(admin_ops.portfolio_admin_detail, session, portfolio_id)
        payload = detail["portfolio"]
        # Drop token-heavy chart data the rebalancing agent doesn't need.
        for key in ("series", "spy_series", "sparkline", "stale_days"):
            payload.pop(key, None)
        prompt = session.get(Prompt, payload["prompt"]["id"]) if payload.get("prompt") else None
        if prompt is not None:
            payload["prompt"] = admin_ops.prompt_out(prompt)
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
        ensure_benchmark_allocations(session)
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
    """List agent identities (model + harness) with how many portfolios use each."""
    with _session() as session:
        counts = _portfolio_counts(session, Portfolio.agent_id)
        agents = session.scalars(select(Agent).order_by(Agent.slug)).all()
        return {
            "agents": [
                {
                    "id": agent.id,
                    "slug": agent.slug,
                    "name": agent.name,
                    "notes": agent.notes,
                    "portfolio_count": counts.get(agent.id, 0),
                }
                for agent in agents
            ]
        }


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
    """Search the investable universe (equities/ETFs/funds) for tickers matching
    a query, filtered to instrument types the arena accepts."""
    return {"results": search_symbols_allowed(query)}


@mcp.tool()
def validate_symbol(symbol: str) -> dict:
    """Resolve and validate one symbol against Yahoo Finance. Returns its
    instrument/name/currency/exchange, or errors with a hint (indices, FX pairs,
    and futures are rejected; use ETFs or CASH:CCY instead)."""
    try:
        resolved = resolve_symbol(symbol)
    except SymbolValidationError as exc:
        raise ValueError(exc.message) from None
    return {
        "symbol": resolved.symbol,
        "instrument": resolved.instrument,
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


# --- Writes: agents ---------------------------------------------------------


@mcp.tool()
def create_agent(name: str, notes: str = "") -> dict:
    """Create an agent identity (e.g. "Claude Opus 4.8 (Claude Code)")."""
    with _session() as session:
        return _guard(admin_ops.create_agent, session, name=name, notes=notes)


@mcp.tool()
def update_agent(agent_id: int, name: str | None = None, notes: str | None = None) -> dict:
    """Rename an agent or edit its notes. Omitted fields are left unchanged."""
    with _session() as session:
        return _guard(admin_ops.update_agent, session, agent_id, name=name, notes=notes)


@mcp.tool()
def delete_agent(agent_id: int) -> dict:
    """Delete an agent. Fails if any portfolio still uses it (reassign first)."""
    with _session() as session:
        return _guard(admin_ops.delete_agent, session, agent_id)


# --- Writes: prompts --------------------------------------------------------


@mcp.tool()
def create_prompt(name: str, text: str, notes: str = "") -> dict:
    """Create a prompt (its instructions text is stored verbatim)."""
    with _session() as session:
        return _guard(admin_ops.create_prompt, session, name=name, text=text, notes=notes)


@mcp.tool()
def update_prompt(
    prompt_id: int, name: str | None = None, text: str | None = None, notes: str | None = None
) -> dict:
    """Edit a prompt's name, text, or notes. Omitted fields are left unchanged."""
    with _session() as session:
        return _guard(admin_ops.update_prompt, session, prompt_id, name=name, text=text, notes=notes)


@mcp.tool()
def delete_prompt(prompt_id: int) -> dict:
    """Delete a prompt. Fails if any portfolio still uses it."""
    with _session() as session:
        return _guard(admin_ops.delete_prompt, session, prompt_id)


# --- Writes: portfolios -----------------------------------------------------


@mcp.tool()
def create_portfolio(name: str, agent_id: int, prompt_id: int, cost_bps: int | None = None) -> dict:
    """Create a portfolio bound to an agent and a prompt. `cost_bps` defaults to
    the configured default. Enter its first allocation with `create_allocation`."""
    with _session() as session:
        return _guard(
            admin_ops.create_portfolio,
            session,
            name=name,
            agent_id=agent_id,
            prompt_id=prompt_id,
            cost_bps=cost_bps,
        )


@mcp.tool()
def update_portfolio(
    portfolio_id: int,
    name: str | None = None,
    status: str | None = None,
    agent_id: int | None = None,
    prompt_id: int | None = None,
    cost_bps: int | None = None,
) -> dict:
    """Edit a portfolio: rename, archive/unarchive (`status` = "active" |
    "archived"), reassign agent/prompt, or change cost_bps. Omitted fields are
    left unchanged."""
    with _session() as session:
        return _guard(
            admin_ops.update_portfolio,
            session,
            portfolio_id,
            name=name,
            status=status,
            agent_id=agent_id,
            prompt_id=prompt_id,
            cost_bps=cost_bps,
        )


@mcp.tool()
def delete_portfolio(portfolio_id: int) -> dict:
    """Delete a portfolio and all of its allocations. Irreversible."""
    with _session() as session:
        return _guard(admin_ops.delete_portfolio, session, portfolio_id)


# --- Writes: allocations ----------------------------------------------------


@mcp.tool()
def create_allocation(portfolio_id: int, positions: list[PositionIn], note: str = "") -> dict:
    """Enter a rebalance (or the first allocation). `positions` weights must sum
    to exactly 100; use CASH:USD/CASH:EUR for cash. Each position's `note` and
    the general `note` are the handoff to the next rebalance. Entry time is
    server-set; the allocation freezes after its effective market close."""
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


# --- Writes: settings -------------------------------------------------------


@mcp.tool()
def get_settings() -> dict:
    """Read arena settings (the default cost_bps applied to new portfolios)."""
    with _session() as session:
        return admin_ops.get_default_cost_bps(session)


@mcp.tool()
def update_settings(default_cost_bps: int) -> dict:
    """Set the default cost_bps applied to new portfolios."""
    with _session() as session:
        return admin_ops.set_default_cost_bps(session, default_cost_bps)
