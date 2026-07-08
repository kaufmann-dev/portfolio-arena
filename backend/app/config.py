"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

JWT_ALGORITHM = "HS256"
JWT_TTL_SECONDS = 86400

PRICE_FETCH_MAX_WORKERS = 16
PRICE_FETCH_TIMEOUT_SECONDS = 8
PRICE_FETCH_TOTAL_TIMEOUT_SECONDS = 10
PRICE_FAILURE_COOLDOWN_SECONDS = 300
YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"

# Benchmark portfolios are valued by the identical engine, at zero cost.
BENCHMARKS = [
    {"slug": "spy-buy-and-hold", "name": "SPY Buy & Hold", "symbol": "SPY"},
    {"slug": "rsp-buy-and-hold", "name": "RSP Buy & Hold", "symbol": "RSP"},
]
BENCHMARK_AGENT_SLUG = "benchmark"
BENCHMARK_PROMPT_SLUG = "buy-and-hold"

TOO_EARLY_AGE_DAYS = 183  # portfolios younger than ~6 months are "too early to judge"


class Settings(BaseSettings):
    database_url: str = Field(validation_alias="DATABASE_URL")
    jwt_secret: str = Field(validation_alias="ARENA_JWT_SECRET")
    admin_email: str = Field(validation_alias="ARENA_ADMIN_EMAIL")
    admin_password: str = Field(validation_alias="ARENA_ADMIN_PASSWORD")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
