"""
Auth utility functions for Cosmic Conquest OTP authentication.

All functions are pure (take Redis client `r` as a parameter) — no global
Redis access in this module.
"""

import hmac
import secrets
import uuid
from typing import Optional


def generate_otp() -> str:
    """Return a cryptographically random 6-digit string (zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def store_otp(r, email: str, otp: str) -> None:
    """Write otp:<email> to Redis with TTL=300 seconds. Resets attempt counter."""
    r.set(f"otp:{email}", otp, ex=300)
    # Reset verify attempts whenever a new OTP is issued
    r.delete(f"otp_attempts:{email}")


def verify_and_consume_otp(r, email: str, submitted: str) -> Optional[bool]:
    """
    Atomically verify and consume an OTP using GETDEL to prevent race conditions.

    Also enforces a max of 5 verification attempts per OTP. After 5 failed
    attempts the OTP is deleted (brute-force protection).

    Returns:
        True   — OTP matched and was consumed
        False  — OTP exists but does not match submitted value
        None   — key does not exist (expired, never requested, or exhausted)
    """
    # Check attempt count first
    attempts_key = f"otp_attempts:{email}"
    attempts = r.get(attempts_key)
    if attempts is not None and int(attempts) >= 5:
        # Too many failed attempts — delete the OTP and reject
        r.delete(f"otp:{email}")
        r.delete(attempts_key)
        return None

    # Use GETDEL for atomic read-and-delete (Redis 6.2+)
    # If GETDEL isn't available, fall back to GET + DELETE in a pipeline
    otp_key = f"otp:{email}"
    
    # Capture remaining TTL before GETDEL removes the key
    remaining_ttl = r.ttl(otp_key)
    if remaining_ttl is None or remaining_ttl <= 0:
        remaining_ttl = 300

    try:
        stored = r.getdel(otp_key)
    except AttributeError:
        # Fallback for older Redis clients without getdel
        stored = r.get(otp_key)
        if stored is not None:
            r.delete(otp_key)

    if stored is None:
        return None  # key missing → expired or never requested

    # Constant-time comparison to prevent timing side-channel
    if not hmac.compare_digest(stored.encode(), submitted.encode()):
        # Mismatch: re-store the OTP with the original remaining TTL
        r.set(otp_key, stored, ex=remaining_ttl)
        r.incr(attempts_key)
        r.expire(attempts_key, remaining_ttl)
        return False

    # Match: OTP already deleted by GETDEL, clean up attempts
    r.delete(attempts_key)
    return True


def check_and_increment(r, email: str, limit: int = 3, window: int = 60) -> bool:
    """
    Atomic rate limiter using INCR-first pattern.

    Increments the counter first, then checks if it exceeds the limit.
    This eliminates the TOCTOU race between GET and INCR.

    Returns True if the request is allowed, False if over limit.
    """
    key = f"ratelimit:{email}"
    # Atomically increment and set TTL in a pipeline
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window, nx=True)  # set TTL only on first hit
    results = pipe.execute()

    current_count = results[0]  # result of INCR

    if current_count > limit:
        return False
    return True


def issue_token(r, email: str) -> str:
    """
    Generate a UUID session token, store token:<uuid> → email with TTL=86400,
    and return the UUID string.
    """
    token = str(uuid.uuid4())
    r.set(f"token:{token}", email, ex=86400)
    return token


def resolve_token(r, token: str) -> Optional[str]:
    """
    Look up token:<token> in Redis.

    Returns the associated email string, or None if the key is absent or
    expired.
    """
    value = r.get(f"token:{token}")
    return value if value else None
