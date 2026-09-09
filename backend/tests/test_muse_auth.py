"""OAuth catalog discovery delegates refresh to Muse without starting inference."""

import asyncio
import json
import signal
from pathlib import Path

import pytest

from app.evaluator import muse

from .test_evaluator import _settings


def save_credential(settings, credential):
    path = settings.muse_config_home / "muse" / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "providers": {"meta": credential}}))


class FakeInput:
    def __init__(self):
        self.messages = []
        self.closed = False

    def write(self, message):
        self.messages.append(json.loads(message))

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    pid = 99999999
    returncode = 0

    def __init__(self, response=b'{"jsonrpc":"2.0","id":1,"result":{}}\n'):
        self.stdin = FakeInput()
        self.stdout = self
        self.response = response

    async def readline(self):
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    async def wait(self):
        return self.returncode


def native_catalog():
    return {
        "schema_version": 1,
        "provider_id": "meta",
        "profile_id": "tbh",
        "source": "provider_catalog",
        "rows": [
            {
                "model_id": "muse-spark-test",
                "display_label": "Muse Spark Test",
                "provider_id": "meta",
                "visibility": "visible",
                "description": "Provider description",
                "reasoning_effort_variants": [{"tier": "minimal"}, {"tier": "high"}],
            },
            {
                "model_id": "hidden-model",
                "display_label": "Hidden Model",
                "provider_id": "meta",
                "visibility": "hidden",
            },
        ],
    }


def save_catalog(data_directory, catalog):
    path = Path(data_directory) / "muse" / "model-catalog" / "6d657461__p746268.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog))


@pytest.mark.parametrize("credential_kind", ["env", "api_key", "oauth"])
def test_catalog_discovery_uses_native_login_for_all_credentials(tmp_path, monkeypatch, credential_kind):
    monkeypatch.delenv("META_API_KEY", raising=False)
    settings = _settings(tmp_path)
    if credential_kind == "env":
        monkeypatch.setenv("META_API_KEY", "api-key-secret")
    elif credential_kind == "api_key":
        save_credential(settings, {"api_key": "api-key-secret"})
    else:
        save_credential(settings, {"mechanism": "oauth", "access_token": "oauth-secret"})
    launches = []
    signals = []
    process = FakeProcess()

    async def launch(*args, **kwargs):
        launches.append((args, kwargs))
        save_catalog(kwargs["env"]["XDG_DATA_HOME"], native_catalog())
        return process

    monkeypatch.setattr(muse.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(muse.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    result = asyncio.run(muse.fetch_muse_catalog(settings))

    assert result == {
        "data": [
            {
                "id": "muse-spark-test",
                "metadata": {
                    "muse-code": {
                        "name": "Muse Spark Test",
                        "description": "Provider description",
                        "is_hidden": False,
                        "variants": {"minimal": {}, "high": {}},
                    }
                },
            }
        ],
    }
    assert len(launches) == 1
    args, kwargs = launches[0]
    assert args == ("muse", "serve", "--no-session-log", "--disable-shell", "--disable-write")
    assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["XDG_CONFIG_HOME"] == str(settings.muse_config_home)
    assert kwargs["env"]["XDG_DATA_HOME"] != str(settings.muse_config_home / "data")
    assert not Path(kwargs["env"]["XDG_DATA_HOME"]).exists()
    assert [message["method"] for message in process.stdin.messages] == ["initialize", "initialized"]
    assert process.stdin.closed is True
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]


@pytest.mark.parametrize("cache_source", [None, "bundled_catalog", "config_catalog"])
def test_catalog_discovery_rejects_stale_and_fallback_catalogs(tmp_path, monkeypatch, cache_source):
    monkeypatch.delenv("META_API_KEY", raising=False)
    settings = _settings(tmp_path)
    save_credential(settings, {"mechanism": "oauth", "access_token": "oauth-secret"})
    save_catalog(settings.muse_config_home / "data", native_catalog())
    process = FakeProcess()

    async def launch(*_args, **kwargs):
        if cache_source:
            save_catalog(kwargs["env"]["XDG_DATA_HOME"], native_catalog() | {"source": cache_source})
        return process

    monkeypatch.setattr(muse.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(muse.os, "killpg", lambda *_args: None)

    with pytest.raises(RuntimeError, match="fresh Meta model catalog"):
        asyncio.run(muse.fetch_muse_catalog(settings))


@pytest.mark.parametrize(
    "response,error_type,expected",
    [
        (b'{"id":1,"error":{"message":"private-token-secret"}}\n', RuntimeError, "initialize"),
        (b"private-token-secret", RuntimeError, "invalid"),
        (b"", RuntimeError, "closed"),
        (TimeoutError(), RuntimeError, "timed out"),
        (asyncio.CancelledError(), asyncio.CancelledError, ""),
    ],
)
def test_cli_catalog_discovery_cleans_processes_and_sanitizes_errors(
    tmp_path, monkeypatch, response, error_type, expected
):
    monkeypatch.delenv("META_API_KEY", raising=False)
    settings = _settings(tmp_path)
    save_credential(settings, {"mechanism": "oauth", "access_token": "oauth-secret"})
    process = FakeProcess(response)
    signals = []

    async def launch(*_args, **_kwargs):
        return process

    monkeypatch.setattr(muse.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(muse.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(error_type) as failure:
        asyncio.run(muse.fetch_muse_catalog(settings))
    assert expected in str(failure.value)
    assert "private-token-secret" not in str(failure.value)
    assert "oauth-secret" not in str(failure.value)
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
