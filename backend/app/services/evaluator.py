"""Database-backed evaluator configuration, scheduling, queue, and run lifecycle."""

import base64
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Agent,
    Allocation,
    EvaluationRun,
    EvaluatorInstance,
    EvaluatorSettings,
    ModelDefinition,
    Portfolio,
    PortfolioEvaluatorConfig,
    Prompt,
    Signal,
)
from . import admin_ops
from .admin_ops import AdminOpError
from .arena import compute_valuations
from .harnesses import automation_harness_ids, supports_automation
from .model_catalog import agent_out, agent_snapshot_out, model_ref
from .prompt_policy import automated_execution_prompt
from .trading_calendar import close_at, effective_date_for, is_trading_day

ACTIVE_STATUSES = {"queued", "running", "cancel_requested"}
FINISHED_STATUSES = {"cancelled", "succeeded", "failed", "skipped"}
RUN_ERROR_MAX_LENGTH = 4000
RUN_REPORT_MAX_LENGTH = 20_000
INSTANCE_STALE_SECONDS = 180


def _managed_liquidations(
    session: Session,
    portfolios: list[Portfolio],
) -> dict[int, str]:
    candidates = [
        portfolio
        for portfolio in portfolios
        if (portfolio.prompt_mode == "managed" and portfolio.direction == "short" and portfolio.allocations)
    ]
    if not candidates:
        return {}
    valuations = compute_valuations(session, candidates)
    return {
        portfolio.id: valuation.result.liquidated_at
        for portfolio in candidates
        if (
            (valuation := valuations.by_portfolio_id.get(portfolio.id)) is not None
            and valuation.result is not None
            and valuation.result.liquidated_at is not None
        )
    }


def _liquidated_managed_ids(
    session: Session,
    portfolios: list[Portfolio],
) -> set[int]:
    return set(_managed_liquidations(session, portfolios))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def settings_out(settings: EvaluatorSettings) -> dict:
    return {
        "enabled": settings.enabled,
        "max_concurrency": settings.max_concurrency,
        "poll_seconds": settings.poll_seconds,
        "attempt_timeout_seconds": settings.attempt_timeout_seconds,
        "max_attempts": settings.max_attempts,
        "queue_before_close_minutes": settings.queue_before_close_minutes,
        "updated_at": settings.updated_at.isoformat(),
    }


def config_out(
    config: PortfolioEvaluatorConfig | None,
    portfolio: Portfolio,
    *,
    liquidated_at: str | None = None,
) -> dict:
    return {
        "portfolio": {
            "id": portfolio.id,
            "slug": portfolio.slug,
            "name": portfolio.name,
            "status": portfolio.status,
            "prompt_mode": portfolio.prompt_mode,
            "direction": portfolio.direction,
            "is_liquidated": liquidated_at is not None,
            "liquidated_at": liquidated_at,
        },
        "agent": agent_out(portfolio.agent),
        "enabled": config.enabled if config is not None else False,
        "weekdays": (
            [0, 1, 2, 3, 4]
            if portfolio.prompt_mode == "rebuilt"
            else (list(config.weekdays) if config is not None else [])
        ),
        "updated_at": config.updated_at.isoformat() if config is not None else None,
    }


