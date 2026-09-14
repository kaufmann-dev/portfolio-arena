"""Antigravity CLI contracts and evaluator lifecycle boundaries."""

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from app.evaluator import worker
from app.evaluator.agy import (
    AgyAuthenticationRequired,
    agy_environment,
    agy_models,
    agy_proposal_schema,
    agy_result,
    write_agy_config,
)

from .test_muse_worker import _proposal, _settings
from .test_muse_worker import _run as muse_run


def _run(**kwargs):
    return muse_run(**kwargs).model_copy(
        update={"harness": "agy", "execution_model_id": "gemini-3.8-flash-low"}
    )


def _output(proposal):
    return json.dumps(
        {
            "event": "result",
            "result": {"status": "SUCCESS", "response": "Ignore this prose", "structured_output": proposal},
        }
    ).encode()


def test_configuration_preserves_login_and_restricts_tools(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    auth = settings.agy_home / "antigravity-cli" / "antigravity-oauth-token"
    auth.parent.mkdir(parents=True)
    auth.write_text("saved-login")
    write_agy_config(settings)
    write_agy_config(settings)
    assert auth.read_text() == "saved-login"
    config_file = auth.parent / "settings.json"
    permissions = json.loads(config_file.read_text())["permissions"]
    assert {"command(*)", "unsandboxed(*)", "read_file(*)", "write_file(*)"} <= set(permissions["deny"])
    assert "mcp(portfolio_arena/get_portfolio)" in permissions["allow"]
    assert "mcp(portfolio_arena/*)" not in permissions["allow"]
    assert "mcp(massive/*)" in permissions["allow"]
    mcp_file = settings.agy_home / "config" / "mcp_config.json"
    mcp = json.loads(mcp_file.read_text())["mcpServers"]
    assert mcp["portfolio_arena"]["serverUrl"] == settings.internal_mcp_url
    assert mcp["portfolio_arena"]["headers"] == {"Authorization": "Bearer internal-secret"}
    assert mcp["massive"]["env"] == {"MASSIVE_API_KEY": "massive-secret"}
    for path in (config_file, mcp_file):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "META_API_KEY", "ANTIGRAVITY_LS_ADDRESS", "AGY_ADC_AUTH"):
        monkeypatch.setenv(key, "must-not-inherit")
    environment = agy_environment(settings)
    assert "must-not-inherit" not in environment.values()
    assert environment.get("HOME") == os.environ.get("HOME")
    assert environment["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"


def test_native_model_listing():
    assert agy_models(b"gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\nclaude-sonnet-4-6\tClaude\n") == {
        "gemini-3.8-flash-low",
        "claude-sonnet-4-6",
    }
    assert agy_models(b"\n") == set()


@pytest.mark.parametrize(
    "value", [b"not a listing", b"model\tName\nmodel\tOther", b"\tMissing ID", b"model\t"]
)
def test_invalid_model_listing_is_rejected(value):
    with pytest.raises(ValueError):
        agy_models(value)


@pytest.mark.parametrize(
    "value",
    [
        {"status": "SUCCESS", "response": "{}"},
        {"status": "SUCCESS", "structured_output": "{}"},
        {"status": "ERROR", "structured_output": {}},
        {"status": "CANCELLED", "structured_output": {}},
        {"status": "SUCCESS", "error": "failed", "structured_output": {}},
        [],
    ],
)
def test_incomplete_or_failed_result_is_rejected(value):
    with pytest.raises(ValueError):
        agy_result(json.dumps({"event": "result", "result": value}).encode(), b"")


def test_structured_result_ignores_prose_but_rejects_timeout_even_with_success():
    output = _output(_proposal())
    assert agy_result(output, b"") == _proposal()
    with pytest.raises(ValueError, match="timed out"):
        agy_result(output, b"[agy] print timeout after 30s with turn in progress; returning partial output")


def test_generation_schema_adapts_nullable_enum_without_changing_arena_contract():
    original = json.loads(Path(worker.__file__).with_name("proposal.schema.json").read_text())
    adapted = agy_proposal_schema()
    assert adapted["properties"]["blocked_reason"]["type"] == ["string", "null"]
    assert "enum" not in adapted["properties"]["blocked_reason"]
    adapted["properties"]["blocked_reason"] = original["properties"]["blocked_reason"]
    assert adapted == original
    assert None in original["properties"]["blocked_reason"]["enum"]


@pytest.mark.parametrize("reason", ["unknown", "", 7, False])
def test_generation_schema_does_not_weaken_proposal_validation(reason):
    proposal = _proposal()
    proposal.update(status="blocked", positions=[], error="Research failed", blocked_reason=reason)
    with pytest.raises(ValueError):
        worker.Proposal.model_validate(agy_result(_output(proposal), b""))


def _fake_cli(monkeypatch, result, *, code=0, stderr=b""):
    captured = []

    class Process:
        pid = 99999999
        returncode = code

        async def communicate(self, prompt=None):
            captured[-1][1]["prompt"] = prompt
            return result, stderr

    async def launch(*command, **options):
        captured.append((command, options))
        return Process()

    async def available(_settings, run=None):
        return {_run().execution_model_id}

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(worker, "agy_available_models", available)
    return captured


@pytest.mark.parametrize("effort", [None, "high"])
@pytest.mark.parametrize("abstained", [False, True])
def test_execution_submits_only_validated_structured_result(tmp_path, monkeypatch, effort, abstained):
    proposal = _proposal()
    if abstained:
        proposal.update(status="abstained", positions=[])
    calls = _fake_cli(monkeypatch, _output(proposal))
    requests = []

    async def request(_settings, method, path, payload=None):
        requests.append((path, payload))
        return {}

    monkeypatch.setattr(worker, "internal_request", request)
    asyncio.run(worker.evaluate_run(_settings(tmp_path), _run(effort=effort)))
    assert requests == [
        (
            "/runs/17/submit",
            {"attempt_count": 1, **{key: proposal[key] for key in ("positions", "note", "report")}},
        )
    ]
    command, options = calls[0]
    assert command[:2] == ("agy", f"--gemini_dir={_settings(tmp_path).agy_home}")
    assert "--print" not in command
    assert command[command.index("--input-format") + 1] == "stream-json"
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert json.loads(options["prompt"]) == {"event": "user", "message": {"content": _run().execution_prompt}}
    assert command[command.index("--model") + 1] == _run().execution_model_id
    assert "--json-schema" in command
    assert json.loads(command[command.index("--json-schema") + 1]) == agy_proposal_schema()
    assert "--disable-slash-commands" in command
    assert "--dangerously-skip-permissions" not in command
    assert options["start_new_session"] is True
    assert options["stdin"] == asyncio.subprocess.PIPE
    assert not Path(options["cwd"]).exists()
    if effort is None:
        assert "--effort" not in command
    else:
        assert command[command.index("--effort") + 1] == effort


@pytest.mark.parametrize("kind", ["timeout", "invalid", "blocked", "nonzero"])
def test_failed_execution_never_submits(tmp_path, monkeypatch, kind):
    proposal = _proposal()
    if kind == "invalid":
        proposal["positions"] = []
    elif kind == "blocked":
        proposal.update(
            status="blocked", positions=[], error="No research", blocked_reason="research_unavailable"
        )
    _fake_cli(
        monkeypatch,
        _output(proposal),
        code=1 if kind == "nonzero" else 0,
        stderr=b"[agy] print timeout" if kind == "timeout" else b"",
    )
    requests = []

    async def request(_settings, method, path, payload=None):
        requests.append((path, payload))
        return {}

    monkeypatch.setattr(worker, "internal_request", request)
    asyncio.run(worker.evaluate_run(_settings(tmp_path), _run()))
    assert len(requests) == 1
    assert requests[0][0] == "/runs/17/fail"
    if kind == "blocked":
        assert requests[0][1]["report"] == proposal["report"]


@pytest.mark.parametrize("ending", ["cancel", "timeout", "unavailable"])
def test_preflight_enforces_model_deadline_and_cancellation(tmp_path, monkeypatch, ending):
    async def available(_settings, run=None):
        if ending == "cancel":
            raise worker.RunCancelled("Cancelled by an administrator.")
        if ending == "unavailable":
            return set()
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "agy_available_models", available)
    expected = {"cancel": worker.RunCancelled, "timeout": RuntimeError, "unavailable": ValueError}[ending]
    with pytest.raises(expected, match="Cancelled|exceeded|unavailable"):
        asyncio.run(
            worker.run_agy(_settings(tmp_path), _run(timeout_seconds=0 if ending == "timeout" else 30))
        )


