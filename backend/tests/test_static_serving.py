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


def test_unknown_api_routes_do_not_serve_the_spa(client):
    response = client.get("/api/retired-endpoint")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


def test_client_routes_serve_the_spa(client):
    for path in ("/", "/p/example", "/prompt/example/", "/agent/example", "/admin", "/about"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<!doctype html>" in response.text.lower()


def test_unknown_paths_render_the_404_page(client):
    response = client.get("/some/unknown/route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Page not found</h1>" in response.text


def test_error_page_copy_matches_status():
    from app.error_pages import render_error_page

    assert "<h1>Access denied</h1>" in render_error_page(403)
    assert "<h1>Something went wrong</h1>" in render_error_page(500)
    assert "<h1>Something went wrong</h1>" in render_error_page(503)
    assert "<h1>Request could not be completed</h1>" in render_error_page(400)
