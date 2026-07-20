"""OIDC authentication endpoints for the admin-only browser application."""

import logging
from collections.abc import Mapping

import httpx
from authlib.integrations.base_client import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import APP_SESSION_COOKIE, get_settings
from ..db import get_session
from ..oidc import callback_url, get_oidc_client, post_logout_url
from ..schemas import CurrentUser
from ..security import (
    clear_session_cookie,
    create_auth_session,
    load_auth_session,
    pop_auth_session,
    record_browser_activity,
    require_admin,
    require_same_origin,
    set_session_cookie,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth")


@router.get("/login")
async def login(request: Request):
    return await get_oidc_client().authorize_redirect(request, callback_url())


@router.get("/callback")
async def callback(request: Request, session: Session = Depends(get_session)):
    try:
        token = await get_oidc_client().authorize_access_token(request)
    except OAuthError as exc:
        request.session.clear()
        logger.warning("OIDC callback failed: %s", exc.error)
        raise HTTPException(400, "OIDC authentication failed") from None
    except httpx.HTTPError as exc:
        request.session.clear()
        logger.warning("OIDC callback failed while contacting the provider: %s", type(exc).__name__)
        raise HTTPException(502, "OIDC provider is unavailable") from None

    userinfo = token.get("userinfo")
    id_token = token.get("id_token")
    subject = userinfo.get("sub") if isinstance(userinfo, Mapping) else None
    if not isinstance(subject, str) or not subject or not isinstance(id_token, str) or not id_token:
        request.session.clear()
        raise HTTPException(400, "OIDC provider returned an invalid identity")

    display_name = next(
        (
            value
            for value in (
                userinfo.get("email"),
                userinfo.get("preferred_username"),
                userinfo.get("name"),
                subject,
            )
            if isinstance(value, str) and value
        ),
        subject,
    )
    raw_token = create_auth_session(
        session,
        subject=subject,
        display_name=display_name,
        id_token=id_token,
        replace_token=request.cookies.get(APP_SESSION_COOKIE),
    )
    request.session.clear()
    response = RedirectResponse(f"{get_settings().public_url}/", status_code=303)
    set_session_cookie(response, raw_token)
    return response


@router.get("/me")
def me(current_user: CurrentUser = Depends(require_admin)):
    return {"displayName": current_user.display_name}


@router.post("/activity", status_code=204, response_class=Response)
def activity(_: CurrentUser = Depends(record_browser_activity)) -> Response:
    return Response(status_code=204)


@router.post("/logout")
async def logout(request: Request, session: Session = Depends(get_session)):
    require_same_origin(request)
    raw_token = request.cookies.get(APP_SESSION_COOKIE)
    auth_session = load_auth_session(session, raw_token)
    id_token = auth_session.id_token if auth_session else None
    pop_auth_session(session, raw_token)
    request.session.clear()

    if id_token:
        try:
            response = await get_oidc_client().logout_redirect(
                request,
                post_logout_redirect_uri=post_logout_url(),
                id_token_hint=id_token,
                client_id=get_settings().oidc_client_id,
            )
            response.status_code = 303
        except (OAuthError, httpx.HTTPError, RuntimeError, ValueError):
            logger.warning("OIDC RP-initiated logout failed after the local session was removed")
            response = PlainTextResponse(
                "Signed out locally, but the identity provider logout failed.",
                status_code=502,
            )
    else:
        response = RedirectResponse(f"{get_settings().public_url}/", status_code=303)

    clear_session_cookie(response)
    return response


@router.get("/logged-out")
async def logged_out(request: Request):
    try:
        await get_oidc_client().validate_logout_response(request)
    except OAuthError:
        request.session.clear()
        raise HTTPException(400, "OIDC logout response was invalid") from None
    request.session.clear()
    return RedirectResponse(f"{get_settings().public_url}/", status_code=303)
