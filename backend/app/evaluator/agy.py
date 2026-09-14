"""Antigravity CLI configuration and native headless output validation."""

import json
import os
import tempfile
from pathlib import Path

from .config import EvaluatorRuntimeSettings


class AgyAuthenticationRequired(RuntimeError):
    """Antigravity needs a native account login before it can claim work."""


def _write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_agy_config(settings: EvaluatorRuntimeSettings) -> None:
    # --gemini_dir isolates both configuration and native persisted login without
    # changing HOME. Only evaluator-owned settings and MCP config are replaced.
    _write_private_json(
        settings.agy_home / "antigravity-cli" / "settings.json",
        {
            "permissions": {
                "deny": [
                    "command(*)",
                    "unsandboxed(*)",
                    "read_file(*)",
                    "write_file(*)",
                    "execute_url(*)",
                ],
                "allow": [
                    "read_url(*)",
                    "mcp(massive/*)",
                    "mcp(portfolio_arena/get_portfolio)",
                    "mcp(portfolio_arena/get_effective_date)",
                    "mcp(portfolio_arena/validate_symbol)",
                    "mcp(portfolio_arena/search_symbols)",
                ],
            },
        },
    )
    _write_private_json(
        settings.agy_home / "config" / "mcp_config.json",
        {
            "mcpServers": {
                "portfolio_arena": {
                    "serverUrl": settings.internal_mcp_url,
                    "headers": {"Authorization": f"Bearer {settings.internal_token}"},
                },
                "massive": {
                    "command": "/bin/bash",
                    "args": ["-lc", "exec mcp_massive"],
                    "env": {"MASSIVE_API_KEY": settings.massive_api_key},
                },
            },
        },
    )


def agy_environment(settings: EvaluatorRuntimeSettings) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGY_", "ANTIGRAVITY_", "CASCADE_", "CODEX_", "OPENCODE_"))
    }
    for name in (
        "OPENAI_API_KEY",
        "META_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GEMINI_BASE_URL",
    ):
        environment.pop(name, None)
    environment["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    environment["ARENA_INTERNAL_MCP_API_KEY"] = settings.internal_token
    environment["MASSIVE_API_KEY"] = settings.massive_api_key
    return environment


def agy_command(settings: EvaluatorRuntimeSettings, *arguments: str) -> list[str]:
    return ["agy", f"--gemini_dir={settings.agy_home}", *arguments]


def agy_proposal_schema() -> dict:
    schema = json.loads(Path(__file__).with_name("proposal.schema.json").read_text())
    # agy converts this to a Gemini function declaration. Nullable types work,
    # but a JSON null inside an enum becomes an invalid empty enum value.
    # Keep the nullable type as a generation constraint; Proposal validation
    # still enforces the exact allowed reasons and their relationship to status.
    reason = schema["properties"]["blocked_reason"]
    reason.pop("enum")
    reason["description"] = (
        "Use portfolio_unavailable or research_unavailable when status is blocked; null otherwise."
    )
    return schema


def agy_models(stdout: bytes) -> set[str]:
    """Native `agy models` emits a slug and display name separated by a tab."""
    models = set()
    for line in stdout.decode().splitlines():
        if not line.strip():
            continue
        slug, separator, name = line.partition("\t")
        if not separator or not slug or not name.strip() or any(char.isspace() for char in slug):
            raise ValueError("Antigravity returned an invalid model listing")
        if slug in models:
            raise ValueError("Antigravity returned a duplicate model ID")
        models.add(slug)
    return models


def agy_result(stdout: bytes, stderr: bytes) -> dict:
    # Verified against agy 1.2.2: a print timeout can exit 0 with SUCCESS, even
    # without an answer. Reject that diagnostic and require structured_output;
    # response can include extra prose and internal task-completion fields.
    if b"[agy] print timeout" in stderr:
        raise ValueError("Antigravity timed out before completing its structured result")
    result = json.loads(stdout)
    if not isinstance(result, dict) or result.get("status") != "SUCCESS" or result.get("error"):
        raise ValueError("Antigravity evaluation did not complete successfully")
    structured = result.get("structured_output")
    if not isinstance(structured, dict):
        raise ValueError("Antigravity completed without a structured result")
    return structured
