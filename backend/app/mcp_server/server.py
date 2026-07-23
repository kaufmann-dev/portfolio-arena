"""The FastMCP instance, its API-key gate, and the mountable ASGI app.

Named ``mcp_server`` (not ``mcp``) so it never shadows the installed ``mcp``
package. Every MCP request must present a valid API key — there is no anonymous
access — checked by a thin ASGI wrapper around the streamable-HTTP app.
"""

import json
import secrets
from datetime import UTC, datetime

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings
from ..db import session_factory
from ..security import resolve_api_key

INTERNAL_READ_TOOLS = frozenset(
    {
        "get_portfolio",
        "get_effective_date",
        "validate_symbol",
        "search_symbols",
    }
)

INSTRUCTIONS = """\
Portfolio Arena tracks AI-managed stock portfolios against SPY/RSP benchmarks.
NAVs are recomputed from locked allocations plus cached prices — nothing is
snapshotted. Typical rebalance workflow for one portfolio:

1. `get_portfolio(slug_or_id)` — read its prompt, drifted holdings (entry vs
   current price), full allocation history with the general note and per-position
   notes, and performance metrics.
2. Follow the returned prompt allocation policy. Weights must sum to exactly
   100 across USD-denominated equities and ETFs. Validate unfamiliar tickers
   with `validate_symbol` / `search_symbols`.
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


def _is_internal_key(raw: str) -> bool:
    internal_key = get_settings().internal_mcp_api_key
    return internal_key is not None and secrets.compare_digest(
        raw,
        internal_key.get_secret_value(),
    )


def _authenticate_api_key(raw: str) -> bool:
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


async def _buffer_request(receive: Receive) -> tuple[bytes, Receive]:
    messages = []
    body = bytearray()
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break

    index = 0

    async def replay() -> dict:
        nonlocal index
        if index < len(messages):
            message = messages[index]
            index += 1
            return message
        return await receive()

    return bytes(body), replay


def _internal_calls_are_read_only(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return True
    messages = payload if isinstance(payload, list) else [payload]
    for message in messages:
        if not isinstance(message, dict) or message.get("method") != "tools/call":
            continue
        params = message.get("params")
        if not isinstance(params, dict) or params.get("name") not in INTERNAL_READ_TOOLS:
            return False
    return True


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
        is_internal = bool(raw and _is_internal_key(raw))
        if not raw or (not is_internal and not await anyio.to_thread.run_sync(_authenticate_api_key, raw)):
            response = JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
            await response(scope, receive, send)
            return
        if is_internal and scope["method"] == "POST":
            body, receive = await _buffer_request(receive)
            if not _internal_calls_are_read_only(body):
                response = JSONResponse(
                    {"detail": "The internal evaluator token is read-only"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_mcp_asgi_app() -> ASGIApp:
    from . import tools  # noqa: F401 — importing registers the @mcp.tool functions

    return ApiKeyAuth(mcp.streamable_http_app())
