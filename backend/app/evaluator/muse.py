"""Muse Code CLI configuration and headless result decoding."""

import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path

from .config import EvaluatorRuntimeSettings


def muse_environment(settings: EvaluatorRuntimeSettings) -> dict[str, str]:
    environment = os.environ.copy()
    environment["XDG_CONFIG_HOME"] = str(settings.muse_config_home)
    environment["XDG_DATA_HOME"] = str(settings.muse_config_home / "data")
    environment["MASSIVE_API_KEY"] = settings.massive_api_key
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "MUSE_AUTH_PATH", "META_BASE_URL"):
        environment.pop(name, None)
    return environment


def write_muse_config(settings: EvaluatorRuntimeSettings) -> None:
    directory = settings.muse_config_home / "muse"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {
        "schema_version": 1,
        "mcp_servers": {
            "portfolio_arena": {
                "transport": "streamable_http",
                "url": settings.internal_mcp_url,
                "headers": {"Authorization": f"Bearer {settings.internal_token}"},
                "mode": "required",
            },
            "massive": {
                "transport": "stdio",
                "command": "/bin/bash",
                "args": ["-lc", "exec mcp_massive"],
                "env": {"MASSIVE_API_KEY": settings.massive_api_key},
                "mode": "required",
            },
        },
    }
    # Muse accepts literal HTTP headers. Keep the generated credential-bearing
    # file private, and preserve the CLI-owned auth.json alongside it.
    path = directory / "settings.json"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as handle:
            temporary_path = Path(handle.name)
            json.dump(config, handle)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def muse_credential(settings: EvaluatorRuntimeSettings) -> str | None:
    if key := os.environ.get("META_API_KEY", "").strip():
        return key
    path = settings.muse_config_home / "muse" / "auth.json"
    try:
        credentials = json.loads(path.read_text())["providers"]["meta"]
        key = credentials.get("api_key") or credentials.get("access_token")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return key.strip() if isinstance(key, str) and key.strip() else None


async def fetch_muse_catalog(settings: EvaluatorRuntimeSettings) -> dict:
    """Discover through the native CLI so it owns login and subscription exchange.

    A fresh data directory prevents stale catalogs from making a failed login
    appear successful. The handshake creates no session and starts no inference.
    """
    if muse_credential(settings) is None:
        raise ValueError("Muse authentication is required to import Meta models")
    with tempfile.TemporaryDirectory(prefix="arena-muse-catalog-") as directory:
        data_directory = Path(directory) / "data"
        environment = muse_environment(settings)
        environment["XDG_DATA_HOME"] = str(data_directory)
        process = await asyncio.create_subprocess_exec(
            "muse",
            "serve",
            "--no-session-log",
            "--disable-shell",
            "--disable-write",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=directory,
            env=environment,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(30):
                initialize = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "portfolio_arena", "version": "1"}},
                }
                process.stdin.write((json.dumps(initialize) + "\n").encode())
                await process.stdin.drain()
                while line := await process.stdout.readline():
                    try:
                        response = json.loads(line)
                    except ValueError:
                        raise RuntimeError("Muse returned an invalid catalog discovery response") from None
                    if isinstance(response, dict) and response.get("id") == 1:
                        if "error" in response or "result" not in response:
                            raise RuntimeError("Muse could not initialize catalog discovery")
                        break
                else:
                    raise RuntimeError("Muse closed before completing catalog discovery")
                process.stdin.write(b'{"jsonrpc":"2.0","method":"initialized"}\n')
                await process.stdin.drain()
                process.stdin.close()
                await process.wait()
                if process.returncode != 0:
                    raise RuntimeError("Muse catalog discovery failed")
        except TimeoutError:
            raise RuntimeError("Muse catalog discovery timed out") from None
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()

        data = []
        for path in (data_directory / "muse" / "model-catalog").glob("*.json"):
            try:
                catalog = json.loads(path.read_text())
                if (
                    catalog.get("schema_version") != 1
                    or catalog.get("provider_id") != "meta"
                    or catalog.get("source") != "provider_catalog"
                ):
                    continue
                for row in catalog["rows"]:
                    if row.get("provider_id") != "meta" or row.get("visibility") != "visible":
                        continue
                    data.append(
                        {
                            "id": row["model_id"],
                            "metadata": {
                                "muse-code": {
                                    "name": row["display_label"],
                                    "description": row.get("description"),
                                    "is_hidden": False,
                                    "variants": {
                                        variant["tier"]: {}
                                        for variant in row.get("reasoning_effort_variants", [])
                                    },
                                }
                            },
                        }
                    )
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                raise RuntimeError("Muse wrote an invalid model catalog") from None
        if not data:
            raise RuntimeError("Muse could not load a fresh Meta model catalog; check the Muse login")
        return {"data": data}


def muse_command(model: str, effort: str | None, workspace: Path, prompt_path: Path) -> list[str]:
    command = [
        "muse",
        "exec",
        "--json",
        "--provider",
        "meta",
        "--model",
        model,
        "--workspace",
        str(workspace),
        "--prompt-file",
        str(prompt_path),
        "--no-session-log",
        "--no-foreign-personal-context",
        "--approval-mode",
        "never",
        "--disable-write",
        "--disable-shell",
    ]
    if effort is not None:
        command.extend(["--reasoning-effort", effort])
    return command


def muse_result(stdout: bytes) -> str:
    """Read the root turn's terminal text, never a tool or subagent's output."""
    root_run_id = None
    terminal = None
    for line in stdout.decode().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        payload = record.get("payload", {})
        event_type = record.get("payload_type")
        if event_type == "run.lifecycle.started" and root_run_id is None:
            root_run_id = payload["run_stream"]["id"]
        if (
            event_type in {"run.terminal.completed", "run.terminal.failed", "run.terminal.cancelled"}
            and root_run_id is not None
            and payload.get("run_stream", {}).get("id") == root_run_id
        ):
            if terminal is not None:
                raise ValueError("Muse returned multiple terminal results for one evaluation")
            terminal = payload
    if terminal is None:
        raise ValueError("Muse completed without a terminal evaluation result")
    if terminal.get("terminal") != "completed":
        raise ValueError(f"Muse evaluation failed: {terminal.get('reason') or terminal.get('terminal')}")
    result = terminal.get("text", "").strip()
    if result.startswith("```json\n") and result.endswith("\n```"):
        result = result[len("```json\n") : -len("\n```")].strip()
    return result
