"""OpenCode provider configuration and native CLI output decoding."""

import json
import os
from pathlib import Path

from .config import EvaluatorRuntimeSettings, without_web_credentials


def prepare_opencode(settings: EvaluatorRuntimeSettings) -> None:
    for directory in ("config", "data", "cache", "state"):
        (settings.opencode_home / directory).mkdir(parents=True, exist_ok=True, mode=0o700)


def opencode_environment(settings: EvaluatorRuntimeSettings) -> dict[str, str]:
    # The dedicated XDG config retains native provider settings and auth plugins.
    # Ignore inherited OpenCode overrides so they cannot select a foreign home,
    # session database, permissions, or configuration for evaluator attempts.
    environment = {
        key: value
        for key, value in without_web_credentials(os.environ).items()
        if not key.startswith("OPENCODE_")
    }
    environment.pop("CODEX_API_KEY", None)
    environment.pop("CODEX_HOME", None)
    for kind in ("config", "data", "cache", "state"):
        environment[f"XDG_{kind.upper()}_HOME"] = str(settings.opencode_home / kind)
    environment["ARENA_INTERNAL_MCP_API_KEY"] = settings.internal_token
    environment["MASSIVE_API_KEY"] = settings.massive_api_key
    environment["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "true"
    environment["OPENCODE_DISABLE_CLAUDE_CODE"] = "true"
    environment["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "true"
    environment["OPENCODE_ENABLE_EXA"] = "true"
    permissions = {
        "*": "deny",
        "webfetch": "allow",
        "websearch": "allow",
        "massive_*": "allow",
        "portfolio_arena_get_portfolio": "allow",
        "portfolio_arena_get_effective_date": "allow",
        "portfolio_arena_validate_symbol": "allow",
        "portfolio_arena_search_symbols": "allow",
    }
    environment["OPENCODE_PERMISSION"] = json.dumps(permissions)
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "snapshot": False,
            "agent": {
                "arena-evaluator": {
                    "description": "Research a Portfolio Arena allocation and return its structured result.",
                    "mode": "primary",
                    "permission": permissions,
                }
            },
            "mcp": {
                "portfolio_arena": {
                    "type": "remote",
                    "url": settings.internal_mcp_url,
                    "headers": {"Authorization": "Bearer {env:ARENA_INTERNAL_MCP_API_KEY}"},
                    "oauth": False,
                    "enabled": True,
                },
                "massive": {
                    "type": "local",
                    "command": ["/bin/bash", "-lc", "exec mcp_massive"],
                    "environment": {"MASSIVE_API_KEY": "{env:MASSIVE_API_KEY}"},
                    "enabled": True,
                },
            },
        }
    )
    return environment


def opencode_command(model: str, variant: str | None, workspace: Path) -> list[str]:
    command = [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        model,
        "--agent",
        "arena-evaluator",
        "--dir",
        str(workspace),
    ]
    if variant is not None:
        command.extend(["--variant", variant])
    return command


def opencode_models(stdout: bytes) -> dict[str, dict]:
    """Decode `models --verbose`: provider/model lines followed by JSON objects.

    This format is emitted by OpenCode 1.18.9's ModelsCommand. Do not parse JSON
    with brace counting: model descriptions can themselves contain braces.
    """
    remaining = stdout.decode().strip()
    decoder = json.JSONDecoder()
    models = {}
    while remaining:
        execution_id, separator, remaining = remaining.partition("\n")
        provider, slash, model_id = execution_id.partition("/")
        if not separator or not slash or not provider or not model_id or execution_id in models:
            raise ValueError("OpenCode returned an invalid model listing")
        model, end = decoder.raw_decode(remaining.lstrip())
        remaining = remaining.lstrip()[end:].strip()
        if not isinstance(model, dict) or not isinstance(model.get("variants", {}), dict):
            raise ValueError("OpenCode returned invalid model metadata")
        models[execution_id] = model
    return models


def validate_opencode_model(models: dict[str, dict], execution_id: str, variant: str | None) -> None:
    model = models.get(execution_id)
    if model is None:
        raise ValueError(
            f"OpenCode model {execution_id} is unavailable; check its provider configuration and login"
        )
    if model.get("capabilities", {}).get("toolcall") is not True:
        raise ValueError(f"OpenCode model {execution_id} does not support the required research tools")
    if variant is not None:
        variants = model.get("variants", {})
        definition = variants.get(variant)
        if not isinstance(definition, dict) or definition.get("disabled") is True:
            raise ValueError(f"OpenCode variant {variant} is unavailable for {execution_id}")


def opencode_result(stdout: bytes) -> str:
    """Accept only the last completed root assistant message's terminal text.

    OpenCode 1.18.9 emits completed text parts and step_finish events. A step
    ending in tool-calls is intermediate; length/error finishes are not success.
    """
    root_session = None
    current_message = None
    terminal_message = None
    finish_reason = None
    messages: dict[str, dict[str, str]] = {}
    for line in stdout.decode().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or not isinstance(record.get("sessionID"), str):
            raise ValueError("OpenCode returned an invalid event")
        if root_session is None:
            root_session = record["sessionID"]
        if record["sessionID"] != root_session:
            continue
        event_type = record.get("type")
        if event_type == "error":
            error = record.get("error", {})
            detail = error.get("data", {}).get("message") if isinstance(error, dict) else None
            raise ValueError(f"OpenCode evaluation failed: {detail or str(error)}")
        if event_type not in {"step_start", "step_finish", "text"}:
            continue
        part = record.get("part")
        if not isinstance(part, dict) or part.get("sessionID") != root_session:
            raise ValueError("OpenCode returned an invalid assistant part")
        message_id = part.get("messageID")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("OpenCode returned an assistant part without a message ID")
        if event_type == "step_start":
            current_message = message_id
            terminal_message = finish_reason = None
        elif event_type == "step_finish":
            if message_id != current_message:
                raise ValueError("OpenCode completed an unexpected assistant message")
            terminal_message = message_id
            finish_reason = part.get("reason")
        else:
            part_id, value = part.get("id"), part.get("text")
            if (
                not isinstance(part_id, str)
                or not isinstance(value, str)
                or not isinstance(part.get("time"), dict)
                or part["time"].get("end") is None
            ):
                raise ValueError("OpenCode returned an incomplete text part")
            messages.setdefault(message_id, {})[part_id] = value
    if terminal_message is None or terminal_message != current_message or finish_reason != "stop":
        raise ValueError(f"OpenCode completed without a successful terminal result ({finish_reason})")
    result = "".join(messages.get(terminal_message, {}).values()).strip()
    if not result:
        raise ValueError("OpenCode completed without terminal assistant text")
    return result
