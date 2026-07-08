"""Authentication endpoints (single admin)."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import User
from ..ratelimit import limiter
from ..schemas import CurrentUser, LoginRequest, PasswordChangeRequest
from ..security import create_access_token, hash_password, require_admin, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, session: Session = Depends(get_session)):
    user = session.scalars(select(User).where(User.email == body.email)).first()

    if not user or not verify_password(body.password, user.password_hash):
        logger.warning("failed login: email=%s ip=%s", body.email, get_remote_address(request))
        raise HTTPException(401, "Invalid email or password")

    token = create_access_token(user.email)
    return {"token": token, "email": user.email}


@router.get("/me")
def me(current_user: CurrentUser = Depends(require_admin)):
    return {"email": current_user.email}


@router.put("/password")
def change_password(
    body: PasswordChangeRequest,
    current_user: CurrentUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    user = session.scalars(select(User).where(User.email == current_user.email)).first()
    if not user or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    session.execute(
        update(User)
        .where(User.email == current_user.email)
        .values(password_hash=hash_password(body.new_password))
    )
    session.commit()
    return {"ok": True}
