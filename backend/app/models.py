"""ORM models mirroring the production schema (see alembic/versions/0001_initial.py)."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("idx_auth_sessions_last_seen_at", "last_seen_at"),
        Index("idx_auth_sessions_absolute_expires_at", "absolute_expires_at"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    id_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class ModelDefinition(Base):
    """One model plus its harness-specific execution capabilities."""

    __tablename__ = "model_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    capabilities: Mapped[list["ModelHarnessCapability"]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ModelHarnessCapability.harness",
    )
    agents: Mapped[list["Agent"]] = relationship(back_populates="model")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="model")


class ModelHarnessCapability(Base):
    __tablename__ = "model_harness_capabilities"

    model_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_definitions.id", ondelete="CASCADE"), primary_key=True
    )
    harness: Mapped[str] = mapped_column(Text, primary_key=True)
    execution_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning_efforts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")

    model: Mapped[ModelDefinition] = relationship(back_populates="capabilities")


class Agent(Base):
    """A reusable model + harness + reasoning execution profile."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_definitions.id"), nullable=False)
    harness: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id", "harness"],
            ["model_harness_capabilities.model_id", "model_harness_capabilities.harness"],
            name="agents_model_harness_fkey",
        ),
        Index(
            "agents_execution_profile_key",
            model_id,
            func.coalesce(harness, ""),
            func.coalesce(reasoning_effort, ""),
            unique=True,
        ),
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model: Mapped[ModelDefinition] = relationship(back_populates="agents")
    portfolios: Mapped[list["Portfolio"]] = relationship(back_populates="agent")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="agent")


class Prompt(Base):
    """Editable strategy text plus server-enforced position sizing policy."""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    min_position_weight_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    max_position_weight_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    portfolios: Mapped[list["Portfolio"]] = relationship(back_populates="prompt")


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="portfolios_status_check"),
        CheckConstraint("cost_bps >= 0", name="portfolios_cost_bps_check"),
        CheckConstraint(
            "prompt_mode IS NULL OR prompt_mode IN ('managed', 'rebuilt')",
            name="portfolios_prompt_mode_check",
        ),
        CheckConstraint(
            "(is_benchmark AND agent_id IS NULL AND prompt_id IS NULL) OR "
            "(NOT is_benchmark AND agent_id IS NOT NULL AND prompt_id IS NOT NULL)",
            name="portfolios_identity_assignment_check",
        ),
        CheckConstraint(
            "(is_benchmark AND prompt_mode IS NULL) OR (NOT is_benchmark AND prompt_mode IS NOT NULL)",
            name="portfolios_prompt_mode_assignment_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("agents.id"), nullable=True)
    prompt_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("prompts.id"), nullable=True)
    prompt_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    is_benchmark: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    agent: Mapped[Agent | None] = relationship(back_populates="portfolios")
    prompt: Mapped[Prompt | None] = relationship(back_populates="portfolios")
    allocations: Mapped[list["Allocation"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(Allocation.effective_date, Allocation.entered_at)",
    )
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    evaluator_config: Mapped["PortfolioEvaluatorConfig | None"] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Allocation(Base):
    """One row per decision (initial or rebalance).

    Locked (= effective_date's close has passed) is derived, never stored;
    once locked, positions and effective_date are frozen — note remains editable.
    """

    __tablename__ = "allocations"
    __table_args__ = (Index("idx_allocations_portfolio_id", "portfolio_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    portfolio: Mapped[Portfolio] = relationship(back_populates="allocations")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="allocation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Position.id",
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("weight_pct >= 0", name="positions_weight_pct_check"),
        UniqueConstraint("allocation_id", "symbol", name="positions_allocation_id_symbol_key"),
        Index("idx_positions_allocation_id", "allocation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("allocations.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    weight_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    allocation: Mapped[Allocation] = relationship(back_populates="positions")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'cancel_requested', 'cancelled', "
            "'succeeded', 'failed', 'skipped')",
            name="evaluation_runs_status_check",
        ),
        CheckConstraint(
            "trigger_kind IN ('scheduled', 'manual', 'retry')",
            name="evaluation_runs_trigger_kind_check",
        ),
        CheckConstraint("attempt_count >= 0", name="evaluation_runs_attempt_count_check"),
        CheckConstraint("max_attempts BETWEEN 1 AND 5", name="evaluation_runs_max_attempts_check"),
        CheckConstraint("timeout_seconds BETWEEN 60 AND 7200", name="evaluation_runs_timeout_check"),
        Index("idx_evaluation_runs_scheduled_id", "scheduled_for", "id"),
        Index(
            "evaluation_runs_portfolio_session_key",
            "portfolio_id",
            "scheduled_for",
            unique=True,
            postgresql_where="trigger_kind = 'scheduled' AND scheduled_for IS NOT NULL",
        ),
        Index(
            "evaluation_runs_portfolio_active_key",
            "portfolio_id",
            unique=True,
            postgresql_where="status IN ('queued', 'running', 'cancel_requested')",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    model_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_definitions.id"), nullable=False)
    scheduled_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    trigger_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="scheduled")
    retry_of_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("evaluation_runs.id", ondelete="SET NULL"), nullable=True
    )
    harness: Mapped[str] = mapped_column(Text, nullable=False)
    execution_model_id: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1500")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    harness_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allocation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("allocations.id", ondelete="SET NULL"), nullable=True
    )
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    portfolio: Mapped[Portfolio] = relationship(back_populates="evaluation_runs")
    agent: Mapped[Agent] = relationship(back_populates="evaluation_runs")
    model: Mapped[ModelDefinition] = relationship(back_populates="evaluation_runs")
    allocation: Mapped[Allocation | None] = relationship()


class EvaluatorSettings(Base):
    __tablename__ = "evaluator_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="evaluator_settings_singleton_check"),
        CheckConstraint(
            "max_concurrency BETWEEN 1 AND 20",
            name="evaluator_settings_concurrency_check",
        ),
        CheckConstraint(
            "poll_seconds BETWEEN 10 AND 300",
            name="evaluator_settings_poll_check",
        ),
        CheckConstraint(
            "attempt_timeout_seconds BETWEEN 60 AND 7200",
            name="evaluator_settings_timeout_check",
        ),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 5",
            name="evaluator_settings_attempts_check",
        ),
        CheckConstraint(
            "queue_before_close_minutes BETWEEN 15 AND 240",
            name="evaluator_settings_queue_check",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    poll_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    attempt_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1500")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    queue_before_close_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="90")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PortfolioEvaluatorConfig(Base):
    __tablename__ = "portfolio_evaluator_configs"

    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    weekdays: Mapped[list[int]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    portfolio: Mapped[Portfolio] = relationship(back_populates="evaluator_config")


class EvaluatorInstance(Base):
    __tablename__ = "evaluator_instances"
    __table_args__ = (Index("idx_evaluator_instances_heartbeat", "last_heartbeat_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    harness: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    harness_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    active_run_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApiKey(Base):
    """A named credential for the MCP server. The raw key is shown once at
    creation and stored only as a SHA-256 hash; revoked keys are kept for audit."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)  # first chars, for display
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PriceCache(Base):
    """Daily adjusted-close series per USD-denominated equity or ETF."""

    __tablename__ = "price_cache"
    __table_args__ = (Index("idx_price_cache_fetched_at", "fetched_at"),)

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    series: Mapped[list] = mapped_column(JSONB, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