@pytest.mark.parametrize(
    "diagnostic",
    [
        b"Error: authentication required. Run 'agy' to log in.",
        b"Error: not logged in.",
        b"Fetching available models...\nError: Please sign in to view available models. "
        b"Launch the CLI without arguments to sign in.",
    ],
)
def test_native_authentication_error_has_login_hint(tmp_path, monkeypatch, diagnostic):
    settings = _settings(tmp_path / "directory with spaces")
    _fake_cli(monkeypatch, b"", code=1, stderr=diagnostic)
    with pytest.raises(AgyAuthenticationRequired) as error:
        asyncio.run(worker._agy_probe(settings, "models"))
    assert f"agy --gemini_dir='{settings.agy_home}'" in str(error.value)


@pytest.mark.parametrize("available", [True, False])
def test_scheduler_claims_only_authenticated_agy_work(tmp_path, monkeypatch, available):
    state = worker.WorkerState()
    requests = []

    async def version(_settings):
        return "1.2.2"

    async def models(_settings):
        if not available:
            raise AgyAuthenticationRequired("Run agy --gemini_dir=/var/lib/agy")
        return {_run().execution_model_id}

    monkeypatch.setattr(worker, "agy_version", version)
    monkeypatch.setattr(worker, "agy_available_models", models)

    async def exercise():
        checked = asyncio.Event()

        async def request(_settings, method, path, payload=None):
            assert (method, path) == ("POST", "/claim")
            requests.append(payload)
            return {"settings": {"enabled": True, "poll_seconds": 60}, "runs": []}

        async def pause(_seconds):
            checked.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(worker, "internal_request", request)
        monkeypatch.setattr(worker.asyncio, "sleep", pause)
        task = asyncio.create_task(worker.scheduler(_settings(tmp_path), "agy-worker", state, "agy"))
        try:
            await asyncio.wait_for(checked.wait(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert state.authenticated is available
    assert state.status == ("idle" if available else "authentication_required")
    assert requests == (
        [{"worker_id": "agy-worker", "harness": "agy", "harness_version": "1.2.2", "limit": 20}]
        if available
        else []
    )


def test_stream_ignores_progress_and_rejects_missing_or_multiple_results():
    progress = b'{"event":"init"}\n{"event":"step_update"}\n'
    result = _output(_proposal())
    assert agy_result(progress + result, b"") == _proposal()
    for output in (progress, result + b"\n" + result):
        with pytest.raises(ValueError, match="exactly one"):
            agy_result(output, b"")


def test_large_prompt_reaches_cli_intact_through_stdin(tmp_path, monkeypatch):
    import sys

    real_launch = asyncio.create_subprocess_exec
    prompt = "research context " * 10_000
    run = _run().model_copy(update={"execution_prompt": prompt})
    output = _output(_proposal())

    async def launch(*command, **options):
        assert prompt not in command
        script = (
            "import json,sys; value=json.load(sys.stdin); "
            f"assert value == {{'event':'user','message':{{'content':{prompt!r}}}}}; "
            f"sys.stdout.buffer.write({output!r})"
        )
        # Use a script file so the test itself does not exceed Linux's argv limit.
        script_path = tmp_path / "fake_agy.py"
        script_path.write_text(script)
        return await real_launch(sys.executable, str(script_path), **options)

    async def models(*args, **kwargs):
        return {run.execution_model_id}

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(worker, "agy_available_models", models)
    assert asyncio.run(worker.run_agy(_settings(tmp_path), run)).model_dump() == _proposal()
