"""Opaque browser sessions plus API-key authentication for the MCP server."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .config import (
    APP_SESSION_COOKIE,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_IDLE_SECONDS,
    get_settings,
)
from .db import get_session
from .models import ApiKey, AuthSession
from .schemas import CurrentUser

API_KEY_PREFIX = "arena_"
API_KEY_DISPLAY_LEN = 12  # leading chars stored as `prefix` for identifying a key in the UI
BROWSER_ACTIVITY_PATH = "/api/auth/activity"
BROWSER_ACTIVITY_HEADER = "X-Portfolio-Arena-Activity"
BROWSER_ACTIVITY_VALUE = "1"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _delete_expired_sessions(session: Session, now: datetime) -> None:
    idle_cutoff = now - timedelta(seconds=SESSION_IDLE_SECONDS)
    session.execute(
        delete(AuthSession).where(
            or_(
                AuthSession.absolute_expires_at <= now,
                AuthSession.last_seen_at <= idle_cutoff,
            )
        )
    )


def create_auth_session(
    session: Session,
    *,
    subject: str,
    display_name: str,
    id_token: str,
    replace_token: str | None = None,
    now: datetime | None = None,
) -> str:
    """Create an admin session and return its one-time raw cookie value."""
    if not subject or not display_name or not id_token:
        raise ValueError("OIDC sessions require a subject, display name, and ID token")

    current_time = now or datetime.now(UTC)
    _delete_expired_sessions(session, current_time)
    if replace_token:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(replace_token)))

    raw_token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=_token_hash(raw_token),
            subject=subject,
            display_name=display_name,
            id_token=id_token,
            created_at=current_time,
            last_seen_at=current_time,
            absolute_expires_at=current_time + timedelta(seconds=SESSION_ABSOLUTE_SECONDS),
        )
    )
    session.commit()
    return raw_token


def set_session_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        APP_SESSION_COOKIE,
        raw_token,
        max_age=SESSION_ABSOLUTE_SECONDS,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        APP_SESSION_COOKIE,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite="lax",
        path="/",
    )


def load_auth_session(session: Session, raw_token: str | None) -> AuthSession | None:
    if not raw_token:
        return None
    return session.get(AuthSession, _token_hash(raw_token))


def pop_auth_session(session: Session, raw_token: str | None) -> AuthSession | None:
    auth_session = load_auth_session(session, raw_token)
    if auth_session:
        session.delete(auth_session)
        session.commit()
    return auth_session


def require_same_origin(request: Request) -> None:
    if request.headers.get("origin") != get_settings().public_url:
        raise HTTPException(403, "Invalid request origin")


def require_internal_worker(request: Request) -> None:
    configured = get_settings().internal_mcp_api_key
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if (
        configured is None
        or scheme.lower() != "bearer"
        or not token
        or not secrets.compare_digest(token, configured.get_secret_value())
    ):
        raise HTTPException(401, "Invalid internal worker token")


def _load_active_auth_session(
    request: Request,
    session: Session,
    now: datetime,
) -> AuthSession:
    auth_session = load_auth_session(session, request.cookies.get(APP_SESSION_COOKIE))
    if not auth_session:
        raise HTTPException(401, "Not authenticated")

    idle_expires_at = auth_session.last_seen_at + timedelta(seconds=SESSION_IDLE_SECONDS)
    if now >= auth_session.absolute_expires_at or now >= idle_expires_at:
        session.delete(auth_session)
        session.commit()
        raise HTTPException(401, "Session expired")

    return auth_session


def require_admin(
    request: Request,
    session: Session = Depends(get_session),
) -> CurrentUser:
    auth_session = _load_active_auth_session(request, session, datetime.now(UTC))
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        require_same_origin(request)

    return CurrentUser(display_name=auth_session.display_name)


def record_browser_activity(
    request: Request,
    session: Session = Depends(get_session),
) -> CurrentUser:
    raw_path = request.scope.get("raw_path")
    if (
        request.method != "POST"
        or request.url.path != BROWSER_ACTIVITY_PATH
        or (raw_path is not None and raw_path != BROWSER_ACTIVITY_PATH.encode("ascii"))
        or request.headers.get(BROWSER_ACTIVITY_HEADER) != BROWSER_ACTIVITY_VALUE
    ):
        raise HTTPException(403, "Invalid activity request")

    require_same_origin(request)
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site != "same-origin":
        raise HTTPException(403, "Invalid fetch metadata")

    now = datetime.now(UTC)
    auth_session = _load_active_auth_session(request, session, now)
    auth_session.last_seen_at = now
    session.commit()
    return CurrentUser(display_name=auth_session.display_name)


# --- API keys (MCP server) ---------------------------------------------------


def generate_api_key() -> str:
    """A high-entropy opaque token. Shown to the operator once, never stored raw."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    """SHA-256 hex. The token already has 256 bits of entropy, so a fast digest
    (not a password hash) is correct and lets us look keys up by an indexed unique column."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_api_key(session: Session, raw: str) -> ApiKey | None:
    """Return the matching, non-revoked key, or None. Does not stamp last_used_at."""
    if not raw:
        return None
    api_key = session.scalars(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw))).first()
    if api_key is None or api_key.revoked_at is not None:
        return None
    return api_key
