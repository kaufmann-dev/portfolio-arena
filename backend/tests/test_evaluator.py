"""Focused tests for the integrated Codex worker."""

import asyncio
import json
from pathlib import Path

from app.evaluator.config import EvaluatorRuntimeSettings
from app.evaluator.worker import (
    ClaimedRun,
    WorkerState,
    codex_environment,
    evaluate_run,
    evaluator_prompt,
    run_codex,
    scheduler,
    worker_state_payload,
    write_codex_config,
)


def _settings(tmp_path: Path) -> EvaluatorRuntimeSettings:
    return EvaluatorRuntimeSettings(
        internal_api_url="http://127.0.0.1:8000/api/internal/evaluator",
        internal_mcp_url="http://127.0.0.1:8000/mcp",
        internal_token="internal-secret",
        massive_api_key="massive-secret",
        codex_home=tmp_path,
    )


def _run(reasoning_effort: str | None = "xhigh") -> ClaimedRun:
    return ClaimedRun.model_validate(
        {
            "id": 17,
            "portfolio": {"id": 2, "slug": "test-portfolio", "name": "Test Portfolio"},
            "trigger_kind": "manual",
            "harness": "codex",
            "execution_model_id": "gpt-5.6-sol",
            "reasoning_effort": reasoning_effort,
            "timeout_seconds": 300,
            "deadline_at": None,
        }
    )


def test_generated_codex_config_is_read_only_and_internal(tmp_path):
    write_codex_config(_settings(tmp_path))

    config = (tmp_path / "config.toml").read_text()

    assert 'default_permissions = ":read-only"' in config
    assert 'url = "http://127.0.0.1:8000/mcp"' in config
    assert config.count('default_tools_approval_mode = "approve"') == 2
    assert 'command = "/bin/bash"' in config
    assert 'args = ["-lc", "exec mcp_massive"]' in config
    assert 'env_vars = ["MASSIVE_API_KEY"]' in config
    assert "submit_evaluation" not in config
    assert "internal-secret" not in config
    assert "massive-secret" not in config


def test_codex_environment_removes_platform_api_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "remove-me")
    monkeypatch.setenv("CODEX_API_KEY", "remove-me-too")

    environment = codex_environment(_settings(tmp_path))

    assert environment["ARENA_INTERNAL_MCP_API_KEY"] == "internal-secret"
    assert environment["MASSIVE_API_KEY"] == "massive-secret"
    assert "OPENAI_API_KEY" not in environment
    assert "CODEX_API_KEY" not in environment


