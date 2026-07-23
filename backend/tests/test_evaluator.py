"""Focused tests for the integrated Codex worker."""

import asyncio
import json
from pathlib import Path

from app.evaluator.config import EvaluatorRuntimeSettings
from app.evaluator.worker import (
    ClaimedRun,
    WorkerState,
    codex_environment,
    run_codex,
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


def _run(service_tier: str = "fast") -> ClaimedRun:
    return ClaimedRun.model_validate(
        {
            "id": 17,
            "portfolio": {"id": 2, "slug": "test-portfolio", "name": "Test Portfolio"},
            "trigger_kind": "manual",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "service_tier": service_tier,
            "timeout_seconds": 300,
            "deadline_at": None,
        }
    )


def test_generated_codex_config_is_read_only_and_internal(tmp_path):
    write_codex_config(_settings(tmp_path))

    config = (tmp_path / "config.toml").read_text()

    assert 'default_permissions = ":read-only"' in config
    assert 'url = "http://127.0.0.1:8000/mcp"' in config
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


def test_run_codex_applies_snapshot_reasoning_and_fast_tier(tmp_path, monkeypatch):
    captured: list[str] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self, _prompt):
            output_path = Path(captured[captured.index("--output-last-message") + 1])
            output_path.write_text(
                json.dumps(
                    {
                        "positions": [{"symbol": "AAPL", "weight_pct": 100, "note": "test"}],
                        "note": "test",
                        "report": "test",
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
    assert 'service_tier="fast"' in captured
    assert "features.fast_mode=true" in captured


def test_worker_state_payload_is_stable():
    state = WorkerState(
        status="idle",
        codex_version="codex-cli 0.144.5",
        authenticated=True,
        active_run_count=0,
        last_error=None,
        poll_seconds=30,
    )

    assert worker_state_payload(state) == {
        "status": "idle",
        "codex_version": "codex-cli 0.144.5",
        "authenticated": True,
        "active_run_count": 0,
        "last_error": None,
        "poll_seconds": 30,
    }
