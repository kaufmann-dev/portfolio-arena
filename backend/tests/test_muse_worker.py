"""Muse CLI boundaries, validation, and evaluation process cleanup."""

import asyncio
import json
import os
import signal
import stat
import sys
from contextlib import suppress
from pathlib import Path

import pytest

from app.evaluator import worker
from app.evaluator.config import EvaluatorRuntimeSettings
from app.evaluator.muse import muse_credential, muse_environment, muse_result, write_muse_config


def _settings(tmp_path):
    return EvaluatorRuntimeSettings(
        internal_api_url="http://127.0.0.1:8000/api/internal/evaluator",
        internal_mcp_url="http://127.0.0.1:8000/mcp",
        internal_token="internal-secret",
        massive_api_key="massive-secret",
        codex_home=tmp_path / "codex",
        muse_config_home=tmp_path / "muse-config",
    )


def _run(*, effort="high", timeout_seconds=300):
    return worker.ClaimedRun(
        id=17,
        portfolio={"id": 2, "slug": "muse-portfolio", "name": "Muse Portfolio"},
        trigger_kind="manual",
        harness="muse",
        execution_model_id="muse-spark-1.3",
        reasoning_effort=effort,
        timeout_seconds=timeout_seconds,
        execution_prompt="Evaluate the portfolio using its assigned research instructions.",
    )


def _proposal():
    return {
        "status": "proposal",
        "blocked_reason": None,
        "positions": [{"symbol": "AAPL", "weight_pct": 100, "note": "Supported thesis."}],
        "note": "Keep the position.",
        "report": "Research completed.",
        "error": "",
    }


def _event(event_type, run_id="root", **payload):
    # Envelope and run_stream fields verified against `muse exec --provider echo
    # --json` from the official Muse 1.1.1-R2514.1 binary.
    return {
        "schema_version": 1,
        "stream": {"kind": "session", "id": "session"},
        "payload_type": event_type,
        "payload_schema_version": 1,
        "payload": {"run_stream": {"kind": "run", "id": run_id}, **payload},
    }


def _jsonl(*records):
    return ("\n".join(json.dumps(record) for record in records) + "\n").encode()


def _result_stream(result):
    return _jsonl(
        _event("run.lifecycle.started", kind="run_started"),
        _event("run.terminal.completed", terminal="completed", reason=None, text=json.dumps(result)),
    )


def test_muse_reads_root_terminal_result_and_ignores_subagent_and_tool_text():
    result = json.dumps(_proposal())
    output = _jsonl(
        _event("run.lifecycle.started"),
        _event("run.lifecycle.started", "child"),
        _event("run.output.delta", text='{"status":"blocked"}'),
        _event("task.lifecycle.completed", text='{"status":"blocked"}'),
        _event("run.terminal.completed", "child", terminal="completed", text="child result"),
        _event("run.terminal.completed", terminal="completed", text=f"```json\n{result}\n```"),
        _event("run.terminal.failed", "other-child", terminal="failed", reason="Child failure."),
    )

    assert muse_result(output) == result


@pytest.mark.parametrize(
    "records,error",
    [
        ([_event("run.output.delta", text=json.dumps(_proposal()))], "without a terminal"),
        (
            [
                _event("run.lifecycle.started"),
                _event("run.terminal.completed", "child", terminal="completed", text="{}"),
            ],
            "without a terminal",
        ),
        (
            [
                _event("run.lifecycle.started"),
                _event("run.terminal.failed", terminal="failed", reason="Provider unavailable."),
            ],
            "Provider unavailable",
        ),
        (
            [
                _event("run.lifecycle.started"),
                _event("run.terminal.cancelled", terminal="cancelled", reason="Stopped."),
            ],
            "Stopped",
        ),
        (
            [
                _event("run.lifecycle.started"),
                _event("run.terminal.completed", terminal="completed", text="{}"),
                _event("run.terminal.completed", terminal="completed", text="{}"),
            ],
            "multiple terminal",
        ),
    ],
)
def test_muse_requires_one_successful_root_terminal(records, error):
    with pytest.raises(ValueError, match=error):
        muse_result(_jsonl(*records))


def test_muse_config_keeps_credentials_private_and_preserves_login(tmp_path):
    settings = _settings(tmp_path)
    directory = settings.muse_config_home / "muse"
    directory.mkdir(parents=True)
    auth_path = directory / "auth.json"
    auth_path.write_text('{"providers":{"meta":{"access_token":"saved-login"}}}')
    config_path = directory / "settings.json"
    config_path.write_text("{}")
    config_path.chmod(0o644)

    write_muse_config(settings)

    config = json.loads(config_path.read_text())
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert auth_path.read_text() == '{"providers":{"meta":{"access_token":"saved-login"}}}'
    arena = config["mcp_servers"]["portfolio_arena"]
    assert arena["url"] == settings.internal_mcp_url
    assert arena["headers"] == {"Authorization": "Bearer internal-secret"}
    assert arena["mode"] == "required"
    assert config["mcp_servers"]["massive"]["mode"] == "required"
    assert config["mcp_servers"]["massive"]["env"] == {"MASSIVE_API_KEY": "massive-secret"}


