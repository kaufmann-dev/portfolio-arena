"""Static frontend cache policy."""

from app.static_files import static_cache_headers


def test_spa_shell_always_revalidates():
    assert static_cache_headers("", spa=True) == {"Cache-Control": "no-cache, max-age=0, must-revalidate"}
    assert static_cache_headers("p/example", spa=True) == {
        "Cache-Control": "no-cache, max-age=0, must-revalidate"
    }


def test_hashed_assets_are_immutable_but_other_files_are_not():
    assert static_cache_headers("assets/index-abc123.css") == {
        "Cache-Control": "public, max-age=31536000, immutable"
    }
    assert static_cache_headers("favicon.svg") is None
