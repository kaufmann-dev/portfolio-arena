"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

APP_SESSION_COOKIE = "portfolio_arena_session"
OIDC_FLOW_COOKIE = "portfolio_arena_oidc_flow"
SESSION_IDLE_SECONDS = 24 * 60 * 60
SESSION_ABSOLUTE_SECONDS = 7 * 24 * 60 * 60

PRICE_FETCH_MAX_WORKERS = 16
PRICE_FETCH_TIMEOUT_SECONDS = 8
PRICE_FETCH_TOTAL_TIMEOUT_SECONDS = 20
PRICE_FAILURE_COOLDOWN_SECONDS = 300
MASSIVE_BASE_URL = "https://api.massive.com"
MASSIVE_DATA_DELAY_MINUTES = 15

# Benchmark portfolios are valued by the identical engine, at zero cost.
BENCHMARKS = [
    {"slug": "spy-buy-and-hold", "name": "SPY Buy & Hold", "symbol": "SPY"},
    {"slug": "rsp-buy-and-hold", "name": "RSP Buy & Hold", "symbol": "RSP"},
]
BENCHMARK_IDENTITY = {
    "slug": "benchmark",
    "name": "Benchmark",
}
BENCHMARK_STRATEGY = {
    "slug": "buy-and-hold",
    "name": "Buy & Hold",
    "text": "Hold the benchmark ETF forever.",
    "min_position_weight_pct": 100,
    "max_position_weight_pct": 100,
}

TOO_EARLY_AGE_DAYS = 183  # portfolios younger than ~6 months are "too early to judge"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _require_https_outside_loopback(value: str, variable_name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS:
        return
    raise ValueError(f"{variable_name} must use HTTPS outside loopback development")


class Settings(BaseSettings):
    database_url: str = Field(validation_alias="DATABASE_URL")
    public_url: str = Field(validation_alias="ARENA_PUBLIC_URL")
    oidc_issuer_url: str = Field(validation_alias="ARENA_OIDC_ISSUER_URL")
    oidc_client_id: str = Field(min_length=1, validation_alias="ARENA_OIDC_CLIENT_ID")
    oidc_client_secret: SecretStr = Field(min_length=1, validation_alias="ARENA_OIDC_CLIENT_SECRET")
    oidc_state_secret: SecretStr = Field(min_length=32, validation_alias="ARENA_OIDC_STATE_SECRET")
    massive_api_key: SecretStr = Field(min_length=1, validation_alias="MASSIVE_API_KEY")
    internal_mcp_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ARENA_INTERNAL_MCP_API_KEY",
    )

    default_cost_bps: int = Field(default=10, validation_alias="ARENA_DEFAULT_COST_BPS")
    db_connect_retries: int = Field(default=30, validation_alias="ARENA_DB_CONNECT_RETRIES")
    db_connect_retry_delay: float = Field(default=2.0, validation_alias="ARENA_DB_CONNECT_RETRY_DELAY")
    price_cache_ttl_seconds: int = Field(default=3600, validation_alias="ARENA_PRICE_CACHE_TTL_SECONDS")
    static_dir: Path = Field(default=REPO_ROOT / "frontend" / "dist", validation_alias="ARENA_STATIC_DIR")
    port: int = Field(default=8000, validation_alias="PORT")

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        # Coolify hands out postgres:// / postgresql:// URLs; SQLAlchemy needs the driver suffix.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg2://" + value[len(prefix) :]
        return value

    @field_validator("public_url")
    @classmethod
    def _normalize_public_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            "\\" in normalized
            or parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ARENA_PUBLIC_URL must be an http(s) origin without a path")
        _require_https_outside_loopback(normalized, "ARENA_PUBLIC_URL")
        return normalized

    @field_validator("oidc_issuer_url")
    @classmethod
    def _normalize_oidc_issuer_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if (
            "\\" in normalized
            or parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ARENA_OIDC_ISSUER_URL must be an http(s) URL")
        _require_https_outside_loopback(normalized, "ARENA_OIDC_ISSUER_URL")
        return normalized

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
