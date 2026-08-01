"""Cache policy for the built frontend shell and immutable assets."""

SPA_CACHE_HEADERS = {"Cache-Control": "no-cache, max-age=0, must-revalidate"}
IMMUTABLE_ASSET_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def static_cache_headers(path: str, *, spa: bool = False) -> dict[str, str] | None:
    if spa:
        return SPA_CACHE_HEADERS
    return IMMUTABLE_ASSET_CACHE_HEADERS if path.startswith("assets/") else None
