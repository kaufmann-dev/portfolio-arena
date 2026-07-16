"""Environment and allowlist configuration for the evaluation worker."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PortfolioJob:
    slug: str
    model: str


@dataclass(frozen=True)
class EvaluatorSettings:
    arena_mcp_url: str
    arena_mcp_api_key: str
    massive_api_key: str
    codex_home: Path
    jobs: tuple[PortfolioJob, ...]
    max_concurrency: int
    poll_seconds: int
    attempt_timeout_seconds: int
    service_tier: str
    heartbeat_file: Path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def load_settings() -> EvaluatorSettings:
    config_path = Path(os.environ.get("EVALUATOR_CONFIG", "/app/evaluator.toml"))
    with config_path.open("rb") as file:
        raw = tomllib.load(file)
    jobs = tuple(
        PortfolioJob(slug=str(item["slug"]).strip(), model=str(item["model"]).strip())
        for item in raw.get("portfolios", [])
    )
    if not jobs:
        raise RuntimeError(f"No portfolios are configured in {config_path}")
    slugs = [job.slug for job in jobs]
    if len(slugs) != len(set(slugs)):
        raise RuntimeError("Evaluator portfolio slugs must be unique")

    max_concurrency = int(os.environ.get("EVALUATOR_MAX_CONCURRENCY", "5"))
    poll_seconds = int(os.environ.get("EVALUATOR_POLL_SECONDS", "60"))
    attempt_timeout = int(os.environ.get("EVALUATOR_ATTEMPT_TIMEOUT_SECONDS", "1500"))
    if max_concurrency < 1:
        raise RuntimeError("EVALUATOR_MAX_CONCURRENCY must be at least 1")
    if poll_seconds < 10:
        raise RuntimeError("EVALUATOR_POLL_SECONDS must be at least 10")
    if not 60 <= attempt_timeout <= 1500:
        raise RuntimeError("EVALUATOR_ATTEMPT_TIMEOUT_SECONDS must be between 60 and 1500")

    return EvaluatorSettings(
        arena_mcp_url=_required("ARENA_MCP_URL").rstrip("/"),
        arena_mcp_api_key=_required("ARENA_MCP_API_KEY"),
        massive_api_key=_required("MASSIVE_API_KEY"),
        codex_home=Path(os.environ.get("CODEX_HOME", "/var/lib/codex")),
        jobs=jobs,
        max_concurrency=max_concurrency,
        poll_seconds=poll_seconds,
        attempt_timeout_seconds=attempt_timeout,
        service_tier=os.environ.get("EVALUATOR_SERVICE_TIER", "fast").strip() or "fast",
        heartbeat_file=Path(os.environ.get("EVALUATOR_HEARTBEAT_FILE", "/tmp/evaluator-heartbeat")),
    )
