"""Pydantic request/response models."""

from pydantic import BaseModel, Field


class CurrentUser(BaseModel):
    email: str


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    notes: str = ""


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    text: str = Field(min_length=1)
    notes: str = ""


class PromptPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    text: str | None = Field(default=None, min_length=1)
    notes: str | None = None


class PositionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    weight_pct: float = Field(ge=0)
    note: str = Field(default="", max_length=2000)


class AllocationCreate(BaseModel):
    prompt_id: int
    positions: list[PositionIn] = Field(min_length=1)
    note: str = ""


class AllocationUpdate(BaseModel):
    """Positions are frozen once the allocation is locked; metadata stays editable."""

    prompt_id: int | None = None
    positions: list[PositionIn] | None = Field(default=None, min_length=1)
    note: str | None = None


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    agent_id: int
    cost_bps: int | None = Field(default=None, ge=0)


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|archived)$")
    agent_id: int | None = None
    cost_bps: int | None = Field(default=None, ge=0)


class SettingsUpdate(BaseModel):
    default_cost_bps: int = Field(ge=0)
