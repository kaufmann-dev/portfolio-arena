from fastapi import APIRouter

from . import admin, auth, keys, public

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(public.router)
api_router.include_router(admin.router)
api_router.include_router(keys.router)
