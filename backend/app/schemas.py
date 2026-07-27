"""Pydantic request/response models."""

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .services.prompt_policy import validate_wrapper_prompt


class CurrentUser(BaseModel):
    display_name: str


class ModelHarnessCapabilityIn(BaseModel):
    harness: str = Field(min_length=1, max_length=50)
    execution_model_id: str = Field(min_length=1, max_length=200)
    reasoning_efforts: list[str] = Field(max_length=20)


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    notes: str = ""
    capabilities: list[ModelHarnessCapabilityIn] = Field(max_length=20)


class ModelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = None
    capabilities: list[ModelHarnessCapabilityIn] | None = Field(default=None, max_length=20)


class AgentCreate(BaseModel):
    model_id: int
    harness: str | None = Field(default=None, max_length=50)
    reasoning_effort: str | None = Field(default=None, max_length=50)
    slug: str | None = None
    notes: str = ""


class AgentPatch(BaseModel):
    model_id: int
    harness: str | None = Field(default=None, max_length=50)
    reasoning_effort: str | None = Field(default=None, max_length=50)
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
    prompt_mode: Literal["managed", "rebuilt"]
    cost_bps: int | None = Field(default=None, ge=0)


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|archived)$")
    agent_id: int | None = None
    prompt_id: int | None = None
    prompt_mode: Literal["managed", "rebuilt"] | None = None
    cost_bps: int | None = Field(default=None, ge=0)


class SettingsUpdate(BaseModel):
    default_cost_bps: int = Field(ge=0)
    managed_wrapper_prompt: str = Field(min_length=1)
    rebuilt_wrapper_prompt: str = Field(min_length=1)

    @field_validator("managed_wrapper_prompt", "rebuilt_wrapper_prompt")
    @classmethod
    def validate_wrapper(cls, value: str) -> str:
        try:
            return validate_wrapper_prompt(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from None


class EvaluatorSettingsUpdate(BaseModel):
    enabled: bool
    max_concurrency: int = Field(ge=1, le=20)
    poll_seconds: int = Field(ge=10, le=300)
    attempt_timeout_seconds: int = Field(ge=60, le=1500)
    max_attempts: int = Field(ge=1, le=5)
    queue_before_close_minutes: int = Field(ge=15, le=240)


class PortfolioEvaluatorConfigUpdate(BaseModel):
    enabled: bool
    weekdays: list[int] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_weekdays(self):
        if len(self.weekdays) != len(set(self.weekdays)):
            raise ValueError("Evaluator weekdays must be unique.")
        if any(day < 0 or day > 4 for day in self.weekdays):
            raise ValueError("Evaluator weekdays must be between 0 and 4.")
        return self


class EvaluationRunsCreate(BaseModel):
    portfolio_ids: list[int] = Field(min_length=1, max_length=100)


class EvaluatorHeartbeatIn(BaseModel):
    instance_id: str = Field(min_length=1, max_length=64)
    harness: str = Field(min_length=1, max_length=50)
    status: str = Field(min_length=1, max_length=50)
    harness_version: str | None = Field(default=None, max_length=200)
    authenticated: bool
    active_run_count: int = Field(ge=0, le=100)
    last_error: str | None = Field(default=None, max_length=4000)


class EvaluatorClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    harness: str = Field(min_length=1, max_length=50)
    harness_version: str = Field(min_length=1, max_length=200)
    limit: int = Field(ge=1, le=20)


class EvaluatorRunSubmitIn(BaseModel):
    positions: list[PositionIn] = Field(min_length=1)
    note: str = Field(max_length=4000)
    report: str = Field(max_length=20_000)


class EvaluatorRunFailIn(BaseModel):
    error: str = Field(min_length=1, max_length=4000)
    cancelled: bool = False


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
