"""FastAPI application factory: API routers plus SPA static serving."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .api import api_router
from .config import get_settings
from .db import dispose_engine, session_factory
from .log import setup_logging
from .migrate import run_migrations
from .ratelimit import limiter
from .seed import run_seed

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    with session_factory()() as session:
        run_seed(session)
    yield
    dispose_engine()


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(lifespan=lifespan)
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        logger.warning(
            "rate limit exceeded: ip=%s endpoint=%s", get_remote_address(request), request.url.path
        )
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Try again later."},
            headers={"Retry-After": "60"},
        )

    app.include_router(api_router)

    static_dir: Path = get_settings().static_dir.resolve()

    @app.get("/{path:path}")
    def serve_static(path: str):
        if not path or path == "/":
            return FileResponse(static_dir / "index.html")

        try:
            file_path = (static_dir / path).resolve(strict=False)
            file_path.relative_to(static_dir)
        except ValueError:
            raise HTTPException(404, "Not found") from None

        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
