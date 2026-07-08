"""API-key-authenticated MCP server exposing the full app surface."""

from .server import build_mcp_asgi_app, mcp

__all__ = ["build_mcp_asgi_app", "mcp"]
