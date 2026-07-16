"""Pure worker configuration and NYSE scheduling checks."""

from datetime import date, time

from app.evaluator.config import load_settings
from app.evaluator.worker import evaluation_window, write_codex_config
from app.services.trading_calendar import NY


def test_early_close_evaluation_window():
    starts_at, cutoff_at = evaluation_window(date(2026, 11, 27))
    assert starts_at.astimezone(NY).time() == time(11, 30)
    assert cutoff_at.astimezone(NY).time() == time(12, 50)


def test_codex_config_uses_environment_backed_arena_auth(tmp_path, monkeypatch):
    allowlist = tmp_path / "evaluator.toml"
    allowlist.write_text('[[portfolios]]\nslug = "example"\nmodel = "gpt-5.6-sol"\n')
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("EVALUATOR_CONFIG", str(allowlist))
    monkeypatch.setenv("ARENA_MCP_URL", "https://arena.example/mcp")
    monkeypatch.setenv("ARENA_MCP_API_KEY", "arena_secret")
    monkeypatch.setenv("MASSIVE_API_KEY", "massive_secret")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    settings = load_settings()
    write_codex_config(settings)
    config = (codex_home / "config.toml").read_text()

    assert 'bearer_token_env_var = "ARENA_MCP_API_KEY"' in config
    assert "arena_secret" not in config
    assert "massive_secret" not in config
    assert (
        'enabled_tools = ["get_portfolio", "get_effective_date", "validate_symbol", "search_symbols"]'
        in config
    )
