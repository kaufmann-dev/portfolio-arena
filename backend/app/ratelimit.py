"""Shared slowapi rate limiter (in-memory; single-instance deployment)."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
