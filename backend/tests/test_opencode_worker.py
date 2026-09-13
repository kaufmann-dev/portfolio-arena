"""OpenCode CLI contracts, provider preflight, and worker lifecycle."""

import asyncio
import json
from pathlib import Path

import pytest

from app.evaluator import worker
from app.evaluator.opencode import (
    opencode_environment,
    opencode_models,
    opencode_result,
    prepare_opencode,
    validate_opencode_model,
)

from .test_muse_worker import _jsonl, _proposal, _settings
from .test_muse_worker import _run as muse_run


def _run(**kwargs):
    return muse_run(**kwargs).model_copy(
        update={
            "harness": "opencode",
            "execution_model_id": "test/model/version",
        }
    )


def _event(kind, message="answer", session="root", **part):
    # OpenCode 1.18.9/1.18.30 CLI emits completed parts in this envelope.
    return {
        "type": kind,
        "timestamp": 1,
        "sessionID": session,
        "part": {
            "id": f"{message}-{kind}",
            "type": kind.replace("_", "-"),
            "sessionID": session,
            "messageID": message,
            **part,
        },
    }


def _text(value, **kwargs):
    return _event("text", text=value, time={"start": 1, "end": 2}, **kwargs)


def _stream(proposal):
    return _jsonl(
        _event("step_start"),
        _text(json.dumps(proposal)),
        _event("step_finish", reason="stop"),
    )


def _models():
    return {"test/model/version": {"capabilities": {"toolcall": True}, "variants": {"high": {}}}}


