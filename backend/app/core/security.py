"""Auth: open demo mode or Supabase Auth JWTs (HS256) with analyst roles.

``AUTH_MODE=open``  -> every request is an analyst (local demo).
``AUTH_MODE=jwt``   -> ``Authorization: Bearer <supabase-access-token>`` is
verified (HS256 with SUPABASE_JWT_SECRET); the role is read from the
``app_metadata.role`` claim (``analyst`` or ``admin``).  Read endpoints stay
public so the dashboard can render KPIs before sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def verify_jwt(token: str, secret: str) -> dict | None:
    """Minimal HS256 JWT verification (no PyJWT dependency)."""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if "exp" in payload and float(payload["exp"]) < time.time():
            return None
        return payload
    except Exception:
        return None


class AuthMiddleware(BaseHTTPMiddleware):
    """Protects mutating endpoints in ``jwt`` mode; annotates ``request.state``."""

    async def dispatch(self, request: Request, call_next):
        request.state.user = {"sub": "demo-analyst", "role": "analyst", "mode": "open"}
        if settings.AUTH_MODE != "jwt":
            return await call_next(request)
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/media") or path.startswith("/app"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        payload = verify_jwt(token, settings.SUPABASE_JWT_SECRET) if token and settings.SUPABASE_JWT_SECRET else None
        if payload is None:
            if request.method in WRITE_METHODS:
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            return await call_next(request)
        role = (payload.get("app_metadata") or {}).get("role") or payload.get("role") or "analyst"
        request.state.user = {**payload, "role": role, "mode": "jwt"}
        if request.method in WRITE_METHODS and role not in ("analyst", "admin", "service_role"):
            return JSONResponse({"detail": "insufficient role"}, status_code=403)
        return await call_next(request)


def require_roles(*roles: Iterable[str]):
    """FastAPI dependency factory for role-gated endpoints."""
    from fastapi import HTTPException

    def _dep(request: Request) -> dict:
        user = getattr(request.state, "user", {})
        if user.get("role") not in roles:
            raise HTTPException(403, "insufficient role")
        return user

    return _dep