def run_out(run: EvaluationRun) -> dict:
    result = None
    if run.allocation_id is not None:
        result = {"kind": "allocation", "id": run.allocation_id}
    elif run.signal_id is not None:
        result = {"kind": "signal", "id": run.signal_id}
    return {
        "id": run.id,
        "portfolio": {
            "id": run.portfolio.id,
            "slug": run.portfolio.slug,
            "name": run.portfolio.name,
            "direction": run.portfolio.direction,
        },
        "agent": agent_snapshot_out(
            run.agent,
            run.model,
            harness=run.harness,
            execution_model_id=run.execution_model_id,
            reasoning_effort=run.reasoning_effort,
        ),
        "model": model_ref(run.model),
        "trigger_kind": run.trigger_kind,
        "retry_of_run_id": run.retry_of_run_id,
        "scheduled_for": run.scheduled_for.isoformat() if run.scheduled_for else None,
        "harness": run.harness,
        "execution_model_id": run.execution_model_id,
        "reasoning_effort": run.reasoning_effort,
        "timeout_seconds": run.timeout_seconds,
        "max_attempts": run.max_attempts,
        "harness_version": run.harness_version,
        "worker_id": run.worker_id,
        "status": run.status,
        "attempt_count": run.attempt_count,
        "lease_expires_at": _iso(run.lease_expires_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "result": result,
        "report": run.report,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _run_query():
    return select(EvaluationRun).options(
        selectinload(EvaluationRun.portfolio).selectinload(Portfolio.prompt),
        selectinload(EvaluationRun.agent)
        .selectinload(Agent.model)
        .selectinload(ModelDefinition.capabilities),
        selectinload(EvaluationRun.model),
    )


def _load_run(session: Session, run_id: int, *, lock: bool = False) -> EvaluationRun:
    query = _run_query().where(EvaluationRun.id == run_id)
    if lock:
        query = query.with_for_update()
    run = session.scalars(query).first()
    if run is None:
        raise AdminOpError(404, "Evaluation run not found")
    return run


def _claimed_run_out(session: Session, run: EvaluationRun) -> dict:
    app_settings = admin_ops.get_app_settings(session)
    wrapper_prompt = app_settings[f"{run.portfolio.prompt_mode}_wrapper_prompt"]
    allocation_policy = app_settings[f"{run.portfolio.prompt_mode}_allocation_policy"]
    return {
        **run_out(run),
        "execution_prompt": automated_execution_prompt(
            run.portfolio,
            wrapper_prompt,
            allocation_policy,
        ),
    }


def get_settings(session: Session, *, lock: bool = False) -> EvaluatorSettings:
    query = select(EvaluatorSettings).where(EvaluatorSettings.id == 1)
    if lock:
        query = query.with_for_update()
    settings = session.scalars(query).one_or_none()
    if settings is None:
        settings = EvaluatorSettings(id=1)
        session.add(settings)
        session.flush()
    return settings


def _validate_settings(values: dict) -> None:
    ranges = {
        "max_concurrency": (1, 20),
        "poll_seconds": (10, 300),
        "attempt_timeout_seconds": (60, 7200),
        "max_attempts": (1, 5),
        "queue_before_close_minutes": (15, 240),
    }
    for key, (minimum, maximum) in ranges.items():
        value = values[key]
        if not minimum <= value <= maximum:
            raise AdminOpError(422, f"{key} must be between {minimum} and {maximum}")


def update_settings(session: Session, **values) -> dict:
    settings = get_settings(session, lock=True)
    merged = {
        key: values.get(key, getattr(settings, key))
        for key in (
            "enabled",
            "max_concurrency",
            "poll_seconds",
            "attempt_timeout_seconds",
            "max_attempts",
            "queue_before_close_minutes",
        )
    }
    _validate_settings(merged)
    for key, value in merged.items():
        setattr(settings, key, value)
    session.commit()
    return settings_out(settings)


def update_portfolio_config(
    session: Session,
    *,
    portfolio_id: int,
    enabled: bool,
    weekdays: list[int],
) -> dict:
    portfolio = session.scalars(
        select(Portfolio)
        .where(Portfolio.id == portfolio_id)
        .options(
            selectinload(Portfolio.agent).selectinload(Agent.model).selectinload(ModelDefinition.capabilities)
        )
    ).first()
    if portfolio is None:
        raise AdminOpError(404, "Portfolio not found")
    liquidations = _managed_liquidations(session, [portfolio])
    if enabled and portfolio.id in liquidations:
        raise AdminOpError(
            409,
            "Reset this liquidated short portfolio before enabling evaluation.",
        )
    get_settings(session, lock=True)
    clean_weekdays = [0, 1, 2, 3, 4] if portfolio.prompt_mode == "rebuilt" else sorted(set(weekdays))
    if enabled and portfolio.status != "active":
        raise AdminOpError(409, "Archived portfolios cannot be enabled for evaluation")
    if enabled and not supports_automation(portfolio.agent.harness):
        raise AdminOpError(422, "The portfolio agent does not support integrated automation")
    if any(day < 0 or day > 4 for day in clean_weekdays):
        raise AdminOpError(422, "Weekdays must be numbers from 0 (Monday) through 4 (Friday)")

    config = session.get(PortfolioEvaluatorConfig, portfolio_id)
    if config is None:
        config = PortfolioEvaluatorConfig(
            portfolio_id=portfolio_id,
            enabled=enabled,
            weekdays=clean_weekdays,
        )
        session.add(config)
    else:
        config.enabled = enabled
        config.weekdays = clean_weekdays
    if not enabled:
        current_time = datetime.now(UTC)
        queued_runs = session.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.portfolio_id == portfolio_id,
                EvaluationRun.status == "queued",
            )
            .with_for_update(skip_locked=True)
        ).all()
        for run in queued_runs:
            run.status = "cancelled"
            run.finished_at = current_time
            run.error = "Cancelled because portfolio automation was disabled."
    session.commit()
    return config_out(
        config,
        portfolio,
        liquidated_at=liquidations.get(portfolio.id),
    )