def test_environment_preserves_provider_credentials_and_native_configuration(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    prepare_opencode(settings)
    config_path = settings.opencode_home / "config" / "opencode" / "opencode.json"
    config_path.parent.mkdir()
    config_path.write_text('{"provider":{"test":{"options":{"baseURL":"https://provider.test"}}}}')
    auth = settings.opencode_home / "data" / "opencode" / "auth.json"
    auth.parent.mkdir()
    auth.write_text('{"test":{"type":"oauth","refresh":"saved-login"}}')
    original = (config_path.read_text(), auth.read_text())
    monkeypatch.setenv("OPENAI_API_KEY", "openai-provider-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-provider-key")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"share":"auto"}')
    monkeypatch.setenv("OPENCODE_PERMISSION", '"allow"')
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", "/foreign/config")
    monkeypatch.setenv("OPENCODE_DB", "/foreign/sessions.db")
    environment = opencode_environment(settings)
    prepare_opencode(settings)
    assert (config_path.read_text(), auth.read_text()) == original
    for kind in ("config", "data", "cache", "state"):
        assert environment[f"XDG_{kind.upper()}_HOME"] == str(settings.opencode_home / kind)
    assert environment["OPENAI_API_KEY"] == "openai-provider-key"
    assert environment["ANTHROPIC_API_KEY"] == "anthropic-provider-key"
    assert "OPENCODE_DB" not in environment
    assert "OPENCODE_CONFIG_DIR" not in environment
    assert environment["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
    assert environment["OPENCODE_DISABLE_CLAUDE_CODE"] == "true"
    assert environment["OPENCODE_DISABLE_EXTERNAL_SKILLS"] == "true"
    config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    assert config["share"] == "disabled"
    permissions = config["agent"]["arena-evaluator"]["permission"]
    assert permissions["*"] == "deny"
    assert not {"bash", "edit", "task", "question", "read"} & permissions.keys()
    assert permissions["massive_*"] == permissions["webfetch"] == permissions["websearch"] == "allow"
    assert "portfolio_arena_*" not in permissions
    arena = config["mcp"]["portfolio_arena"]
    assert arena["url"] == settings.internal_mcp_url
    assert arena["oauth"] is False
    assert arena["headers"]["Authorization"] == "Bearer {env:ARENA_INTERNAL_MCP_API_KEY}"
    assert config["mcp"]["massive"]["command"] == ["/bin/bash", "-lc", "exec mcp_massive"]
    assert "internal-secret" not in environment["OPENCODE_CONFIG_CONTENT"]


def test_native_model_listing_preserves_exact_ids_and_variant_names():
    first = {"name": "A model with {braces}", "capabilities": {"toolcall": True}, "variants": {"careful": {}}}
    second = {"capabilities": {"toolcall": True}, "variants": {}}
    output = (
        f"test/model/version\n{json.dumps(first, indent=2)}\nlocal/other\n{json.dumps(second, indent=2)}\n"
    )
    models = opencode_models(output.encode())
    assert models == {"test/model/version": first, "local/other": second}
    validate_opencode_model(models, "test/model/version", "careful")
    validate_opencode_model(models, "local/other", None)
    assert opencode_models(b"\n") == {}


@pytest.mark.parametrize(
    "output", [b"test/model\n{", b"test/model\n[]", b"unexpected output", b"test/model\n{}\ntest/model\n{}"]
)
def test_invalid_native_model_listing_is_rejected(output):
    with pytest.raises(ValueError):
        opencode_models(output)


@pytest.mark.parametrize(
    "models,variant,error",
    [
        ({}, None, "provider configuration and login"),
        (_models(), "unknown", "variant unknown is unavailable"),
        ({"test/model/version": {"capabilities": {"toolcall": False}}}, None, "research tools"),
        (
            {
                "test/model/version": {
                    "capabilities": {"toolcall": True},
                    "variants": {"high": {"disabled": True}},
                }
            },
            "high",
            "variant high is unavailable",
        ),
    ],
)
def test_preflight_rejects_unavailable_model_tools_and_variants(models, variant, error):
    with pytest.raises(ValueError, match=error):
        validate_opencode_model(models, "test/model/version", variant)


def test_result_uses_last_root_assistant_message_and_deduplicates_text_parts():
    proposal = json.dumps(_proposal())
    output = _jsonl(
        _event("step_start", message="research"),
        _text("I will research this.", message="research"),
        _event("tool_use", message="research", state={"output": "tool output"}),
        _event("step_finish", message="research", reason="tool-calls"),
        _event("step_start"),
        _text("child response", session="child"),
        _event("step_finish", session="child", reason="stop"),
        _event("reasoning", text="private reasoning"),
        _text(proposal[:20], id="part-one"),
        _text(proposal[:20], id="part-one"),
        _text(proposal[20:], id="part-two"),
        _event("step_finish", reason="stop"),
    )
    assert opencode_result(output) == proposal


@pytest.mark.parametrize(
    "records",
    [
        [],
        [_event("step_start"), _text("{}")],
        [_event("step_start"), _text("{}"), _event("step_finish", reason="length")],
        [_event("step_start"), _text("{}"), _event("step_finish", reason="tool-calls")],
        [_event("step_start"), _event("step_finish", reason="stop")],
        [_event("step_start"), _text("{}"), _event("step_finish", message="other", reason="stop")],
        [_event("step_start"), _event("text", text="{}"), _event("step_finish", reason="stop")],
        [
            _event("step_start"),
            _text("{}"),
            _event("step_finish", reason="stop"),
            _event("step_start", message="unfinished"),
        ],
        [{"type": "text", "part": {"text": "{}"}}],
    ],
)
def test_incomplete_or_unsuccessful_output_is_rejected(records):
    with pytest.raises(ValueError):
        opencode_result(_jsonl(*records))


def test_error_event_is_rejected_even_after_valid_output():
    with pytest.raises(ValueError, match="Provider authentication expired"):
        opencode_result(
            _stream(_proposal())
            + _jsonl(
                {
                    "type": "error",
                    "sessionID": "root",
                    "error": {"name": "APIError", "data": {"message": "Provider authentication expired"}},
                }
            )
        )
    with pytest.raises(ValueError):
        opencode_result(_stream(_proposal()) + b"not json\n")


def _fake_cli(monkeypatch, result, *, exit_code=0, stderr=b""):
    captured = []

    class Process:
        pid = 99999999
        returncode = exit_code

        async def communicate(self, prompt=None):
            captured[-1]["prompt"] = prompt
            return result, stderr

    async def launch(*command, **options):
        captured.append({"command": command, "options": options})
        return Process()

    async def available(_settings, run=None):
        return _models()

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(worker, "opencode_available_models", available)
    return captured


@pytest.mark.parametrize("variant", [None, "high"])
@pytest.mark.parametrize("abstain", [False, True])
def test_execution_uses_snapshot_and_submits_validated_result(tmp_path, monkeypatch, variant, abstain):
    proposal = _proposal()
    if abstain:
        proposal.update(status="abstained", positions=[])
    captured = _fake_cli(monkeypatch, _stream(proposal))
    submissions = []

    async def request(_settings, method, path, payload=None):
        submissions.append((method, path, payload))
        return {}

    monkeypatch.setattr(worker, "internal_request", request)
    asyncio.run(worker.evaluate_run(_settings(tmp_path), _run(effort=variant)))
    assert submissions == [
        ("POST", "/runs/17/submit", {key: proposal[key] for key in ("positions", "note", "report")})
    ]
    call = captured[0]
    command = call["command"]
    assert command[:4] == ("opencode", "run", "--format", "json")
    assert command[command.index("--model") + 1] == "test/model/version"
    assert command[command.index("--agent") + 1] == "arena-evaluator"
    assert call["options"]["start_new_session"] is True
    assert call["options"]["stdin"] == asyncio.subprocess.PIPE
    assert not Path(call["options"]["cwd"]).exists()
    assert _run().execution_prompt.encode() in call["prompt"]
    assert b'"required"' in call["prompt"]
    assert not {"--session", "--continue", "--share", "--auto"} & set(command)
    if variant is None:
        assert "--variant" not in command
    else:
        assert command[command.index("--variant") + 1] == variant


@pytest.mark.parametrize(
    "result,error",
    [
        ({"status": "proposal", "positions": []}, "ValidationError"),
        (
            {
                "status": "blocked",
                "blocked_reason": "research_unavailable",
                "positions": [],
                "note": "",
                "report": "Research failed.",
                "error": "Provider unavailable.",
            },
            "EvaluationBlocked",
        ),
        ("not an object", "ValidationError"),
    ],
)
def test_invalid_and_blocked_results_fail_without_submission(tmp_path, monkeypatch, result, error):
    _fake_cli(monkeypatch, _stream(result))
    failures = []

    async def request(_settings, method, path, payload=None):
        failures.append((path, payload))
        return {}

    monkeypatch.setattr(worker, "internal_request", request)
    asyncio.run(worker.evaluate_run(_settings(tmp_path), _run()))
    assert len(failures) == 1
    assert failures[0][0] == "/runs/17/fail"
    assert error in failures[0][1]["error"]
    if error == "EvaluationBlocked":
        assert failures[0][1]["report"] == "Research failed."


def test_nonzero_exit_rejects_even_valid_output_and_retains_diagnostic(tmp_path, monkeypatch):
    _fake_cli(monkeypatch, _stream(_proposal()), exit_code=1, stderr=b"Provider authentication failed")
    with pytest.raises(RuntimeError, match="Provider authentication failed"):
        asyncio.run(worker.run_opencode(_settings(tmp_path), _run()))


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_preflight_obeys_attempt_deadline_and_cancellation(tmp_path, monkeypatch, ending):
    async def preflight(_settings, run=None):
        if ending == "cancel":
            raise worker.RunCancelled("Cancelled by an administrator.")
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "opencode_available_models", preflight)
    error = worker.RunCancelled if ending == "cancel" else RuntimeError
    with pytest.raises(error, match="Cancelled|exceeded"):
        asyncio.run(
            worker.run_opencode(_settings(tmp_path), _run(timeout_seconds=0 if ending == "timeout" else 30))
        )


