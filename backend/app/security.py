"""Password hashing and JWT authentication (single admin, no other roles),
plus API-key generation/verification for the MCP server."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import JWT_ALGORITHM, JWT_TTL_SECONDS, get_settings
from .db import get_session
from .models import ApiKey, User
from .schemas import CurrentUser

API_KEY_PREFIX = "arena_"
API_KEY_DISPLAY_LEN = 12  # leading chars stored as `prefix` for identifying a key in the UI


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


def require_admin(request: Request, session: Session = Depends(get_session)) -> CurrentUser:
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Not authenticated")

    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "Token expired") from None

    subject = payload.get("sub")
    if not subject:
        raise HTTPException(401, "Token expired")

    user = session.scalars(select(User).where(User.email == subject)).first()
    if not user or user.role != "admin":
        raise HTTPException(401, "Token expired")
    return CurrentUser(email=user.email)


# --- API keys (MCP server) ---------------------------------------------------


def generate_api_key() -> str:
    """A high-entropy opaque token. Shown to the operator once, never stored raw."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    """SHA-256 hex. The token already has 256 bits of entropy, so a fast digest
    (not bcrypt) is correct and lets us look keys up by an indexed unique column."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_api_key(session: Session, raw: str) -> ApiKey | None:
    """Return the matching, non-revoked key, or None. Does not stamp last_used_at."""
    if not raw:
        return None
    api_key = session.scalars(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw))).first()
    if api_key is None or api_key.revoked_at is not None:
        return None
    return api_key