def test_run_codex_applies_snapshot_reasoning_without_service_tier(tmp_path, monkeypatch):
    captured: list[str] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, _prompt):
            output_path = Path(captured[captured.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps(
                    {
                        "status": "proposal",
                        "positions": [{"symbol": "AAPL", "weight_pct": 100, "note": "test"}],
                        "note": "test",
                        "report": "test",
                        "error": "",
                    }
                )
            )
            return b"", b""

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def fake_subprocess(*command, **_kwargs):
        captured.extend(command)
        return FakeProcess()

    monkeypatch.setattr("app.evaluator.worker.asyncio.create_subprocess_exec", fake_subprocess)
    proposal = asyncio.run(run_codex(_settings(tmp_path), _run()))

    assert proposal.positions[0].symbol == "AAPL"
    assert 'model_reasoning_effort="xhigh"' in captured
    assert not any("service_tier" in item for item in captured)
    assert not any("fast_mode" in item for item in captured)


def test_run_codex_omits_reasoning_when_model_has_none(tmp_path, monkeypatch):
    captured: list[str] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, _prompt):
            output_path = Path(captured[captured.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps(
                    {
                        "status": "proposal",
                        "positions": [{"symbol": "AAPL", "weight_pct": 100, "note": "test"}],
                        "note": "test",
                        "report": "test",
                        "error": "",
                    }
                )
            )
            return b"", b""

    async def fake_subprocess(*command, **_kwargs):
        captured.extend(command)
        return FakeProcess()

    monkeypatch.setattr("app.evaluator.worker.asyncio.create_subprocess_exec", fake_subprocess)
    asyncio.run(run_codex(_settings(tmp_path), _run(None)))

    assert not any("model_reasoning_effort" in item for item in captured)


def test_evaluator_prompt_is_lifecycle_neutral():
    prompt = evaluator_prompt(_run())

    assert prompt.count("If the returned allocation history is empty") == 1
    assert "construct the portfolio's initial allocation" in prompt
    assert "produce its next allocation according to the returned strategy" in prompt
    assert "do not rebuild it from scratch" not in prompt
    assert "continuity is useful" not in prompt
    assert "after transaction costs" not in prompt
    assert "Prefer retaining the existing allocation" not in prompt


def test_blocked_codex_result_fails_without_submission(tmp_path, monkeypatch):
    calls: list[tuple[str, str, dict | None]] = []

    async def fake_run_codex(_settings, _claimed_run):
        from app.evaluator.worker import Proposal

        return Proposal(
            status="blocked",
            positions=[],
            note="",
            report="",
            error="get_portfolio was unavailable",
        )

    async def fake_internal_request(_settings, method, path, payload=None):
        calls.append((method, path, payload))
        return {}

    monkeypatch.setattr("app.evaluator.worker.run_codex", fake_run_codex)
    monkeypatch.setattr("app.evaluator.worker.internal_request", fake_internal_request)

    asyncio.run(evaluate_run(_settings(tmp_path), _run()))

    assert calls == [
        (
            "POST",
            "/runs/17/fail",
            {
                "error": "EvaluationBlocked: get_portfolio was unavailable",
                "cancelled": False,
            },
        )
    ]


def test_scheduler_refills_completed_slots_while_other_runs_continue(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    state = WorkerState()
    releases = {run_id: asyncio.Event() for run_id in range(1, 8)}
    started: list[int] = []
    replacements_started = asyncio.Event()
    claim_count = 0

    def run_payload(run_id: int) -> dict:
        return {
            "id": run_id,
            "portfolio": {
                "id": run_id,
                "slug": f"portfolio-{run_id}",
                "name": f"Portfolio {run_id}",
            },
            "trigger_kind": "manual",
            "harness": "codex",
            "execution_model_id": "gpt-5.6-sol",
            "reasoning_effort": None,
            "timeout_seconds": 300,
            "deadline_at": None,
        }

    async def fake_codex_version():
        return "codex-cli test"

    async def fake_codex_is_authenticated():
        return True

    async def fake_internal_request(_settings, method, path, payload=None):
        nonlocal claim_count
        assert (method, path) == ("POST", "/claim")
        claim_count += 1
        if claim_count == 1:
            runs = [run_payload(run_id) for run_id in range(1, 6)]
        elif claim_count == 2:
            runs = [run_payload(6), run_payload(7)]
        else:
            runs = []
        return {
            "settings": {"enabled": True, "poll_seconds": 10},
            "runs": runs,
        }

    async def fake_evaluate_run(_settings, run):
        started.append(run.id)
        if {6, 7}.issubset(started):
            replacements_started.set()
        await releases[run.id].wait()

    monkeypatch.setattr("app.evaluator.worker.write_codex_config", lambda _settings: None)
    monkeypatch.setattr("app.evaluator.worker.codex_version", fake_codex_version)
    monkeypatch.setattr("app.evaluator.worker.codex_is_authenticated", fake_codex_is_authenticated)
    monkeypatch.setattr("app.evaluator.worker.internal_request", fake_internal_request)
    monkeypatch.setattr("app.evaluator.worker.evaluate_run", fake_evaluate_run)

    async def scenario():
        scheduler_task = asyncio.create_task(scheduler(settings, "worker-1", state))
        try:
            while len(started) < 5:
                await asyncio.sleep(0)
            releases[1].set()
            releases[2].set()
            await asyncio.wait_for(replacements_started.wait(), timeout=1)

            assert started == [1, 2, 3, 4, 5, 6, 7]
            assert not any(releases[run_id].is_set() for run_id in (3, 4, 5))
            assert state.active_run_count == 5
            assert claim_count == 2
        finally:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)

    asyncio.run(scenario())


def test_worker_state_payload_is_stable():
    state = WorkerState(
        status="idle",
        harness_version="codex-cli 0.144.5",
        authenticated=True,
        active_run_count=0,
        last_error=None,
        poll_seconds=30,
    )

    assert worker_state_payload(state) == {
        "status": "idle",
        "harness_version": "codex-cli 0.144.5",
        "authenticated": True,
        "active_run_count": 0,
        "last_error": None,
        "poll_seconds": 30,
    }
