"""
Authentication router for Cosmic Conquest.

Provides three endpoints:
  POST /api/auth/request-otp   — generate and email a 6-digit OTP
  POST /api/auth/verify-otp    — verify OTP, issue session token
  GET  /api/auth/validate      — validate a Bearer token, return email

Also exposes BearerTokenMiddleware for use in main.py.
"""

import logging
import smtplib
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from .auth_utils import (
        check_and_increment,
        generate_otp,
        issue_token,
        resolve_token,
        store_otp,
        verify_and_consume_otp,
    )
    from .email_sender import send_otp_email
except ImportError:
    from auth_utils import (
        check_and_increment,
        generate_otp,
        issue_token,
        resolve_token,
        store_otp,
        verify_and_consume_otp,
    )
    from email_sender import send_otp_email

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level Redis client — set by main.py via set_redis() after import
# to avoid circular imports.
# ---------------------------------------------------------------------------

r = None


def set_redis(redis_client) -> None:
    """Called by main.py after import to inject the shared Redis client."""
    global r
    r = redis_client


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class OTPRequest(BaseModel):
    email: EmailStr


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    code: str


class TokenResponse(BaseModel):
    token: str


class ValidateResponse(BaseModel):
    email: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_bearer(auth_header: str) -> Optional[str]:
    """Return the token portion of a 'Bearer <token>' header, or None."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    return parts[1].strip()


# ---------------------------------------------------------------------------
# POST /api/auth/request-otp  (subtask 3.2)
# ---------------------------------------------------------------------------

@router.post("/request-otp")
def request_otp(body: OTPRequest):
    """
    1. Rate-limit check — 3 requests / 60 s per email.
    2. Generate OTP and attempt SMTP delivery.
    3. Store OTP in Redis only after successful delivery.
    """
    email = body.email.lower()  # Normalize to full lowercase

    # 1. Rate limiting
    if not check_and_increment(r, email):
        return JSONResponse(
            status_code=429,
            content={"detail": "TOO MANY REQUESTS. WAIT BEFORE RETRYING."},
        )

    # 2. Generate OTP and send email (store only on success)
    otp = generate_otp()
    try:
        send_otp_email(email, otp)
    except (smtplib.SMTPException, OSError):
        # Full traceback already logged by email_sender; surface generic 503
        logger.exception("OTP email delivery failed for %s", email)
        return JSONResponse(
            status_code=503,
            content={"detail": "EMAIL DELIVERY FAILED. TRY AGAIN LATER."},
        )

    # 3. Store OTP only after confirmed delivery
    store_otp(r, email, otp)
    return {"message": "CODE SENT"}


# ---------------------------------------------------------------------------
# POST /api/auth/verify-otp  (subtask 3.6)
# ---------------------------------------------------------------------------

@router.post("/verify-otp", response_model=TokenResponse)
def verify_otp(body: OTPVerifyRequest):
    """
    1. Look up otp:<email> in Redis.
    2. Missing key  → 401 CODE EXPIRED OR NOT FOUND
    3. Value mismatch → 401 INVALID CODE
    4. Match → delete OTP, issue token, return TokenResponse
    """
    email = body.email.lower()  # Normalize to full lowercase
    result = verify_and_consume_otp(r, email, body.code)

    if result is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "CODE EXPIRED OR NOT FOUND"},
        )
    if result is False:
        return JSONResponse(
            status_code=401,
            content={"detail": "INVALID CODE"},
        )

    # result is True — OTP matched and has been deleted
    token = issue_token(r, email)
    return TokenResponse(token=token)


# ---------------------------------------------------------------------------
# GET /api/auth/validate  (subtask 3.10)
# ---------------------------------------------------------------------------

@router.get("/validate", response_model=ValidateResponse)
def validate_token(request: Request):
    """
    1. Extract Bearer token from Authorization header.
    2. Resolve token → email via Redis.
    3. Return ValidateResponse or 401.
    """
    auth_header = request.headers.get("Authorization", "")
    token = _extract_bearer(auth_header)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"detail": "AUTHENTICATION REQUIRED"},
        )

    email = resolve_token(r, token)
    if not email:
        return JSONResponse(
            status_code=401,
            content={"detail": "AUTHENTICATION REQUIRED"},
        )

    return ValidateResponse(email=email)


# ---------------------------------------------------------------------------
# BearerTokenMiddleware  (subtask 4.1)
# ---------------------------------------------------------------------------

class BearerTokenMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that enforces Bearer token authentication on all
    /api/ routes except /api/auth/*.

    The Redis client is injected via the constructor so that this class does
    not depend on the module-level `r` variable and avoids circular imports
    when main.py imports auth.py.
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self._redis = redis_client

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Pass through: auth endpoints and all non-/api/ paths (static, root)
        if path.startswith("/api/auth/") or path == "/api/auth" or not path.startswith("/api"):
            return await call_next(request)

        # Require a valid Bearer token for all other /api/ routes
        token = _extract_bearer(request.headers.get("Authorization", ""))
        if not token:
            return JSONResponse(
                {"detail": "AUTHENTICATION REQUIRED"}, status_code=401
            )

        email = resolve_token(self._redis, token)
        if not email:
            return JSONResponse(
                {"detail": "AUTHENTICATION REQUIRED"}, status_code=401
            )

        # Inject resolved email into request state for downstream handlers
        request.state.player_email = email
        return await call_next(request)
