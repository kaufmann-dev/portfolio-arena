"""Pydantic request/response models."""

import math

from pydantic import BaseModel, Field, model_validator


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


class AllocationPolicyIn(BaseModel):
    min_position_weight_pct: float = Field(gt=0, le=100)
    max_position_weight_pct: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def validate_feasible(self):
        if self.min_position_weight_pct > self.max_position_weight_pct:
            raise ValueError("Minimum position weight cannot exceed the maximum.")
        minimum_positions = math.ceil(100 / self.max_position_weight_pct)
        maximum_positions = math.floor(100 / self.min_position_weight_pct)
        if minimum_positions > maximum_positions:
            raise ValueError("Position weight limits cannot form a portfolio totaling 100%.")
        return self


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    text: str = Field(min_length=1)
    notes: str = ""
    allocation_policy: AllocationPolicyIn


class PromptPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    text: str | None = Field(default=None, min_length=1)
    notes: str | None = None
    allocation_policy: AllocationPolicyIn | None = None


class PositionIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    weight_pct: float = Field(ge=0)
    note: str = Field(default="", max_length=2000)


class AllocationCreate(BaseModel):
    positions: list[PositionIn] = Field(min_length=1)
    note: str = ""


class AllocationUpdate(BaseModel):
    """Positions are frozen once the allocation is locked; metadata stays editable."""

    positions: list[PositionIn] | None = Field(default=None, min_length=1)
    note: str | None = None


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    agent_id: int
    prompt_id: int
    cost_bps: int | None = Field(default=None, ge=0)


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|archived)$")
    agent_id: int | None = None
    prompt_id: int | None = None
    cost_bps: int | None = Field(default=None, ge=0)


class SettingsUpdate(BaseModel):
    default_cost_bps: int = Field(ge=0)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
