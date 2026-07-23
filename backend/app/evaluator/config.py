"""Deployment-only evaluator configuration."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvaluatorRuntimeSettings:
    internal_api_url: str
    internal_mcp_url: str
    internal_token: str
    massive_api_key: str
    codex_home: Path


def load_settings() -> EvaluatorRuntimeSettings:
    port = int(os.environ.get("PORT", "8000"))
    base_url = os.environ.get("ARENA_INTERNAL_URL", f"http://127.0.0.1:{port}").rstrip("/")
    return EvaluatorRuntimeSettings(
        internal_api_url=f"{base_url}/api/internal/evaluator",
        internal_mcp_url=f"{base_url}/mcp",
        internal_token=os.environ.get("ARENA_INTERNAL_MCP_API_KEY", "").strip(),
        massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
        codex_home=Path(os.environ.get("CODEX_HOME", "/var/lib/codex")),
    )