@pytest.mark.parametrize("available", [False, True])
def test_scheduler_claims_only_opencode_without_importing_models(tmp_path, monkeypatch, available):
    state = worker.WorkerState()
    settings = _settings(tmp_path)
    requests = []

    async def version(_settings):
        return "opencode-test-version"

    async def models(_settings):
        return _models() if available else {}

    def unexpected(*_args):
        pytest.fail("OpenCode must not call another harness")

    monkeypatch.setattr(worker, "opencode_version", version)
    monkeypatch.setattr(worker, "opencode_available_models", models)
    for name in (
        "write_codex_config",
        "write_muse_config",
        "codex_version",
        "muse_version",
        "fetch_muse_catalog",
    ):
        monkeypatch.setattr(worker, name, unexpected)

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
        task = asyncio.create_task(worker.scheduler(settings, "opencode-worker", state, "opencode"))
        try:
            await asyncio.wait_for(checked.wait(), timeout=1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
    assert state.authenticated is available
    assert state.status == ("idle" if available else "authentication_required")
    if available:
        assert requests == [
            {
                "worker_id": "opencode-worker",
                "harness": "opencode",
                "harness_version": "opencode-test-version",
                "limit": 20,
            }
        ]
    else:
        assert requests == []
        assert "opencode auth login" in state.last_error
