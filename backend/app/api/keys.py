"""API-key management (browser admin session only). Keys authenticate the MCP server, so
key management itself is deliberately *not* exposed as an MCP tool — it can only
be done here, from the admin panel."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import ApiKey
from ..schemas import ApiKeyCreate
from ..security import API_KEY_DISPLAY_LEN, generate_api_key, hash_api_key, require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


def _key_out(api_key: ApiKey) -> dict:
    return {
        "id": api_key.id,
        "name": api_key.name,
        "prefix": api_key.prefix,
        "created_at": api_key.created_at.isoformat(),
        "last_used_at": api_key.last_used_at.isoformat() if api_key.last_used_at else None,
        "revoked": api_key.revoked_at is not None,
    }


@router.get("/keys")
def list_keys(session: Session = Depends(get_session)):
    keys = session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return {"keys": [_key_out(key) for key in keys]}


@router.post("/keys", status_code=201)
def create_key(body: ApiKeyCreate, session: Session = Depends(get_session)):
    raw = generate_api_key()
    api_key = ApiKey(name=body.name, key_hash=hash_api_key(raw), prefix=raw[:API_KEY_DISPLAY_LEN])
    session.add(api_key)
    session.commit()
    # The raw key is returned exactly once — it is never recoverable afterwards.
    return {**_key_out(api_key), "key": raw}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, session: Session = Depends(get_session)):
    api_key = session.get(ApiKey, key_id)
    if api_key is not None and api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        session.commit()
    return {"ok": True}
