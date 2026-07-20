"""Configured OpenID Connect relying-party client."""

from functools import lru_cache

from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App

from .config import get_settings

OIDC_SCOPES = "openid email profile"


@lru_cache
def get_oidc_client() -> StarletteOAuth2App:
    settings = get_settings()
    oauth = OAuth()
    client = oauth.register(
        name="portfolio_arena",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        server_metadata_url=f"{settings.oidc_issuer_url}/.well-known/openid-configuration",
        client_kwargs={
            "scope": OIDC_SCOPES,
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic",
        },
    )
    assert client is not None
    return client


def callback_url() -> str:
    return f"{get_settings().public_url}/api/auth/callback"


def post_logout_url() -> str:
    return f"{get_settings().public_url}/api/auth/logged-out"