def test_muse_environment_uses_own_login_and_preserves_meta_key(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "MUSE_AUTH_PATH", "META_BASE_URL"):
        monkeypatch.setenv(name, "foreign-setting")
    monkeypatch.setenv("META_API_KEY", "meta-secret")
    settings = _settings(tmp_path)

    environment = muse_environment(settings)

    assert environment["XDG_CONFIG_HOME"] == str(settings.muse_config_home)
    assert environment["XDG_DATA_HOME"] == str(settings.muse_config_home / "data")
    assert environment["META_API_KEY"] == "meta-secret"
    assert environment["MASSIVE_API_KEY"] == "massive-secret"
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY", "MUSE_AUTH_PATH", "META_BASE_URL"} & environment.keys()


@pytest.mark.parametrize("credential_field", ["api_key", "access_token"])
def test_muse_authenticates_with_meta_api_key_or_persisted_login(tmp_path, monkeypatch, credential_field):
    settings = _settings(tmp_path)
    monkeypatch.delenv("META_API_KEY", raising=False)
    directory = settings.muse_config_home / "muse"
    directory.mkdir(parents=True)
    assert muse_credential(settings) is None
    path = directory / "auth.json"
    path.write_text(json.dumps({"providers": {"meta": {credential_field: " saved-secret "}}}))
    assert muse_credential(settings) == "saved-secret"
    monkeypatch.setenv("META_API_KEY", " environment-secret ")
    assert muse_credential(settings) == "environment-secret"
    monkeypatch.delenv("META_API_KEY")
    path.write_text("invalid login file")
    assert muse_credential(settings) is None


def _fake_muse(monkeypatch, output, *, captured=None, exit_code=0, stderr=b""):
    class Process:
        pid = 99999999
        returncode = exit_code

        async def communicate(self, prompt):
            assert prompt is None
            return output, stderr

    async def spawn(*command, **options):
        assert command[0] == "muse"
        if captured is not None:
            captured["command"] = command
            captured["options"] = options
            captured["prompt"] = Path(command[command.index("--prompt-file") + 1]).read_text()
        return Process()

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", spawn)


