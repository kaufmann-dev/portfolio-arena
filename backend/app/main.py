"""FastAPI application factory: API routers plus SPA static serving."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from .api import api_router
from .config import OIDC_FLOW_COOKIE, get_settings
from .db import dispose_engine, session_factory
from .log import setup_logging
from .mcp_server import build_mcp_asgi_app, mcp
from .migrate import run_migrations
from .ratelimit import limiter
from .seed import run_seed
from .services.market_refresh import run_market_refresh_loop
from .static_files import static_cache_headers

logger = logging.getLogger(__name__)


class McpTrailingSlash:
    """Rewrite exactly ``/mcp`` to ``/mcp/`` before routing. Starlette's
    ``Mount('/mcp')`` only matches ``/mcp/…``, so without this the bare path
    falls through to the SPA catch-all (405/index.html) instead of the MCP app.
    This lets clients use either form with no redirect."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    with session_factory()() as session:
        run_seed(session)
    refresh_stop = asyncio.Event()
    refresh_task = asyncio.create_task(run_market_refresh_loop(refresh_stop))
    # A mounted sub-app's own lifespan never runs, so the MCP session manager
    # must be started here or every /mcp request fails.
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        refresh_stop.set()
        await refresh_task
        dispose_engine()


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.oidc_state_secret.get_secret_value(),
        session_cookie=OIDC_FLOW_COOKIE,
        max_age=600,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.state.limiter = limiter
    app.add_middleware(McpTrailingSlash)

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

    @app.get("/api/health")
    def health():
        with session_factory()() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    # Mount before the SPA catch-all: routes match in registration order, so the
    # catch-all would otherwise swallow GET /mcp and serve index.html.
    app.mount("/mcp", build_mcp_asgi_app())

    static_dir: Path = settings.static_dir.resolve()

    @app.get("/{path:path}")
    def serve_static(path: str):
        if not path or path == "/":
            return FileResponse(static_dir / "index.html", headers=static_cache_headers(path, spa=True))

        try:
            file_path = (static_dir / path).resolve(strict=False)
            file_path.relative_to(static_dir)
        except ValueError:
            raise HTTPException(404, "Not found") from None

        if file_path.is_file():
            return FileResponse(file_path, headers=static_cache_headers(path))
        return FileResponse(static_dir / "index.html", headers=static_cache_headers(path, spa=True))

    return app


app = create_app()
