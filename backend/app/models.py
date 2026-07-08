"""ORM models mirroring the production schema (see alembic/versions/0001_initial.py)."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin')", name="users_role_check"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="admin")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Agent(Base):
    """One row per model/harness identity, e.g. "Claude Opus 4.8 (Claude Code)"."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    portfolios: Mapped[list["Portfolio"]] = relationship(back_populates="agent")


class Prompt(Base):
    """Prompt texts — plain editable rows; the operator versions them by slug."""

    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[int] = mapped_column(Integer, ForeignKey("agents.id"), nullable=False)
    prompt_id: Mapped[int] = mapped_column(Integer, ForeignKey("prompts.id"), nullable=False)
    cost_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    is_benchmark: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    agent: Mapped[Agent] = relationship(back_populates="portfolios")
    prompt: Mapped[Prompt] = relationship(back_populates="portfolios")
    allocations: Mapped[list["Allocation"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="(Allocation.effective_date, Allocation.entered_at)",
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
        CheckConstraint("instrument IN ('equity', 'cash')", name="positions_instrument_check"),
        UniqueConstraint("allocation_id", "symbol", name="positions_allocation_id_symbol_key"),
        Index("idx_positions_allocation_id", "allocation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    allocation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("allocations.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    instrument: Mapped[str] = mapped_column(Text, nullable=False)
    weight_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    allocation: Mapped[Allocation] = relationship(back_populates="positions")


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
    """Daily series per Yahoo symbol — equities (adjusted close) and FX pairs share this table."""

    __tablename__ = "price_cache"
    __table_args__ = (Index("idx_price_cache_fetched_at", "fetched_at"),)

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    series: Mapped[list] = mapped_column(JSONB, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