@pytest.mark.parametrize("effort", [None, "high"])
def test_muse_exec_uses_snapshot_model_and_validates_json_before_submission(tmp_path, monkeypatch, effort):
    calls = []
    captured = {}
    _fake_muse(monkeypatch, _result_stream(_proposal()), captured=captured)

    async def request(_settings, method, path, payload=None):
        calls.append((method, path, payload))
        return {}

    monkeypatch.setattr(worker, "internal_request", request)
    asyncio.run(worker.evaluate_run(_settings(tmp_path), _run(effort=effort)))

    assert calls == [
        (
            "POST",
            "/runs/17/submit",
            {key: _proposal()[key] for key in ("positions", "note", "report")},
        )
    ]
    command = captured["command"]
    assert command[:3] == ("muse", "exec", "--json")
    assert command[command.index("--provider") + 1] == "meta"
    assert command[command.index("--model") + 1] == "muse-spark-1.3"
    assert command[command.index("--approval-mode") + 1] == "never"
    assert {"--disable-write", "--disable-shell", "--no-session-log", "--no-foreign-personal-context"} <= set(
        command
    )
    assert captured["options"]["start_new_session"] is True
    assert captured["options"]["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["options"]["env"]["MASSIVE_API_KEY"] == "massive-secret"
    assert "internal-secret" not in " ".join(command)
    assert captured["prompt"].startswith(_run().execution_prompt)
    assert '"required"' in captured["prompt"]
    if effort is None:
        assert "--reasoning-effort" not in command
    else:
        assert command[command.index("--reasoning-effort") + 1] == effort


@pytest.mark.parametrize(
    "result,error",
    [
        ({"status": "proposal", "blocked_reason": None, "positions": []}, "ValidationError"),
        (
            {
                "status": "blocked",
                "blocked_reason": "portfolio_unavailable",
                "positions": [],
                "note": "",
                "report": "",
                "error": "MCP unavailable.",
            },
            "EvaluationBlocked: portfolio_unavailable: MCP unavailable.",
        ),
        ("not a proposal object", "ValidationError"),
    ],
)
def test_invalid_or_blocked_muse_result_records_failure_without_submission(
    tmp_path, monkeypatch, result, error
):
    calls = []
    _fake_muse(monkeypatch, _result_stream(result))

    async def request(_settings, method, path, payload=None):
        calls.append((method, path, payload))
        return {}

    monkeypatch.setattr(worker, "internal_request", request)
    asyncio.run(worker.evaluate_run(_settings(tmp_path), _run()))

    assert len(calls) == 1
    assert calls[0][:2] == ("POST", "/runs/17/fail")
    assert calls[0][2]["cancelled"] is False
    assert error in calls[0][2]["error"]


def test_muse_startup_failure_preserves_diagnostic_stderr(tmp_path, monkeypatch):
    _fake_muse(
        monkeypatch,
        b"",
        exit_code=2,
        stderr=b"Muse cannot load MCP configuration: invalid streamable HTTP URL",
    )

    with pytest.raises(RuntimeError, match="invalid streamable HTTP URL"):
        asyncio.run(worker.run_muse(_settings(tmp_path), _run()))


def test_muse_scheduler_claims_only_muse_work_with_its_own_authentication(tmp_path, monkeypatch):
    monkeypatch.delenv("META_API_KEY", raising=False)
    settings = _settings(tmp_path)
    auth_path = settings.muse_config_home / "muse" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps({"providers": {"meta": {"mechanism": "oauth", "access_token": "login"}}}))
    claims = []
    imports = []
    state = worker.WorkerState()

    async def version(_settings):
        return "muse-test-version"

    def unexpected_codex(*_args):
        pytest.fail("Muse must not use Codex configuration or authentication")

    monkeypatch.setattr(worker, "muse_version", version)
    monkeypatch.setattr(worker, "write_codex_config", unexpected_codex)
    monkeypatch.setattr(worker, "codex_version", unexpected_codex)
    monkeypatch.setattr(worker, "codex_is_authenticated", unexpected_codex)

    async def catalog(_settings):
        return {"data": [{"id": "muse-spark-test"}]}

    monkeypatch.setattr(worker, "fetch_muse_catalog", catalog)

    async def exercise():
        claimed = asyncio.Event()

        async def request(_settings, method, path, payload=None):
            if path == "/models/import-muse":
                assert method == "POST"
                imports.append(payload)
                return {"models_added": 1}
            assert (method, path) == ("POST", "/claim")
            assert len(imports) == 1
            claims.append(payload)
            claimed.set()
            return {"settings": {"enabled": True, "poll_seconds": 60}, "runs": []}

        monkeypatch.setattr(worker, "internal_request", request)
        task = asyncio.create_task(worker.scheduler(settings, "muse-worker", state, "muse"))
        try:
            await asyncio.wait_for(claimed.wait(), timeout=1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())

    assert state.authenticated is True
    assert state.status == "idle"
    assert len(claims) == 1
    assert claims[0]["worker_id"] == "muse-worker"
    assert claims[0]["harness"] == "muse"
    assert claims[0]["harness_version"] == "muse-test-version"


def _process_running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", maxsplit=1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("ending", ["timeout", "admin", "shutdown", "completed"])
def test_evaluation_cleans_mcp_children_after_every_exit(tmp_path, monkeypatch, ending):
    async def exercise():
        child_path = tmp_path / "child.pid"
        child_code = (
            "import os,signal,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            f"open({str(child_path)!r},'w').write(str(os.getpid()));"
            "time.sleep(30)"
        )
        parent_code = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-c',{child_code!r}],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "time.sleep(30)"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            parent_code,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        polling = asyncio.Event()

        async def cancellation(_settings, _run):
            polling.set()
            if ending == "admin":
                return "Cancelled by an administrator."
            await asyncio.Event().wait()

        monkeypatch.setattr(worker, "_wait_for_cancellation", cancellation)
        try:
            async with asyncio.timeout(3):
                while not child_path.exists() or not child_path.read_text():
                    await asyncio.sleep(0.01)
            child_pid = int(child_path.read_text())
            assert _process_running(child_pid)
            if ending == "completed":
                # The CLI can exit while an MCP descendant retains no worker pipes.
                process.terminate()
                await process.wait()
            task = asyncio.create_task(
                worker._communicate_run(
                    _settings(tmp_path),
                    _run(timeout_seconds=0 if ending == "timeout" else 30),
                    process,
                    None,
                )
            )
            if ending == "shutdown":
                await asyncio.wait_for(polling.wait(), timeout=3)
                task.cancel()
            if ending == "completed":
                assert await task == (b"", b"")
            else:
                error = {
                    "timeout": RuntimeError,
                    "admin": worker.RunCancelled,
                    "shutdown": asyncio.CancelledError,
                }[ending]
                with pytest.raises(error):
                    await task
            async with asyncio.timeout(1):
                while _process_running(child_pid):
                    await asyncio.sleep(0.01)
            assert process.returncode is not None
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    asyncio.run(exercise())
