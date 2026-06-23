"""
Property-based tests for OTP login feature.

# Feature: otp-login
"""

import json
import os
import re
import sys

import fakeredis
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.auth_utils import check_and_increment, generate_otp


# ---------------------------------------------------------------------------
# Property 1: OTP Format Invariant
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------

@given(st.none())
@settings(max_examples=200)
def test_otp_format_invariant(_ignored):
    """
    # Feature: otp-login, Property 1: OTP Format Invariant

    generate_otp() must always produce exactly 6 decimal digits, zero-padded.
    Validates: Requirements 1.2
    """
    otp = generate_otp()
    assert re.fullmatch(r'\d{6}', otp) is not None, (
        f"generate_otp() returned {otp!r}, which is not a 6-digit numeric string"
    )


# ---------------------------------------------------------------------------
# Property 12: Rate Limiter Blocks Exactly After 3 Requests
# Validates: Requirements 5.2, 5.3
# ---------------------------------------------------------------------------

@given(st.emails())
@settings(max_examples=100)
def test_rate_limiter_blocks_exactly_after_3_requests(email: str):
    """
    # Feature: otp-login, Property 12: Rate Limiter Blocks Exactly After 3 Requests

    For any email address, calling check_and_increment with limit=3:
    - The first 3 calls must return True (requests allowed)
    - The 4th call must return False (request blocked)

    Validates: Requirements 5.2, 5.3
    """
    # Each hypothesis example gets a fresh FakeRedis to avoid state bleed
    r = fakeredis.FakeRedis(decode_responses=True)

    result_1 = check_and_increment(r, email, limit=3, window=60)
    result_2 = check_and_increment(r, email, limit=3, window=60)
    result_3 = check_and_increment(r, email, limit=3, window=60)
    result_4 = check_and_increment(r, email, limit=3, window=60)

    assert result_1 is True, f"1st request should be allowed for {email!r}"
    assert result_2 is True, f"2nd request should be allowed for {email!r}"
    assert result_3 is True, f"3rd request should be allowed for {email!r}"
    assert result_4 is False, f"4th request should be blocked for {email!r}"


# ---------------------------------------------------------------------------
# Property 13: Email Body Contains OTP and Correct Subject
# Validates: Requirements 7.3
# ---------------------------------------------------------------------------

import email as email_lib
import email.header
from unittest.mock import patch, MagicMock

from src.email_sender import send_otp_email, EMAIL_SUBJECT


