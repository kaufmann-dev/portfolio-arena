"""Deployment-only evaluator configuration."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def without_web_credentials(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in environment.items()
        if key not in {"DATABASE_URL", "ARENA_OIDC_CLIENT_SECRET", "ARENA_OIDC_STATE_SECRET"}
    }


@dataclass(frozen=True)
class EvaluatorRuntimeSettings:
    internal_api_url: str
    internal_mcp_url: str
    internal_token: str
    massive_api_key: str
    codex_home: Path
    muse_config_home: Path
    opencode_home: Path
    agy_home: Path


def load_settings() -> EvaluatorRuntimeSettings:
    port = int(os.environ.get("PORT", "8000"))
    base_url = os.environ.get("ARENA_INTERNAL_URL", f"http://127.0.0.1:{port}").rstrip("/")
    return EvaluatorRuntimeSettings(
        internal_api_url=f"{base_url}/api/internal/evaluator",
        internal_mcp_url=f"{base_url}/mcp",
        internal_token=os.environ.get("ARENA_INTERNAL_MCP_API_KEY", "").strip(),
        massive_api_key=os.environ.get("MASSIVE_API_KEY", "").strip(),
        codex_home=Path(os.environ.get("CODEX_HOME", "/var/lib/codex")),
        muse_config_home=Path(os.environ.get("MUSE_CONFIG_HOME", "/var/lib/muse")),
        opencode_home=Path(os.environ.get("OPENCODE_HOME", "/var/lib/opencode")),
        agy_home=Path(os.environ.get("AGY_HOME", "/var/lib/agy")).resolve(),
    )
