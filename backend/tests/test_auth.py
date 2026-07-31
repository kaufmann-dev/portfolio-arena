"""OIDC, opaque-session, and browser-admin authorization boundaries."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from authlib.integrations.base_client import OAuthError
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select

from .conftest import PUBLIC_URL

ISSUER_URL = "https://idp.test/application/o/portfolio-arena"
DISCOVERY_URL = f"{ISSUER_URL}/.well-known/openid-configuration"
ACTIVITY_PATH = "/api/auth/activity"
ACTIVITY_HEADER = "X-Portfolio-Arena-Activity"


def _settings_values(**overrides):
    values = {
        "DATABASE_URL": "postgresql://user:pass@localhost/db",
        "ARENA_PUBLIC_URL": "https://arena.test",
        "ARENA_OIDC_ISSUER_URL": ISSUER_URL,
        "ARENA_OIDC_CLIENT_ID": "portfolio-arena-test",
        "ARENA_OIDC_CLIENT_SECRET": "test-client-secret",
        "ARENA_OIDC_STATE_SECRET": "test-state-secret-0123456789abcdef0123456789abcdef",
        "MASSIVE_API_KEY": "test-massive-api-key",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("field", "insecure_url"),
    [
        ("ARENA_PUBLIC_URL", "http://arena.example.com"),
        ("ARENA_PUBLIC_URL", "http://localhost.example.com"),
        ("ARENA_OIDC_ISSUER_URL", "http://identity.example.com/application/o/arena"),
        ("ARENA_OIDC_ISSUER_URL", "http://127.0.0.1.example.com/application/o/arena"),
    ],
)
def test_oidc_urls_require_https_outside_exact_loopback(field, insecure_url):
    from app.config import Settings

    with pytest.raises(ValidationError, match="must use HTTPS outside loopback development"):
        Settings(**_settings_values(**{field: insecure_url}))


def test_oidc_urls_allow_http_on_exact_loopback_hosts():
    from app.config import Settings

    settings = Settings(
        **_settings_values(
            ARENA_PUBLIC_URL="http://localhost:8000",
            ARENA_OIDC_ISSUER_URL="http://127.0.0.1:9000/application/o/arena",
        )
    )
    assert settings.public_url == "http://localhost:8000"
    assert settings.oidc_issuer_url == "http://127.0.0.1:9000/application/o/arena"

    ipv6_settings = Settings(
        **_settings_values(
            ARENA_PUBLIC_URL="http://[::1]:8000",
            ARENA_OIDC_ISSUER_URL="http://localhost:9000/application/o/arena",
        )
    )
    assert ipv6_settings.public_url == "http://[::1]:8000"


@pytest.mark.parametrize(
    ("field", "invalid_url"),
    [
        ("ARENA_PUBLIC_URL", "https://@arena.test"),
        ("ARENA_PUBLIC_URL", "https://:@arena.test"),
        ("ARENA_PUBLIC_URL", "https://arena.test\\alias"),
        ("ARENA_OIDC_ISSUER_URL", "https://@idp.test/application/o/arena"),
        ("ARENA_OIDC_ISSUER_URL", "https://:@idp.test/application/o/arena"),
        ("ARENA_OIDC_ISSUER_URL", "https://idp.test/application\\o/arena"),
    ],
)
def test_oidc_urls_reject_empty_userinfo_and_backslashes(field, invalid_url):
    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(**_settings_values(**{field: invalid_url}))


def _metadata() -> dict[str, object]:
    return {
        "issuer": ISSUER_URL,
        "authorization_endpoint": f"{ISSUER_URL}/authorize",
        "token_endpoint": f"{ISSUER_URL}/token",
        "jwks_uri": f"{ISSUER_URL}/jwks",
        "end_session_endpoint": f"{ISSUER_URL}/logout",
        "code_challenge_methods_supported": ["S256"],
    }


def _session_hash(headers: dict[str, str]) -> str:
    raw_token = headers["Cookie"].partition("=")[2]
    return sha256(raw_token.encode()).hexdigest()


def _set_last_seen(headers: dict[str, str], value: datetime) -> None:
    from app.db import session_factory
    from app.models import AuthSession

    with session_factory()() as session:
        session.get(AuthSession, _session_hash(headers)).last_seen_at = value
        session.commit()


def _last_seen(headers: dict[str, str]) -> datetime:
    from app.db import session_factory
    from app.models import AuthSession

    with session_factory()() as session:
        return session.get(AuthSession, _session_hash(headers)).last_seen_at


class CallbackOidcClient:
    def __init__(self, token: dict | None = None, error: OAuthError | None = None):
        self.token = token
        self.error = error

    async def authorize_access_token(self, _request):
        if self.error:
            raise self.error
        return self.token


class LogoutOidcClient:
    def __init__(self):
        self.logout_kwargs = None
        self.validated = False

    async def logout_redirect(self, _request, **kwargs):
        self.logout_kwargs = kwargs
        return RedirectResponse(f"{ISSUER_URL}/logout")

    async def validate_logout_response(self, _request):
        self.validated = True
        return {"post_logout_redirect_uri": f"{PUBLIC_URL}/api/auth/logged-out"}


class FailingLogoutOidcClient:
    async def logout_redirect(self, _request, **_kwargs):
        raise RuntimeError("provider metadata has no logout endpoint")


class TestPublicReads:
    def test_arenas_open(self, client):
        assert client.get("/api/arena/managed?direction=long").status_code == 200
        assert client.get("/api/arena/rebuilt?direction=long").status_code == 200

    def test_prompts_open(self, client):
        assert client.get("/api/prompts").status_code == 200

    def test_agents_open(self, client):
        assert client.get("/api/agents").status_code == 200


class TestWriteBoundaries:
    def test_writes_require_browser_session(self, client):
        assert client.post("/api/agents", json={"name": "X"}).status_code == 401
        assert (
            client.post(
                "/api/admin/prompts",
                json={
                    "name": "X",
                    "mode": "managed",
                    "managed_text": "y",
                    "allocation_policy": {
                        "min_position_weight_pct": 1,
                        "max_position_weight_pct": 100,
                    },
                },
            ).status_code
            == 401
        )
        assert client.put("/api/settings", json={"default_cost_bps": 5}).status_code == 401
        assert client.delete("/api/prices/cache").status_code == 401

    def test_bearer_token_does_not_authenticate_browser_routes(self, client):
        response = client.post(
            "/api/agents",
            json={"name": "X"},
            headers={"Authorization": "Bearer nope"},
        )
        assert response.status_code == 401

    def test_unsafe_request_requires_exact_origin(self, client, admin_headers):
        cookie = {"Cookie": admin_headers["Cookie"]}
        assert client.post("/api/agents", json={"name": "X"}, headers=cookie).status_code == 403
        assert (
            client.post(
                "/api/agents",
                json={"name": "X"},
                headers={**cookie, "Origin": "https://attacker.test"},
            ).status_code
            == 403
        )


def test_oidc_login_uses_authorization_code_pkce_and_nonce(client):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(DISCOVERY_URL).mock(return_value=httpx.Response(200, json=_metadata()))
        response = client.get("/api/auth/login", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["portfolio-arena-test"]
    assert query["redirect_uri"] == [f"{PUBLIC_URL}/api/auth/callback"]
    assert set(query["scope"][0].split()) == {"openid", "email", "profile"}
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    assert query["state"][0]
    assert query["nonce"][0]
    assert "code_verifier" not in query

    flow_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith("portfolio_arena_oidc_flow=")
    ).lower()
    assert "httponly" in flow_cookie
    assert "samesite=lax" in flow_cookie
    assert "max-age=600" in flow_cookie


def test_oidc_client_is_confidential_and_never_requests_refresh_tokens():
    from app.oidc import OIDC_SCOPES, get_oidc_client

    oidc = get_oidc_client()
    assert oidc.client_secret == "test-client-secret"
    assert oidc.client_kwargs["token_endpoint_auth_method"] == "client_secret_post"
    assert oidc.client_kwargs["code_challenge_method"] == "S256"
    assert OIDC_SCOPES == "openid email profile"
    assert "offline_access" not in OIDC_SCOPES


def test_callback_creates_admin_session_for_any_provider_admitted_identity(client, monkeypatch):
    fake = CallbackOidcClient(
        {
            "access_token": "discarded-access-token",
            "refresh_token": "discarded-refresh-token",
            "id_token": "retained-id-token",
            "userinfo": {"sub": "new-admin-subject", "email": "second-admin@test.local"},
        }
    )
    monkeypatch.setattr("app.api.auth.get_oidc_client", lambda: fake)

    response = client.get("/api/auth/callback?code=code&state=state", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"{PUBLIC_URL}/"
    app_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith("portfolio_arena_session=")
    ).lower()
    assert "httponly" in app_cookie
    assert "samesite=lax" in app_cookie
    assert "max-age=604800" in app_cookie
    assert client.get("/api/auth/me").json() == {"displayName": "second-admin@test.local"}

    from app.db import session_factory
    from app.models import AuthSession

    with session_factory()() as session:
        rows = list(session.scalars(select(AuthSession)))
    assert len(rows) == 1
    assert rows[0].subject == "new-admin-subject"
    assert rows[0].id_token == "retained-id-token"
    assert not hasattr(rows[0], "access_token")
    assert not hasattr(rows[0], "refresh_token")


def test_callback_rejects_invalid_identity(client, monkeypatch):
    fake = CallbackOidcClient({"id_token": "token", "userinfo": {}})
    monkeypatch.setattr("app.api.auth.get_oidc_client", lambda: fake)
    response = client.get("/api/auth/callback?code=code&state=state")
    assert response.status_code == 400
    assert response.json() == {"detail": "OIDC provider returned an invalid identity"}


def test_callback_reports_protocol_error(client, monkeypatch):
    fake = CallbackOidcClient(error=OAuthError(error="access_denied"))
    monkeypatch.setattr("app.api.auth.get_oidc_client", lambda: fake)
    response = client.get("/api/auth/callback?error=access_denied&state=state")
    assert response.status_code == 400
    assert response.json() == {"detail": "OIDC authentication failed"}


def test_me_uses_cookie_and_ignores_legacy_bearer_tokens(client, admin_headers):
    response = client.get("/api/auth/me", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"displayName": "admin@test.local"}
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_normal_authenticated_api_requests_do_not_slide_idle_timeout(client, admin_headers):
    earlier = datetime.now(UTC) - timedelta(hours=1)
    _set_last_seen(admin_headers, earlier)

    assert client.get("/api/auth/me", headers=admin_headers).status_code == 200
    assert client.get("/api/keys", headers=admin_headers).status_code == 200
    assert client.post("/api/keys", headers=admin_headers, json={"name": "passive-check"}).status_code == 201
    assert _last_seen(admin_headers) == earlier


@pytest.mark.parametrize("fetch_site", [None, "same-origin"])
def test_valid_browser_activity_slides_idle_timeout(client, admin_headers, fetch_site):
    earlier = datetime.now(UTC) - timedelta(hours=1)
    _set_last_seen(admin_headers, earlier)
    headers = {**admin_headers, ACTIVITY_HEADER: "1"}
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site

    response = client.post(ACTIVITY_PATH, headers=headers)

    assert response.status_code == 204
    assert response.content == b""
    assert _last_seen(admin_headers) > earlier


@pytest.mark.parametrize(
    ("method", "path", "request_headers", "expected_status"),
    [
        ("POST", ACTIVITY_PATH, {"Origin": PUBLIC_URL}, 403),
        ("POST", ACTIVITY_PATH, {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "0"}, 403),
        ("POST", ACTIVITY_PATH, {ACTIVITY_HEADER: "1"}, 403),
        ("POST", ACTIVITY_PATH, {"Origin": f"{PUBLIC_URL}/", ACTIVITY_HEADER: "1"}, 403),
        (
            "POST",
            ACTIVITY_PATH,
            {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "1", "Sec-Fetch-Site": "same-site"},
            403,
        ),
        (
            "POST",
            ACTIVITY_PATH,
            {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "1", "Sec-Fetch-Site": "cross-site"},
            403,
        ),
        (
            "POST",
            ACTIVITY_PATH,
            {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "1", "Sec-Fetch-Site": "none"},
            403,
        ),
        ("PUT", ACTIVITY_PATH, {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "1"}, 405),
        ("POST", f"{ACTIVITY_PATH}/", {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "1"}, 405),
        ("POST", "/api/auth/%61ctivity", {"Origin": PUBLIC_URL, ACTIVITY_HEADER: "1"}, 403),
    ],
)
def test_invalid_browser_activity_does_not_slide_idle_timeout(
    client,
    admin_headers,
    method,
    path,
    request_headers,
    expected_status,
):
    earlier = datetime.now(UTC) - timedelta(hours=1)
    _set_last_seen(admin_headers, earlier)
    headers = {"Cookie": admin_headers["Cookie"], **request_headers}

    response = client.request(method, path, headers=headers, follow_redirects=False)

    assert response.status_code == expected_status
    assert _last_seen(admin_headers) == earlier


def test_well_formed_activity_requires_browser_session(client):
    response = client.post(
        ACTIVITY_PATH,
        headers={
            "Origin": PUBLIC_URL,
            ACTIVITY_HEADER: "1",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status_code == 401


def test_idle_timeout_requires_login_and_deletes_id_token(client, admin_headers):
    from app.config import SESSION_IDLE_SECONDS
    from app.db import session_factory
    from app.models import AuthSession

    token_hash = _session_hash(admin_headers)
    with session_factory()() as session:
        auth_session = session.get(AuthSession, token_hash)
        auth_session.last_seen_at = datetime.now(UTC) - timedelta(seconds=SESSION_IDLE_SECONDS + 1)
        session.commit()

    assert client.get("/api/keys", headers=admin_headers).status_code == 401
    with session_factory()() as session:
        assert session.get(AuthSession, token_hash) is None


def test_absolute_timeout_requires_login(client, admin_headers):
    from app.db import session_factory
    from app.models import AuthSession

    token_hash = _session_hash(admin_headers)
    with session_factory()() as session:
        auth_session = session.get(AuthSession, token_hash)
        auth_session.absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert client.get("/api/keys", headers=admin_headers).status_code == 401
    with session_factory()() as session:
        assert session.get(AuthSession, token_hash) is None


def test_logout_deletes_local_session_and_uses_rp_logout(client, admin_headers, monkeypatch):
    from app.db import session_factory
    from app.models import AuthSession

    fake = LogoutOidcClient()
    monkeypatch.setattr("app.api.auth.get_oidc_client", lambda: fake)
    token_hash = _session_hash(admin_headers)

    response = client.post("/api/auth/logout", headers=admin_headers, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"{ISSUER_URL}/logout"
    assert fake.logout_kwargs == {
        "post_logout_redirect_uri": f"{PUBLIC_URL}/api/auth/logged-out",
        "id_token_hint": "test-id-token",
        "client_id": "portfolio-arena-test",
    }
    assert "max-age=0" in response.headers["set-cookie"].lower()
    with session_factory()() as session:
        assert session.get(AuthSession, token_hash) is None


def test_logout_stays_local_when_provider_logout_fails(client, admin_headers, monkeypatch):
    from app.db import session_factory
    from app.models import AuthSession

    monkeypatch.setattr("app.api.auth.get_oidc_client", lambda: FailingLogoutOidcClient())
    token_hash = _session_hash(admin_headers)

    response = client.post("/api/auth/logout", headers=admin_headers)

    assert response.status_code == 502
    assert "max-age=0" in response.headers["set-cookie"].lower()
    with session_factory()() as session:
        assert session.get(AuthSession, token_hash) is None


def test_logout_callback_validates_state_and_returns_home(client, monkeypatch):
    fake = LogoutOidcClient()
    monkeypatch.setattr("app.api.auth.get_oidc_client", lambda: fake)
    response = client.get("/api/auth/logged-out?state=logout-state", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{PUBLIC_URL}/"
    assert fake.validated is True


def test_local_password_auth_routes_are_removed(client, admin_headers):
    assert (
        client.post(
            "/api/auth/login",
            headers={"Origin": PUBLIC_URL},
            json={"email": "admin@test.local", "password": "password"},
        ).status_code
        == 405
    )
    assert client.put("/api/auth/password", headers=admin_headers, json={}).status_code == 405