def _runtime_out(session: Session, settings: EvaluatorSettings, now: datetime) -> dict:
    fresh_after = now - timedelta(seconds=INSTANCE_STALE_SECONDS)
    instances = session.scalars(
        select(EvaluatorInstance)
        .where(EvaluatorInstance.last_heartbeat_at >= fresh_after)
        .order_by(EvaluatorInstance.last_heartbeat_at.desc())
    ).all()
    if not instances:
        latest = session.scalars(
            select(EvaluatorInstance).order_by(EvaluatorInstance.last_heartbeat_at.desc()).limit(1)
        ).first()
        return {
            "online": False,
            "status": "offline",
            "authenticated": False,
            "harness": latest.harness if latest else "codex",
            "harness_version": latest.harness_version if latest else None,
            "active_run_count": 0,
            "last_heartbeat_at": _iso(latest.last_heartbeat_at) if latest else None,
            "last_error": latest.last_error if latest else None,
            "instance_count": 0,
        }
    primary = instances[0]
    return {
        "online": True,
        "status": "paused" if not settings.enabled else primary.status,
        "authenticated": all(instance.authenticated for instance in instances),
        "harness": primary.harness,
        "harness_version": primary.harness_version,
        "active_run_count": sum(instance.active_run_count for instance in instances),
        "last_heartbeat_at": primary.last_heartbeat_at.isoformat(),
        "last_error": next((instance.last_error for instance in instances if instance.last_error), None),
        "instance_count": len(instances),
    }