@given(st.from_regex(r'\d{6}', fullmatch=True))
@settings(max_examples=100)
def test_email_body_contains_otp_and_correct_subject(otp):
    """
    # Feature: otp-login, Property 13: Email Body Contains OTP and Correct Subject

    For any 6-digit OTP, send_otp_email must construct a message whose
    Subject header exactly matches EMAIL_SUBJECT and whose plain-text body
    contains the OTP verbatim.
    Validates: Requirements 7.3
    """
    captured_calls = []

    with patch("src.email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)

        def capture_sendmail(from_addr, to_addrs, msg_string):
            captured_calls.append(msg_string)

        mock_smtp.sendmail.side_effect = capture_sendmail
        mock_smtp_cls.return_value = mock_smtp

        send_otp_email("commander@bsg.mil", otp)

    assert len(captured_calls) == 1, "sendmail must be called exactly once"

    raw_message = captured_calls[0]
    parsed = email_lib.message_from_string(raw_message)

    # Decode RFC 2047-encoded subject (MIMEText encodes non-ASCII headers)
    raw_subject = parsed["Subject"]
    decoded_parts = email.header.decode_header(raw_subject)
    decoded_subject = "".join(
        part.decode(charset or "ascii") if isinstance(part, bytes) else part
        for part, charset in decoded_parts
    )
    assert decoded_subject == EMAIL_SUBJECT, (
        f"Expected subject {EMAIL_SUBJECT!r}, got {decoded_subject!r}"
    )

    # decode=True returns bytes for encoded payloads; decode to str for comparison
    raw_body = parsed.get_payload(decode=True)
    body = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    assert otp in body, (
        f"OTP {otp!r} not found verbatim in email body: {body!r}"
    )


# ---------------------------------------------------------------------------
# Property 4: Response Never Reveals OTP
# Validates: Requirements 1.6
# ---------------------------------------------------------------------------

import src.auth as auth_module
from fastapi import FastAPI
from fastapi.testclient import TestClient


@given(st.emails())
@settings(max_examples=100)
def test_response_never_reveals_otp(email: str):
    """
    # Feature: otp-login, Property 4: Response Never Reveals OTP

    For any valid email address, the HTTP 200 response body from
    POST /api/auth/request-otp must not contain the OTP value that was
    stored in Redis.

    Validates: Requirements 1.6
    """
    fake_r = fakeredis.FakeRedis(decode_responses=True)

    # Wire the auth module to use fakeredis
    original_r = auth_module.r
    auth_module.set_redis(fake_r)

    try:
        app = FastAPI()
        app.include_router(auth_module.router)
        client = TestClient(app, raise_server_exceptions=True)

        with patch("src.email_sender.smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp.__exit__ = MagicMock(return_value=False)
            mock_smtp_cls.return_value = mock_smtp

            response = client.post(
                "/api/auth/request-otp",
                json={"email": email},
            )

        # Only check the invariant when the endpoint returns 200 (not rate-limited)
        if response.status_code == 200:
            # Pydantic EmailStr normalizes the email (e.g. lowercases domain).
            # Look up the stored OTP using the normalized form.
            normalized_email = auth_module.OTPRequest(email=email).email.lower()
            stored_otp = fake_r.get(f"otp:{normalized_email}")
            if stored_otp is None:
                # Fallback: scan for any otp:* key in the fresh-per-example Redis
                otp_keys = [k for k in fake_r.keys("otp:*")]
                assert len(otp_keys) == 1, (
                    f"Expected exactly 1 otp:* key, found {otp_keys}"
                )
                stored_otp = fake_r.get(otp_keys[0])

            assert stored_otp is not None, (
                f"Expected an otp key to be set in Redis after a successful request"
            )

            # The response body (as a raw JSON string) must not contain the OTP
            response_text = response.text
            assert stored_otp not in response_text, (
                f"OTP {stored_otp!r} was leaked in the response body: {response_text!r}"
            )
    finally:
        # Restore the original Redis client so other tests are unaffected
        auth_module.set_redis(original_r)


# ---------------------------------------------------------------------------
# Property 3: Invalid Email Always Rejected
# Validates: Requirements 1.5
# ---------------------------------------------------------------------------

import fakeredis as _fakeredis_module
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.auth as _auth_module

# Module-level app + client — created once to avoid per-example overhead.
# A fresh FakeRedis is injected per example to prevent state bleed.
_auth_app = FastAPI()
_auth_app.include_router(_auth_module.router)
_auth_client = TestClient(_auth_app, raise_server_exceptions=False)


@given(
    st.one_of(
        st.just(""),
        st.just("   "),
        st.text(min_size=1).filter(lambda s: "@" not in s),
    )
)
@settings(max_examples=100)
def test_invalid_email_always_rejected(invalid_email: str):
    """
    # Feature: otp-login, Property 3: Invalid Email Always Rejected

    For any string that is not a valid RFC 5321 email address, POST
    /api/auth/request-otp must return HTTP 422 and must not write any
    otp:* keys to Redis.

    Validates: Requirements 1.5
    """
    # Fresh FakeRedis per example — no state bleed between hypothesis runs
    fake_r = _fakeredis_module.FakeRedis(decode_responses=True)
    _auth_module.set_redis(fake_r)

    response = _auth_client.post(
        "/api/auth/request-otp",
        json={"email": invalid_email},
    )

    assert response.status_code == 422, (
        f"Expected 422 for invalid email {invalid_email!r}, "
        f"got {response.status_code}"
    )

    otp_keys = fake_r.keys("otp:*")
    assert len(otp_keys) == 0, (
        f"Redis must have no otp:* keys after rejecting {invalid_email!r}, "
        f"but found: {otp_keys}"
    )


# ---------------------------------------------------------------------------
# Property 2: OTP Storage Round-Trip
# Validates: Requirements 1.4
# ---------------------------------------------------------------------------

import fakeredis
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

import src.auth as auth_module

# Build a minimal FastAPI app that only mounts the auth router so we don't
# need a live Redis connection for the game layer.
from fastapi import FastAPI

_test_app = FastAPI()
_test_app.include_router(auth_module.router)

client = TestClient(_test_app)


@given(st.emails())
@settings(max_examples=100)
def test_otp_storage_round_trip(email: str):
    """
    # Feature: otp-login, Property 2: OTP Storage Round-Trip

    For any valid email address:
    - POST /api/auth/request-otp returns HTTP 200
    - Redis key otp:<email> exists after the call
    - Its TTL is in the range (0, 300]
    - The stored value is a 6-digit string matching \\d{6}

    Validates: Requirements 1.4
    """
    # Fresh fakeredis per example — no state bleed between hypothesis runs
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    auth_module.set_redis(fake_r)

    with patch("src.email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        mock_smtp_cls.return_value = mock_smtp

        response = client.post(
            "/api/auth/request-otp",
            json={"email": email},
        )

    # 1. HTTP 200
    assert response.status_code == 200, (
        f"Expected 200 for {email!r}, got {response.status_code}: {response.text}"
    )

    # The backend normalizes email via Pydantic EmailStr (punycode→unicode,
    # lowercased domain) then applies .lower() on top. We replicate that here.
    normalized_email = auth_module.OTPRequest(email=email).email.lower()

    # 2. Key exists in Redis
    assert fake_r.exists(f"otp:{normalized_email}"), (
        f"otp:{normalized_email} not found in Redis after request-otp"
    )

    # 3. TTL is in (0, 300]
    ttl = fake_r.ttl(f"otp:{normalized_email}")
    assert 0 < ttl <= 300, (
        f"TTL for otp:{normalized_email} is {ttl}, expected (0, 300]"
    )

    # 4. Stored value is a 6-digit string
    stored = fake_r.get(f"otp:{normalized_email}")
    assert re.fullmatch(r'\d{6}', stored) is not None, (
        f"Stored OTP {stored!r} for {normalized_email!r} is not a 6-digit string"
    )


# ---------------------------------------------------------------------------
# Property 6: Wrong OTP Returns Specific 401
# Validates: Requirements 2.4
# ---------------------------------------------------------------------------

from hypothesis import assume


@given(
    st.emails(),
    st.from_regex(r'\d{6}', fullmatch=True),
    st.from_regex(r'\d{6}', fullmatch=True),
)
@settings(max_examples=100)
def test_otp_mismatch_returns_401(email, stored_otp, submitted_otp):
    """
    # Feature: otp-login, Property 6: Wrong OTP Returns Specific 401

    For any email and any two distinct 6-digit OTPs, submitting the wrong OTP
    to POST /api/auth/verify-otp must:
      - return HTTP 401
      - return detail == "INVALID CODE"
      - leave the otp:<email> key still present in Redis (OTP not consumed)

    Validates: Requirements 2.4
    """
    assume(stored_otp != submitted_otp)

    fake_r = fakeredis.FakeRedis(decode_responses=True)

    original_r = auth_module.r
    auth_module.set_redis(fake_r)

    try:
        # Normalize email the same way Pydantic EmailStr does (lowercases domain)
        # so the key we pre-populate matches what the endpoint will look up.
        normalized_email = _normalize_email(email)

        # Pre-populate the stored OTP using the normalized email
        fake_r.set(f"otp:{normalized_email}", stored_otp, ex=300)

        app = FastAPI()
        app.include_router(auth_module.router)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.post(
            "/api/auth/verify-otp",
            json={"email": email, "code": submitted_otp},
        )

        assert response.status_code == 401, (
            f"Expected 401 for mismatched OTP, got {response.status_code}"
        )
        assert response.json()["detail"] == "INVALID CODE", (
            f"Expected detail 'INVALID CODE', got {response.json()['detail']!r}"
        )
        assert fake_r.exists(f"otp:{normalized_email}"), (
            f"otp:{normalized_email} was deleted on mismatch — OTP should be preserved"
        )
    finally:
        auth_module.set_redis(original_r)


# ---------------------------------------------------------------------------
# Property 5: Successful Verification Atomically Deletes OTP and Creates Session Token
# Validates: Requirements 2.3
# ---------------------------------------------------------------------------

import src.auth as auth_module
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, EmailStr


class _EmailNormalizer(BaseModel):
    email: EmailStr


def _normalize_email(raw: str) -> str:
    """Normalize an email the same way the backend does (Pydantic + full lowercase)."""
    return auth_module.OTPRequest(email=raw).email.lower()


def _make_auth_client(fake_r):
    """Build a fresh TestClient wired to the auth router with the given fakeredis."""
    auth_module.set_redis(fake_r)
    _app = FastAPI()
    _app.include_router(auth_module.router)
    return TestClient(_app, raise_server_exceptions=True)


@given(st.emails(), st.from_regex(r'\d{6}', fullmatch=True))
@settings(max_examples=100)
def test_successful_verification_atomically_deletes_otp_and_creates_session_token(
    email: str, code: str
):
    """
    # Feature: otp-login, Property 5: Successful Verification Atomically Deletes OTP and Creates Session Token

    For any valid email and 6-digit OTP:
    - Pre-populate otp:<email> in Redis with the OTP
    - POST /api/auth/verify-otp with matching email+code must:
      1. Return HTTP 200
      2. Delete otp:<email> (single-use enforced)
      3. Return a JSON body with a 'token' field
      4. Store token:<token> in Redis (mapped to email)
      5. Set TTL on token:<token> in (0, 86400]

    Validates: Requirements 2.3
    """
    # Fresh fakeredis per example to prevent state bleed between Hypothesis examples
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    client = _make_auth_client(fake_r)

    # Normalize email the same way Pydantic EmailStr does (lowercases domain)
    # so the key we pre-populate matches what the endpoint will look up.
    normalized_email = _normalize_email(email)

    # Pre-populate the OTP so verify-otp finds it
    fake_r.set(f"otp:{normalized_email}", code, ex=300)

    response = client.post(
        "/api/auth/verify-otp",
        json={"email": email, "code": code},
    )

    # 1. HTTP 200
    assert response.status_code == 200, (
        f"Expected 200 for email={email!r} code={code!r}, got {response.status_code}: {response.text}"
    )

    # 2. OTP key must be deleted (single-use)
    assert not fake_r.exists(f"otp:{normalized_email}"), (
        f"otp:{normalized_email} still exists after successful verification — single-use not enforced"
    )

    # 3. Response body has a 'token' field
    body = response.json()
    assert "token" in body, f"Response body missing 'token' field: {body}"
    token = body["token"]
    assert token, "token field must be non-empty"

    # 4. token:<token> key exists in Redis
    assert fake_r.exists(f"token:{token}"), (
        f"token:{token} not found in Redis after successful verification"
    )

    # 5. TTL is in (0, 86400]
    ttl = fake_r.ttl(f"token:{token}")
    assert 0 < ttl <= 86400, (
        f"Expected TTL in (0, 86400], got {ttl} for token:{token}"
    )

    # 6. token value maps back to the email
    stored_email = fake_r.get(f"token:{token}")
    assert stored_email == normalized_email, (
        f"token:{token} maps to {stored_email!r}, expected {normalized_email!r}"
    )


# ---------------------------------------------------------------------------
# Property 12: Token Validation Round-Trip
# Validates: Requirements 8.2
# ---------------------------------------------------------------------------


@given(st.emails(), st.uuids())
@settings(max_examples=100)
def test_token_validation_round_trip(email: str, token_uuid):
    """
    # Feature: otp-login, Property 12: Token Validation Round-Trip

    For any (email, uuid) pair, if we pre-populate token:<uuid> → email
    in Redis with TTL 86400, then GET /api/auth/validate with header
    Authorization: Bearer <uuid> must return HTTP 200 with the correct email.

    Validates: Requirements 8.2
    """
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    token_str = str(token_uuid)

    # Pre-populate token:<uuid> with the email value (TTL 86400)
    fake_r.set(f"token:{token_str}", email, ex=86400)

    # Wire the auth module to use fakeredis
    original_r = auth_module.r
    auth_module.set_redis(fake_r)

    try:
        app = FastAPI()
        app.include_router(auth_module.router)
        client = TestClient(app, raise_server_exceptions=True)

        response = client.get(
            "/api/auth/validate",
            headers={"Authorization": f"Bearer {token_str}"},
        )

        # Assert response is 200
        assert response.status_code == 200, (
            f"Expected 200 for valid token {token_str!r}, got {response.status_code}: {response.text}"
        )

        # Assert response body contains email field matching stored email
        body = response.json()
        assert "email" in body, (
            f"Response body missing 'email' field: {body}"
        )
        assert body["email"] == email, (
            f"Expected email {email!r}, got {body['email']!r}"
        )
    finally:
        auth_module.set_redis(original_r)


# ---------------------------------------------------------------------------
# Property 10: Re-Login Preserves Existing Game State
# Validates: Requirements 4.5
# ---------------------------------------------------------------------------


@given(st.emails())
@settings(max_examples=100)
def test_relogin_preserves_game_state(email: str):
    """
    # Feature: otp-login, Property 10: Re-Login Preserves Existing Game State

    For any email that has an existing session:<email> key in Redis,
    completing a full OTP authentication flow (request-otp → verify-otp)
    SHALL issue a new Session_Token but SHALL NOT modify or delete the
    session:<email> key.

    Validates: Requirements 4.5
    """
    fake_r = fakeredis.FakeRedis(decode_responses=True)

    original_r = auth_module.r
    auth_module.set_redis(fake_r)

    try:
        normalized_email = _normalize_email(email)

        # 1. Pre-populate session:<email> with arbitrary game state
        game_state_json = '{"test": "data"}'
        fake_r.set(f"session:{normalized_email}", game_state_json)

        # 2. Request OTP (stores OTP in Redis)
        app = FastAPI()
        app.include_router(auth_module.router)
        test_client = TestClient(app, raise_server_exceptions=True)

        with patch("src.auth.send_otp_email"):
            response = test_client.post(
                "/api/auth/request-otp",
                json={"email": email},
            )

        # If rate-limited, skip this example (not relevant to this property)
        if response.status_code == 429:
            return

        assert response.status_code == 200, (
            f"Expected 200 from request-otp, got {response.status_code}: {response.text}"
        )

        # 3. Read the OTP from fakeredis
        stored_otp = fake_r.get(f"otp:{normalized_email}")
        assert stored_otp is not None, (
            f"Expected otp:{normalized_email} to exist in Redis after request-otp"
        )

        # 4. Verify OTP
        response = test_client.post(
            "/api/auth/verify-otp",
            json={"email": email, "code": stored_otp},
        )

        # 5. Assert response is 200 (token issued)
        assert response.status_code == 200, (
            f"Expected 200 from verify-otp, got {response.status_code}: {response.text}"
        )
        assert "token" in response.json(), (
            f"Expected 'token' in response body, got {response.json()}"
        )

        # 6. Assert session:<email> STILL exists and its value is UNCHANGED
        session_value = fake_r.get(f"session:{normalized_email}")
        assert session_value is not None, (
            f"session:{normalized_email} was deleted after re-login — game state lost!"
        )
        assert session_value == game_state_json, (
            f"session:{normalized_email} was modified after re-login. "
            f"Expected {game_state_json!r}, got {session_value!r}"
        )
    finally:
        auth_module.set_redis(original_r)


# ---------------------------------------------------------------------------
# Property 8: Auth Guard on All Protected Endpoints
# Validates: Requirements 3.3, 8.3
# ---------------------------------------------------------------------------

from src.auth import BearerTokenMiddleware

# Game endpoints that require authentication.
# Tuple of (method, path) — all are POST except /api/state which is GET.
_GAME_ENDPOINTS = [
    ("GET", "/api/state"),
    ("POST", "/api/new_game"),
    ("POST", "/api/move"),
    ("POST", "/api/pulse"),
    ("POST", "/api/report"),
    ("POST", "/api/fight"),
    ("POST", "/api/colonize"),
    ("POST", "/api/scrap"),
    ("POST", "/api/build"),
    ("POST", "/api/repair"),
]


def _make_guarded_app(fake_r):
    """
    Build a FastAPI app that includes the auth router AND adds the
    BearerTokenMiddleware, so that game endpoints are protected.
    """
    from fastapi import FastAPI

    app = FastAPI()
    # Include the auth router (so /api/auth/* is accessible and whitelisted)
    app.include_router(auth_module.router)
    # Add the middleware that guards /api/* except /api/auth/*
    app.add_middleware(BearerTokenMiddleware, redis_client=fake_r)
    return app


@given(st.sampled_from(_GAME_ENDPOINTS))
@settings(max_examples=100)
def test_auth_middleware_blocks_unauthenticated_requests(endpoint):
    """
    # Feature: otp-login, Property 8: Auth Guard on All Protected Endpoints

    For any protected game endpoint under /api/ (excluding /api/auth/*),
    a request with:
      - No Authorization header at all
      - An invalid/expired Bearer token
    must receive HTTP 401 with detail "AUTHENTICATION REQUIRED".

    Validates: Requirements 3.3, 8.3
    """
    method, path = endpoint

    # Fresh fakeredis per example — the token won't exist so resolve_token → None
    fake_r = fakeredis.FakeRedis(decode_responses=True)

    original_r = auth_module.r
    auth_module.set_redis(fake_r)

    try:
        app = _make_guarded_app(fake_r)
        client = TestClient(app, raise_server_exceptions=False)

        # --- Case 1: No Authorization header ---
        if method == "GET":
            resp_no_header = client.get(path)
        else:
            resp_no_header = client.post(path, json={})

        assert resp_no_header.status_code == 401, (
            f"[No header] Expected 401 for {method} {path}, "
            f"got {resp_no_header.status_code}: {resp_no_header.text}"
        )
        assert resp_no_header.json()["detail"] == "AUTHENTICATION REQUIRED", (
            f"[No header] Expected detail 'AUTHENTICATION REQUIRED' for {method} {path}, "
            f"got {resp_no_header.json()['detail']!r}"
        )

        # --- Case 2: Invalid/expired Bearer token ---
        headers = {"Authorization": "Bearer invalid-token-12345"}
        if method == "GET":
            resp_bad_token = client.get(path, headers=headers)
        else:
            resp_bad_token = client.post(path, json={}, headers=headers)

        assert resp_bad_token.status_code == 401, (
            f"[Bad token] Expected 401 for {method} {path}, "
            f"got {resp_bad_token.status_code}: {resp_bad_token.text}"
        )
        assert resp_bad_token.json()["detail"] == "AUTHENTICATION REQUIRED", (
            f"[Bad token] Expected detail 'AUTHENTICATION REQUIRED' for {method} {path}, "
            f"got {resp_bad_token.json()['detail']!r}"
        )
    finally:
        auth_module.set_redis(original_r)


# ---------------------------------------------------------------------------
# Property 9: Game State Round-Trip via Email Key with 7-Day TTL
# Validates: Requirements 3.2, 4.1, 4.2
# ---------------------------------------------------------------------------


@given(st.emails())
@settings(max_examples=100)
def test_game_state_round_trip_email_key_with_7day_ttl(email: str):
    """
    # Feature: otp-login, Property 9: Game State Round-Trip via Email Key with 7-Day TTL

    For any email address, calling save_game(email, game) must:
    - Store the game state under Redis key `session:<email>`
    - Set a TTL that is > 0 and <= 604800 (7 days)
    - Store valid JSON that can be parsed back

    Validates: Requirements 3.2, 4.1, 4.2
    """
    # Set required env vars before importing from src.main (startup validation)
    os.environ.setdefault("GMAIL_ADDRESS", "test@example.com")
    os.environ.setdefault("GMAIL_APP_PASSWORD", "fake-password")
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")
    os.environ.setdefault("REDIS_PASSWORD", "")
    os.environ.setdefault("REDIS_USER", "default")

    from src.main import Game, save_game
    import src.main as main_module

    fake_r = fakeredis.FakeRedis(decode_responses=True)

    # Patch the module-level Redis client used by save_game
    original_r = main_module.r
    main_module.r = fake_r

    try:
        game = Game()
        save_game(email, game)

        # 1. Key exists under the correct pattern
        assert fake_r.exists(f"session:{email}"), (
            f"session:{email} not found in Redis after save_game"
        )

        # 2. TTL is > 0 and <= 604800
        ttl = fake_r.ttl(f"session:{email}")
        assert 0 < ttl <= 604800, (
            f"TTL for session:{email} is {ttl}, expected (0, 604800]"
        )

        # 3. Stored value is valid JSON
        stored = fake_r.get(f"session:{email}")
        assert stored is not None, (
            f"session:{email} value is None after save_game"
        )
        parsed = json.loads(stored)
        assert isinstance(parsed, dict), (
            f"Expected stored value to be a JSON object, got {type(parsed).__name__}"
        )
    finally:
        main_module.r = original_r


# ---------------------------------------------------------------------------
# Feature: otp-login, Property 9 (key resolution): Middleware Resolves Correct Game State Key
# Validates: Requirements 3.2
# ---------------------------------------------------------------------------

import json
from unittest.mock import patch

import src.main as main_module
from src.main import Game


@given(st.emails(), st.uuids())
@settings(max_examples=50)
def test_middleware_resolves_correct_game_state_key(email: str, token_uuid):
    """
    # Feature: otp-login, Property 9 (key resolution): Middleware Resolves Correct Game State Key

    For any (email, uuid) pair where token:<uuid> → email exists in Redis,
    hitting a game endpoint with Authorization: Bearer <uuid> must:
    - Read game state from session:<email>
    - Return HTTP 200
    - Not create any other session:* keys

    Validates: Requirements 3.2
    """
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    token_str = str(token_uuid)

    # Normalize email the way Pydantic EmailStr does
    normalized_email = _normalize_email(email)

    # 1. Pre-populate token:<uuid> → email (so middleware resolves email)
    fake_r.set(f"token:{token_str}", normalized_email, ex=86400)

    # 2. Create a valid Game and serialize it into session:<email>
    game = Game.__new__(Game)
    game.width = 20
    game.height = 10
    game.player = main_module.Player(x=0, y=0, ships=50, turns=1, alive=True, fuel=25, hull=100)
    game.galaxy = {}
    game.difficulty = "EASY"
    game.log = "TEST GAME STATE"
    game.game_over = False
    game.victory = False
    game.hunter_active = False
    game.hunter_x = -1
    game.hunter_y = -1
    game.hunter_cooldown = 0

    state_json = game.to_state().model_dump_json()
    fake_r.set(f"session:{normalized_email}", state_json, ex=604800)

    # Wire both auth module and main module to use the same fakeredis
    original_auth_r = auth_module.r
    original_main_r = main_module.r
    auth_module.set_redis(fake_r)

    try:
        with patch.object(main_module, "r", fake_r):
            # Build app with middleware + auth router + game endpoints
            from fastapi import FastAPI
            from src.auth import BearerTokenMiddleware

            app = FastAPI()
            app.include_router(auth_module.router)

            # Re-register game endpoints on this test app using main module functions
            @app.post("/api/report")
            def _report(request: main_module.Request):
                req_email = request.state.player_email
                g = main_module.get_game(req_email)
                g.report()
                main_module.save_game(req_email, g)
                return g.to_state()

            @app.get("/api/state")
            def _state(request: main_module.Request):
                req_email = request.state.player_email
                g = main_module.get_game(req_email)
                return g.to_state()

            app.add_middleware(BearerTokenMiddleware, redis_client=fake_r)

            test_client = TestClient(app, raise_server_exceptions=False)

            # Hit GET /api/state with the Bearer token
            response = test_client.get(
                "/api/state",
                headers={"Authorization": f"Bearer {token_str}"},
            )

            # Assert 200 — game state was found and returned
            assert response.status_code == 200, (
                f"Expected 200 for GET /api/state with valid token, "
                f"got {response.status_code}: {response.text}"
            )

            # The response should contain our test game log
            body = response.json()
            assert body["log"] == "TEST GAME STATE", (
                f"Expected log 'TEST GAME STATE', got {body['log']!r} — "
                f"game state was not read from session:{normalized_email}"
            )

            # Assert session:<email> still exists (was read from)
            assert fake_r.exists(f"session:{normalized_email}"), (
                f"session:{normalized_email} should still exist after GET /api/state"
            )

            # Assert no OTHER session:* keys were created
            all_session_keys = [
                k for k in fake_r.keys("session:*")
                if k != f"session:{normalized_email}"
            ]
            assert len(all_session_keys) == 0, (
                f"Unexpected session keys created: {all_session_keys}. "
                f"Game state should only be stored under session:{normalized_email}"
            )
    finally:
        auth_module.set_redis(original_auth_r)
