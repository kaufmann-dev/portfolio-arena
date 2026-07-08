"""The FastMCP instance, its API-key gate, and the mountable ASGI app.

Named ``mcp_server`` (not ``mcp``) so it never shadows the installed ``mcp``
package. Every MCP request must present a valid API key — there is no anonymous
access — checked by a thin ASGI wrapper around the streamable-HTTP app.
"""

from datetime import UTC, datetime

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..db import session_factory
from ..security import resolve_api_key

INSTRUCTIONS = """\
Portfolio Arena tracks AI-managed stock portfolios against SPY/RSP benchmarks.
NAVs are recomputed from locked allocations plus cached prices — nothing is
snapshotted. Typical rebalance workflow for one portfolio:

1. `get_portfolio(slug_or_id)` — read its prompt, drifted holdings (entry vs
   current price), full allocation history with the general note and per-position
   notes, and performance metrics.
2. Decide new target weights. Weights must sum to exactly 100; use `CASH:USD`
   (or `CASH:EUR`, …) for cash. Validate unfamiliar tickers with
   `validate_symbol` / `search_symbols`.
3. `create_allocation(portfolio_id, positions, note)` — the per-position `note`
   and the general `note` are the handoff to the next rebalance. Entry time is
   server-set and the allocation freezes after its effective market close.

`get_arena_overview()` compares every portfolio's performance at once.
"""

mcp = FastMCP(
    "portfolio-arena",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    # The server runs behind a reverse proxy on an arbitrary domain and every
    # request is already API-key gated, so the SDK's DNS-rebinding Host/Origin
    # check is redundant and would otherwise reject the proxied Host header.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _extract_key(headers: list[tuple[bytes, bytes]]) -> str | None:
    header_map = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in headers}
    scheme, _, token = header_map.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return header_map.get("x-api-key", "").strip() or None


def _authenticate(raw: str) -> bool:
    """Verify the key and stamp last_used_at. Runs in a worker thread (psycopg2
    is blocking), so it opens and closes its own session."""
    session = session_factory()()
    try:
        api_key = resolve_api_key(session, raw)
        if api_key is None:
            return False
        api_key.last_used_at = datetime.now(UTC)
        session.commit()
        return True
    finally:
        session.close()


class ApiKeyAuth:
    """ASGI middleware: reject any MCP request without a valid API key."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Mounting at "/mcp" strips the prefix, so a request to "/mcp" (no trailing
        # slash) reaches the inner app as an empty path and misses its "/" route.
        # Normalize it so clients can use either "/mcp" or "/mcp/".
        if scope["path"] == "":
            scope = {**scope, "path": "/"}
        raw = _extract_key(scope.get("headers", []))
        if not raw or not await anyio.to_thread.run_sync(_authenticate, raw):
            response = JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_mcp_asgi_app() -> ASGIApp:
    from . import tools  # noqa: F401 — importing registers the @mcp.tool functions

    return ApiKeyAuth(mcp.streamable_http_app())