def get_dashboard(session: Session, *, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(UTC)
    settings = get_settings(session)
    portfolios = session.scalars(
        select(Portfolio)
        .join(Portfolio.agent)
        .where(
            Agent.harness.in_(automation_harness_ids()),
        )
        .options(
            selectinload(Portfolio.evaluator_config),
            selectinload(Portfolio.agent)
            .selectinload(Agent.model)
            .selectinload(ModelDefinition.capabilities),
        )
        .order_by(Portfolio.name)
    ).all()
    liquidations = _managed_liquidations(session, portfolios)
    return {
        "settings": settings_out(settings),
        "portfolios": [
            config_out(
                portfolio.evaluator_config,
                portfolio,
                liquidated_at=liquidations.get(portfolio.id),
            )
            for portfolio in portfolios
        ],
        "runtime": _runtime_out(session, settings, current_time),
    }


def scheduled_enqueue_window(
    scheduled_for: date,
    settings: EvaluatorSettings,
) -> tuple[datetime, datetime]:
    close = close_at(scheduled_for)
    return (
        close - timedelta(minutes=settings.queue_before_close_minutes),
        close,
    )


def _next_trading_day(day: date) -> date:
    candidate = day
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def is_due_on(config: PortfolioEvaluatorConfig, session_date: date) -> bool:
    if not config.weekdays or not is_trading_day(session_date):
        return False
    for days_back in range(8):
        source_day = session_date - timedelta(days=days_back)
        if source_day.weekday() in config.weekdays and _next_trading_day(source_day) == session_date:
            return True
    return False


def _active_run(session: Session, portfolio_id: int) -> EvaluationRun | None:
    return session.scalars(
        _run_query().where(
            EvaluationRun.portfolio_id == portfolio_id,
            EvaluationRun.status.in_(ACTIVE_STATUSES),
        )
    ).first()


def _new_run(
    config: PortfolioEvaluatorConfig,
    settings: EvaluatorSettings,
    *,
    trigger_kind: str,
    scheduled_for: date | None = None,
    retry_of_run_id: int | None = None,
) -> EvaluationRun:
    portfolio = config.portfolio
    agent = portfolio.agent
    if not supports_automation(agent.harness):
        raise AdminOpError(409, "Portfolio agent does not support integrated automation")
    capability = next(
        (item for item in agent.model.capabilities if item.harness == agent.harness),
        None,
    )
    if capability is None:
        raise AdminOpError(409, "Portfolio agent model is not configured for its harness")
    return EvaluationRun(
        portfolio_id=config.portfolio_id,
        agent_id=agent.id,
        model_id=agent.model_id,
        scheduled_for=scheduled_for,
        trigger_kind=trigger_kind,
        retry_of_run_id=retry_of_run_id,
        harness=agent.harness,
        execution_model_id=capability.execution_model_id,
        reasoning_effort=agent.reasoning_effort,
        timeout_seconds=settings.attempt_timeout_seconds,
        max_attempts=settings.max_attempts,
        status="queued",
        attempt_count=0,
    )


def _pending_result(
    session: Session,
    portfolio: Portfolio,
    now: datetime,
) -> Allocation | Signal | None:
    effective = effective_date_for(now)
    if portfolio.prompt_mode == "managed":
        return session.scalars(
            select(Allocation).where(
                Allocation.portfolio_id == portfolio.id,
                Allocation.effective_date == effective,
            )
        ).first()
    return session.scalars(
        select(Signal).where(
            Signal.portfolio_id == portfolio.id,
            Signal.effective_date == effective,
        )
    ).first()


def enqueue_manual_runs(
    session: Session,
    *,
    portfolio_ids: list[int],
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    unique_ids = list(dict.fromkeys(portfolio_ids))
    if not unique_ids:
        raise AdminOpError(422, "Select at least one portfolio")
    candidate_portfolios = list(session.scalars(select(Portfolio).where(Portfolio.id.in_(unique_ids))))
    liquidated_ids = _liquidated_managed_ids(session, candidate_portfolios)
    settings = get_settings(session, lock=True)
    if not settings.enabled:
        raise AdminOpError(409, "The evaluator is paused")

    items = []
    for portfolio_id in unique_ids:
        config = session.get(PortfolioEvaluatorConfig, portfolio_id)
        portfolio = session.scalars(
            select(Portfolio)
            .where(Portfolio.id == portfolio_id)
            .options(
                selectinload(Portfolio.agent)
                .selectinload(Agent.model)
                .selectinload(ModelDefinition.capabilities)
            )
        ).first()
        if (
            config is None
            or portfolio is None
            or not config.enabled
            or portfolio.status != "active"
            or not supports_automation(portfolio.agent.harness)
        ):
            items.append(
                {
                    "portfolio_id": portfolio_id,
                    "action": "rejected",
                    "reason": "Portfolio evaluator configuration is not enabled.",
                    "run": None,
                }
            )
            continue
        if portfolio_id in liquidated_ids:
            items.append(
                {
                    "portfolio_id": portfolio_id,
                    "action": "rejected",
                    "reason": "Reset this liquidated short portfolio before running evaluation.",
                    "run": None,
                }
            )
            continue
        active = _active_run(session, portfolio_id)
        if active is not None:
            items.append(
                {
                    "portfolio_id": portfolio_id,
                    "action": "existing",
                    "reason": "The portfolio already has queued or running work.",
                    "run": run_out(active),
                }
            )
            continue
        if _pending_result(session, portfolio, current_time) is not None:
            items.append(
                {
                    "portfolio_id": portfolio_id,
                    "action": "rejected",
                    "reason": "A result already targets the next effective session.",
                    "run": None,
                }
            )
            continue
        run = _new_run(config, settings, trigger_kind="manual")
        session.add(run)
        session.flush()
        items.append(
            {
                "portfolio_id": portfolio_id,
                "action": "queued",
                "reason": None,
                "run": run_out(_load_run(session, run.id)),
            }
        )
    session.commit()
    return {"items": items}


def retry_run(session: Session, *, run_id: int, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(UTC)
    source = _load_run(session, run_id)
    if source.status != "failed":
        raise AdminOpError(409, "Only failed evaluation runs can be retried")
    if source.portfolio_id in _liquidated_managed_ids(session, [source.portfolio]):
        raise AdminOpError(
            409,
            "Reset this liquidated short portfolio before retrying evaluation.",
        )
    settings = get_settings(session, lock=True)
    if not settings.enabled:
        raise AdminOpError(409, "The evaluator is paused")
    config = session.get(PortfolioEvaluatorConfig, source.portfolio_id)
    if config is None or not config.enabled or source.portfolio.status != "active":
        raise AdminOpError(409, "Portfolio evaluator configuration is not enabled")
    active = _active_run(session, source.portfolio_id)
    if active is not None:
        return {"action": "existing", "run": run_out(active)}
    if _pending_result(session, source.portfolio, current_time) is not None:
        raise AdminOpError(409, "A result already targets the next effective session")
    run = _new_run(config, settings, trigger_kind="retry", retry_of_run_id=source.id)
    session.add(run)
    session.commit()
    return {"action": "queued", "run": run_out(_load_run(session, run.id))}


def cancel_run(session: Session, *, run_id: int, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(UTC)
    run = _load_run(session, run_id, lock=True)
    if run.status == "queued":
        run.status = "cancelled"
        run.finished_at = current_time
        run.error = "Cancelled by an administrator."
    elif run.status == "running":
        run.status = "cancel_requested"
        run.error = "Cancellation requested by an administrator."
    elif run.status == "cancel_requested":
        pass
    else:
        raise AdminOpError(409, f"Evaluation run is already {run.status}")
    session.commit()
    return run_out(_load_run(session, run.id))


def _enqueue_scheduled(
    session: Session,
    settings: EvaluatorSettings,
    now: datetime,
    liquidated_ids: set[int],
) -> None:
    from .trading_calendar import NY

    local_date = now.astimezone(NY).date()
    if not is_trading_day(local_date):
        return
    enqueue_at, close = scheduled_enqueue_window(local_date, settings)
    if not enqueue_at <= now < close:
        return
    configs = session.scalars(
        select(PortfolioEvaluatorConfig)
        .where(PortfolioEvaluatorConfig.enabled.is_(True))
        .options(
            selectinload(PortfolioEvaluatorConfig.portfolio)
            .selectinload(Portfolio.agent)
            .selectinload(Agent.model)
            .selectinload(ModelDefinition.capabilities)
        )
    ).all()
    for config in configs:
        if (
            config.portfolio.status != "active"
            or config.portfolio_id in liquidated_ids
            or not supports_automation(config.portfolio.agent.harness)
            or not is_due_on(config, local_date)
        ):
            continue
        existing = session.scalars(
            select(EvaluationRun).where(
                EvaluationRun.portfolio_id == config.portfolio_id,
                EvaluationRun.trigger_kind == "scheduled",
                EvaluationRun.scheduled_for == local_date,
            )
        ).first()
        if existing is not None or _active_run(session, config.portfolio_id) is not None:
            continue
        session.add(
            _new_run(
                config,
                settings,
                trigger_kind="scheduled",
                scheduled_for=local_date,
            )
        )
    session.flush()


def _recover_stale_runs(session: Session, now: datetime) -> None:
    stale = session.scalars(
        select(EvaluationRun)
        .where(
            EvaluationRun.status.in_({"running", "cancel_requested"}),
            EvaluationRun.lease_expires_at.is_not(None),
            EvaluationRun.lease_expires_at <= now,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for run in stale:
        if run.status == "cancel_requested":
            run.status = "cancelled"
            run.finished_at = now
            run.error = "Cancelled after the worker lease expired."
        elif run.attempt_count < run.max_attempts:
            run.status = "queued"
            run.worker_id = None
            run.lease_expires_at = None
            run.error = "Worker lease expired; queued for another attempt."
        else:
            run.status = "failed"
            run.worker_id = None
            run.lease_expires_at = None
            run.finished_at = now
            run.error = "Worker lease expired and no further attempt is available."


def _cancel_archived_queued_runs(session: Session, now: datetime) -> None:
    runs = session.scalars(
        select(EvaluationRun)
        .join(EvaluationRun.portfolio)
        .where(
            EvaluationRun.status == "queued",
            Portfolio.status != "active",
        )
        .with_for_update(skip_locked=True)
    ).all()
    for run in runs:
        run.status = "cancelled"
        run.finished_at = now
        run.error = "Cancelled because the portfolio was archived."


def _cancel_liquidated_queued_runs(
    session: Session,
    liquidated_ids: set[int],
    now: datetime,
) -> None:
    if not liquidated_ids:
        return
    runs = session.scalars(
        select(EvaluationRun)
        .where(
            EvaluationRun.status == "queued",
            EvaluationRun.portfolio_id.in_(liquidated_ids),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for run in runs:
        run.status = "cancelled"
        run.finished_at = now
        run.error = "Cancelled because the short portfolio is liquidated."


def claim_runs(
    session: Session,
    *,
    worker_id: str,
    harness: str,
    harness_version: str,
    limit: int,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    short_managed = list(
        session.scalars(
            select(Portfolio)
            .join(PortfolioEvaluatorConfig)
            .where(
                Portfolio.status == "active",
                Portfolio.prompt_mode == "managed",
                Portfolio.direction == "short",
                PortfolioEvaluatorConfig.enabled.is_(True),
            )
        )
    )
    liquidated_ids = _liquidated_managed_ids(session, short_managed)
    # Serialize claims and every queue-creation path on the singleton settings
    # row. This keeps max_concurrency global across worker instances and
    # prevents manual/scheduled enqueue races.
    settings = get_settings(session, lock=True)
    _recover_stale_runs(session, current_time)
    _cancel_archived_queued_runs(session, current_time)
    _cancel_liquidated_queued_runs(session, liquidated_ids, current_time)
    _enqueue_scheduled(session, settings, current_time, liquidated_ids)

    claimed: list[EvaluationRun] = []
    if settings.enabled:
        active_count = session.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(EvaluationRun.status.in_({"running", "cancel_requested"}))
        )
        capacity = max(0, settings.max_concurrency - int(active_count or 0))
        claim_limit = min(max(0, limit), capacity)
        if claim_limit:
            rows = session.scalars(
                _run_query()
                .join(EvaluationRun.portfolio)
                .where(EvaluationRun.status == "queued", EvaluationRun.harness == harness)
                .where(Portfolio.status == "active")
                .order_by(EvaluationRun.created_at, EvaluationRun.id)
                .limit(claim_limit)
                .with_for_update(skip_locked=True)
            ).all()
            prompt_ids = sorted({run.portfolio.prompt_id for run in rows})
            if prompt_ids:
                # Prompt edits lock the same rows before checking running jobs.
                # Taking these locks before queued -> running makes the claim
                # and edit paths serialize on one prompt version.
                session.scalars(
                    select(Prompt.id).where(Prompt.id.in_(prompt_ids)).order_by(Prompt.id).with_for_update()
                ).all()
            for run in rows:
                run.status = "running"
                run.attempt_count += 1
                run.worker_id = worker_id
                run.harness_version = harness_version
                run.started_at = current_time
                run.finished_at = None
                run.lease_expires_at = current_time + timedelta(seconds=run.timeout_seconds + 60)
                claimed.append(run)
    session.commit()
    claimed_runs = [_load_run(session, run.id) for run in claimed]
    return {
        "settings": settings_out(settings),
        "runs": [_claimed_run_out(session, run) for run in claimed_runs],
    }


def run_control(session: Session, *, run_id: int) -> dict:
    run = _load_run(session, run_id)
    return {
        "status": run.status,
        "lease_expires_at": _iso(run.lease_expires_at),
    }


def submit_run(
    session: Session,
    *,
    run_id: int,
    positions: list[dict],
    note: str,
    report: str,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    preflight_run = _load_run(session, run_id)
    liquidated_before_submit = preflight_run.status in {
        "running",
        "cancel_requested",
    } and preflight_run.portfolio_id in _liquidated_managed_ids(session, [preflight_run.portfolio])
    run = session.scalars(
        select(EvaluationRun)
        .where(EvaluationRun.id == run_id)
        .options(
            selectinload(EvaluationRun.portfolio).selectinload(Portfolio.prompt),
            selectinload(EvaluationRun.portfolio).selectinload(Portfolio.agent),
        )
        .with_for_update()
    ).first()
    if run is None:
        raise AdminOpError(404, "Evaluation run not found")
    if run.status == "succeeded":
        if run.allocation_id is not None:
            output = run_out(run)
            return {"run": output, "result": output["result"]}
        if run.signal_id is not None:
            output = run_out(run)
            return {"run": output, "result": output["result"]}
    if run.status == "cancel_requested":
        run.status = "cancelled"
        run.finished_at = current_time
        run.lease_expires_at = None
        session.commit()
        raise AdminOpError(409, "Evaluation run was cancelled")
    if run.status != "running":
        raise AdminOpError(409, f"Evaluation run is {run.status}, not running")
    if run.lease_expires_at is None or current_time >= run.lease_expires_at:
        raise AdminOpError(409, "Evaluation run lease expired")
    if run.portfolio.status != "active":
        run.status = "skipped"
        run.error = "Portfolio was archived before evaluation submission."
        run.finished_at = current_time
        run.lease_expires_at = None
        session.commit()
        raise AdminOpError(409, "Portfolio is archived")
    if liquidated_before_submit:
        run.status = "skipped"
        run.error = "Short portfolio liquidated before evaluation submission."
        run.finished_at = current_time
        run.lease_expires_at = None
        session.commit()
        raise AdminOpError(
            409,
            "Reset this liquidated short portfolio before submitting an allocation.",
        )

    allocation_policy = admin_ops.get_app_settings(session)[f"{run.portfolio.prompt_mode}_allocation_policy"]
    normalized = admin_ops._normalize_positions(allocation_policy, positions)
    effective = run.scheduled_for if run.trigger_kind == "scheduled" else effective_date_for(current_time)
    assert effective is not None
    if run.portfolio.prompt_mode == "managed":
        allocation = session.scalars(
            select(Allocation).where(
                Allocation.portfolio_id == run.portfolio_id,
                Allocation.effective_date == effective,
            )
        ).first()
        if allocation is None:
            allocation = admin_ops._new_allocation(
                run.portfolio,
                normalized,
                note,
                current_time,
                effective,
            )
            session.add(allocation)
            session.flush()
            run.status = "succeeded"
            run.allocation_id = allocation.id
        else:
            run.status = "skipped"
            run.error = "An allocation already targets this effective session."
            run.allocation_id = None
        run.signal_id = None
    else:
        signal = session.scalars(
            select(Signal).where(
                Signal.portfolio_id == run.portfolio_id,
                Signal.effective_date == effective,
            )
        ).first()
        if signal is None:
            signal = admin_ops._new_signal(
                run.portfolio,
                normalized,
                note,
                current_time,
                effective,
                "integrated",
            )
            session.add(signal)
            session.flush()
            run.status = "succeeded"
            run.signal_id = signal.id
        else:
            run.status = "skipped"
            run.error = "A signal already targets this effective session."
            run.signal_id = None
        run.allocation_id = None
    run.report = report[:RUN_REPORT_MAX_LENGTH]
    run.lease_expires_at = None
    run.finished_at = current_time
    session.commit()
    output = run_out(_load_run(session, run.id))
    return {"run": output, "result": output["result"]}


def fail_run(
    session: Session,
    *,
    run_id: int,
    error: str,
    cancelled: bool = False,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    run = _load_run(session, run_id, lock=True)
    if run.status in FINISHED_STATUSES:
        return run_out(run)
    run.error = error[:RUN_ERROR_MAX_LENGTH]
    run.lease_expires_at = None
    run.worker_id = None
    if cancelled or run.status == "cancel_requested":
        run.status = "cancelled"
        run.finished_at = current_time
    elif run.attempt_count < run.max_attempts:
        run.status = "queued"
    else:
        run.status = "failed"
        run.finished_at = current_time
    session.commit()
    return run_out(_load_run(session, run.id))


def heartbeat(
    session: Session,
    *,
    instance_id: str,
    harness: str,
    status: str,
    harness_version: str | None,
    authenticated: bool,
    active_run_count: int,
    last_error: str | None,
    now: datetime | None = None,
) -> dict:
    current_time = now or datetime.now(UTC)
    session.execute(
        delete(EvaluatorInstance).where(
            EvaluatorInstance.last_heartbeat_at < current_time - timedelta(days=1)
        )
    )
    instance = session.get(EvaluatorInstance, instance_id)
    if instance is None:
        instance = EvaluatorInstance(id=instance_id, harness=harness, status=status)
        session.add(instance)
    instance.harness = harness
    instance.status = status
    instance.harness_version = harness_version
    instance.authenticated = authenticated
    instance.active_run_count = active_run_count
    instance.last_error = last_error[:RUN_ERROR_MAX_LENGTH] if last_error else None
    instance.last_heartbeat_at = current_time
    session.commit()
    return {"ok": True, "server_time": current_time.isoformat()}


def _encode_cursor(run: EvaluationRun, portfolio_id: int | None, status: str | None) -> str:
    payload = json.dumps(
        {
            "created_at": run.created_at.isoformat(),
            "id": run.id,
            "portfolio_id": portfolio_id,
            "status": status,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(
    cursor: str,
    portfolio_id: int | None,
    status: str | None,
) -> tuple[datetime, int]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if payload.get("portfolio_id") != portfolio_id or payload.get("status") != status:
            raise ValueError
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, int(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdminOpError(422, "Invalid evaluation-run cursor") from exc


def list_runs(
    session: Session,
    *,
    portfolio_id: int | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    allowed_statuses = {
        "queued",
        "running",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "failed",
        "skipped",
    }
    if status is not None and status not in allowed_statuses:
        raise AdminOpError(422, "Invalid evaluation-run status")
    limit = max(1, min(limit, 100))
    query = _run_query()
    if portfolio_id is not None:
        query = query.where(EvaluationRun.portfolio_id == portfolio_id)
    if status is not None:
        query = query.where(EvaluationRun.status == status)
    if cursor is not None:
        cursor_time, cursor_id = _decode_cursor(cursor, portfolio_id, status)
        query = query.where(
            or_(
                EvaluationRun.created_at < cursor_time,
                and_(EvaluationRun.created_at == cursor_time, EvaluationRun.id < cursor_id),
            )
        )
    rows = session.scalars(
        query.order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [run_out(run) for run in rows],
        "next_cursor": _encode_cursor(rows[-1], portfolio_id, status) if has_more and rows else None,
    }
