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
    meta_portfolio_sets: Mapped[list["MetaPortfolioSet"]] = relationship(back_populates="agent")
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="agent")


class Prompt(Base):
    """Stable strategy identity whose editable fields live in immutable versions."""

    __tablename__ = "prompts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="prompts_status_check",
        ),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL)",
            name="prompts_archive_state_check",
        ),
        CheckConstraint(
            "context_scope IN ('portfolio', 'arena')",
            name="prompts_context_scope_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    context_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="portfolio")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "prompt_versions.id",
            name="prompts_current_version_id_fkey",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    current_version: Mapped["PromptVersion | None"] = relationship(
        foreign_keys=[current_version_id],
        lazy="joined",
        post_update=True,
    )
    versions: Mapped[list["PromptVersion"]] = relationship(
        back_populates="prompt",
        cascade="all, delete-orphan",
        foreign_keys="PromptVersion.prompt_id",
        order_by="PromptVersion.version",
        passive_deletes=True,
    )
    portfolios: Mapped[list["Portfolio"]] = relationship(back_populates="prompt")
    meta_portfolio_sets: Mapped[list["MetaPortfolioSet"]] = relationship(back_populates="prompt")

    @property
    def name(self) -> str:
        return self._required_current_version().name

    @property
    def mode(self) -> str:
        return self._required_current_version().mode

    @property
    def direction(self) -> str:
        return self._required_current_version().direction

    @property
    def managed_long_text(self) -> str | None:
        return self._required_current_version().managed_long_text

    @property
    def managed_short_text(self) -> str | None:
        return self._required_current_version().managed_short_text

    @property
    def rebuilt_long_text(self) -> str | None:
        return self._required_current_version().rebuilt_long_text

    @property
    def rebuilt_short_text(self) -> str | None:
        return self._required_current_version().rebuilt_short_text

    def text_for(self, mode: str, direction: str) -> str:
        return self._required_current_version().text_for(mode, direction)

    @property
    def notes(self) -> str:
        return self._required_current_version().notes

    def _required_current_version(self) -> "PromptVersion":
        if self.current_version is None:
            raise RuntimeError(f"Prompt {self.id} has no current version")
        return self.current_version


class PromptVersion(Base):
    """One immutable snapshot of every user-editable prompt field."""

    __tablename__ = "prompt_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="prompt_versions_version_check"),
        CheckConstraint(
            "mode IN ('managed', 'rebuilt', 'both')",
            name="prompt_versions_mode_check",
        ),
        CheckConstraint(
            "direction IN ('long', 'short', 'both')",
            name="prompt_versions_direction_check",
        ),
        CheckConstraint(
            "((mode IN ('managed', 'both') AND direction IN ('long', 'both') "
            "AND managed_long_text IS NOT NULL AND btrim(managed_long_text) <> '') OR "
            "(NOT (mode IN ('managed', 'both') AND direction IN ('long', 'both')) "
            "AND managed_long_text IS NULL)) AND "
            "((mode IN ('managed', 'both') AND direction IN ('short', 'both') "
            "AND managed_short_text IS NOT NULL AND btrim(managed_short_text) <> '') OR "
            "(NOT (mode IN ('managed', 'both') AND direction IN ('short', 'both')) "
            "AND managed_short_text IS NULL)) AND "
            "((mode IN ('rebuilt', 'both') AND direction IN ('long', 'both') "
            "AND rebuilt_long_text IS NOT NULL AND btrim(rebuilt_long_text) <> '') OR "
            "(NOT (mode IN ('rebuilt', 'both') AND direction IN ('long', 'both')) "
            "AND rebuilt_long_text IS NULL)) AND "
            "((mode IN ('rebuilt', 'both') AND direction IN ('short', 'both') "
            "AND rebuilt_short_text IS NOT NULL AND btrim(rebuilt_short_text) <> '') OR "
            "(NOT (mode IN ('rebuilt', 'both') AND direction IN ('short', 'both')) "
            "AND rebuilt_short_text IS NULL))",
            name="prompt_versions_mode_texts_check",
        ),
        UniqueConstraint(
            "prompt_id",
            "version",
            name="prompt_versions_prompt_id_version_key",
        ),
        Index("idx_prompt_versions_prompt_id", "prompt_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("prompts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    managed_long_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    managed_short_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rebuilt_long_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rebuilt_short_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    restored_from_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("prompt_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    prompt: Mapped[Prompt] = relationship(
        back_populates="versions",
        foreign_keys=[prompt_id],
    )
    restored_from: Mapped["PromptVersion | None"] = relationship(
        foreign_keys=[restored_from_version_id],
        remote_side=[id],
    )

    def text_for(self, mode: str, direction: str) -> str:
        if mode not in {"managed", "rebuilt"}:
            raise ValueError("Prompt mode must be 'managed' or 'rebuilt'.")
        if direction not in {"long", "short"}:
            raise ValueError("Portfolio direction must be 'long' or 'short'.")
        if self.mode not in {mode, "both"} or self.direction not in {direction, "both"}:
            raise ValueError(f"Prompt version {self.version} does not support {direction} {mode} portfolios.")
        text = getattr(self, f"{mode}_{direction}_text")
        if text is None:
            raise RuntimeError(f"Prompt version {self.version} is missing {direction} {mode} strategy text.")
        return text


class MetaPortfolioSet(Base):
    """One atomic four-cell family of arena-synthesis portfolios."""

    __tablename__ = "meta_portfolio_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    family_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("prompts.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    agent: Mapped[Agent] = relationship(back_populates="meta_portfolio_sets")
    prompt: Mapped[Prompt] = relationship(back_populates="meta_portfolio_sets")
    portfolios: Mapped[list["Portfolio"]] = relationship(
        back_populates="meta_set",
        passive_deletes=True,
        order_by="Portfolio.id",
    )


class Portfolio(Base):
    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="portfolios_status_check"),
        CheckConstraint("cost_bps >= 0", name="portfolios_cost_bps_check"),
        CheckConstraint(
            "prompt_mode IN ('managed', 'rebuilt')",
            name="portfolios_prompt_mode_check",
        ),
        CheckConstraint(
            "direction IN ('long', 'short')",
            name="portfolios_direction_check",
        ),
        UniqueConstraint(
            "meta_set_id",
            "prompt_mode",
            "direction",
            name="portfolios_meta_set_cell_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    prompt_id: Mapped[int] = mapped_column(Integer, ForeignKey("prompts.id"), nullable=False)
    meta_set_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("meta_portfolio_sets.id", ondelete="SET NULL"),
        nullable=True,
    )
    prompt_mode: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    cost_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    founding_v2: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    agent: Mapped[Agent] = relationship(back_populates="portfolios")
    prompt: Mapped[Prompt] = relationship(back_populates="portfolios")
    meta_set: Mapped[MetaPortfolioSet | None] = relationship(back_populates="portfolios")
    allocations: Mapped[list["Allocation"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(Allocation.effective_date, Allocation.entered_at)",
    )
    signals: Mapped[list["Signal"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(Signal.effective_date, Signal.entered_at)",
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


class Signal(Base):
    """One independent rebuilt-portfolio signal for one effective close."""

    __tablename__ = "signals"
    __table_args__ = (
        CheckConstraint(
            "provenance IN ('integrated', 'browser_admin', 'mcp')",
            name="signals_provenance_check",
        ),
        UniqueConstraint(
            "portfolio_id",
            "effective_date",
            name="signals_portfolio_id_effective_date_key",
        ),
        Index("idx_signals_portfolio_id", "portfolio_id"),
        Index("idx_signals_effective_date_id", "effective_date", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    portfolio: Mapped[Portfolio] = relationship(back_populates="signals")
    positions: Mapped[list["SignalPosition"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SignalPosition.id",
    )


class SignalPosition(Base):
    __tablename__ = "signal_positions"
    __table_args__ = (
        CheckConstraint("weight_pct >= 0", name="signal_positions_weight_pct_check"),
        UniqueConstraint("signal_id", "symbol", name="signal_positions_signal_id_symbol_key"),
        Index("idx_signal_positions_signal_id", "signal_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("signals.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    weight_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    signal: Mapped[Signal] = relationship(back_populates="positions")


class MetaBatch(Base):
    """Frozen normal-arena inputs shared by every meta run for one session."""

    __tablename__ = "meta_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('waiting', 'ready', 'insufficient', 'failed')",
            name="meta_batches_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="waiting")
    source_portfolio_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, server_default="[]")
    due_source_portfolio_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, server_default="[]")
    target_portfolio_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, server_default="[]")
    pending_target_portfolio_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(back_populates="meta_batch")


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
        CheckConstraint(
            "NOT (allocation_id IS NOT NULL AND signal_id IS NOT NULL)",
            name="evaluation_runs_result_exclusive_check",
        ),
        Index("idx_evaluation_runs_scheduled_id", "scheduled_for", "id"),
        Index("idx_evaluation_runs_meta_batch_id", "meta_batch_id"),
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
    meta_batch_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("meta_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    signal_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
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
    meta_batch: Mapped[MetaBatch | None] = relationship(back_populates="evaluation_runs")
    allocation: Mapped[Allocation | None] = relationship()
    signal: Mapped[Signal | None] = relationship()


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
    """Daily total-return close series per USD-denominated equity or ETF."""

    __tablename__ = "price_cache"
    __table_args__ = (Index("idx_price_cache_fetched_at", "fetched_at"),)

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    series: Mapped[list] = mapped_column(JSONB, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
