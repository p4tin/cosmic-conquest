"""
Unit tests for specific OTP login scenarios.

Covers:
  - Req 2.5: missing Redis key → 401 "CODE EXPIRED OR NOT FOUND"
  - Req 7.4: SMTP failure → 503 "EMAIL DELIVERY FAILED. TRY AGAIN LATER."
  - Req 7.5: missing GMAIL env vars → RuntimeError at startup
  - Req 3.5: ActionRequest has no session_id field
  - Req 3.5: GameState has no session_id field
  - Req 4.6: new_game overwrites existing session:<email> state
"""

import os
import sys
import smtplib
from unittest.mock import patch, MagicMock

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import src.auth as auth_module
from src.auth_utils import issue_token


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def auth_client(fake_redis, monkeypatch):
    """TestClient wired to the auth router only, using fakeredis."""
    monkeypatch.setattr(auth_module, "r", fake_redis)
    app = FastAPI()
    app.include_router(auth_module.router)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def full_client(fake_redis, monkeypatch):
    """
    TestClient for the full app (auth + game endpoints + BearerTokenMiddleware),
    using fakeredis so game state can be pre-populated and inspected.
    """
    import src.main as main_module
    from src.auth import BearerTokenMiddleware

    monkeypatch.setattr(auth_module, "r", fake_redis)
    monkeypatch.setattr(main_module, "r", fake_redis)

    app = FastAPI()
    app.include_router(auth_module.router)

    # Import and register all game endpoints via the main app's routes
    # Re-use the existing app routes rather than re-declaring them
    from src.main import (
        new_game, get_state, move_player, pulse_scan,
        report_sector, fight_sector, colonize_sector,
        scrap_ships, build_infrastructure, repair_hull,
    )

    app.add_api_route("/api/new_game", new_game, methods=["POST"])
    app.add_api_route("/api/state", get_state, methods=["GET"])
    app.add_api_route("/api/move", move_player, methods=["POST"])

    app.add_middleware(BearerTokenMiddleware, redis_client=fake_redis)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Req 2.5 — missing Redis OTP key → 401 "CODE EXPIRED OR NOT FOUND"
# ---------------------------------------------------------------------------

def test_verify_otp_expired(auth_client):
    """No otp:<email> key in Redis must yield 401 CODE EXPIRED OR NOT FOUND."""
    response = auth_client.post(
        "/api/auth/verify-otp",
        json={"email": "pilot@bsg.mil", "code": "123456"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "CODE EXPIRED OR NOT FOUND"


# ---------------------------------------------------------------------------
# Req 7.4 — SMTP failure → 503
# ---------------------------------------------------------------------------

def test_smtp_failure_returns_503(auth_client):
    """
    When smtplib raises SMTPException during email delivery,
    POST /api/auth/request-otp must return 503.
    """
    with patch("src.email_sender.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=False)
        mock_smtp.sendmail.side_effect = smtplib.SMTPException("SMTP failure")
        mock_smtp_cls.return_value = mock_smtp

        response = auth_client.post(
            "/api/auth/request-otp",
            json={"email": "commander@bsg.mil"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "EMAIL DELIVERY FAILED. TRY AGAIN LATER."


# ---------------------------------------------------------------------------
# Req 7.5 — missing GMAIL env vars → RuntimeError
# ---------------------------------------------------------------------------

def test_missing_env_vars_raises_at_startup():
    """
    The validate_env startup hook must raise RuntimeError when
    GMAIL_ADDRESS or GMAIL_APP_PASSWORD are absent from the environment.
    """
    import asyncio
    from src.main import validate_env

    with patch.dict(os.environ, {}, clear=False):
        # Ensure both vars are absent
        env_backup = {}
        for var in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
            env_backup[var] = os.environ.pop(var, None)

        try:
            with pytest.raises(RuntimeError) as exc_info:
                asyncio.get_event_loop().run_until_complete(validate_env())
            assert "GMAIL_ADDRESS" in str(exc_info.value) or "GMAIL_APP_PASSWORD" in str(exc_info.value)
        finally:
            # Restore env
            for var, val in env_backup.items():
                if val is not None:
                    os.environ[var] = val


# ---------------------------------------------------------------------------
# Req 3.5 — ActionRequest has no session_id field
# ---------------------------------------------------------------------------

def test_action_request_has_no_session_id():
    """ActionRequest model must not expose a session_id field."""
    from src.main import ActionRequest

    model_fields = ActionRequest.model_fields
    assert "session_id" not in model_fields, (
        "ActionRequest should not have a session_id field after OTP migration"
    )


# ---------------------------------------------------------------------------
# Req 3.5 — GameState has no session_id field
# ---------------------------------------------------------------------------

def test_game_state_has_no_session_id():
    """GameState model must not expose a session_id field."""
    from src.main import GameState

    model_fields = GameState.model_fields
    assert "session_id" not in model_fields, (
        "GameState should not have a session_id field after OTP migration"
    )


# ---------------------------------------------------------------------------
# Req 4.6 — new_game overwrites existing session:<email> state
# ---------------------------------------------------------------------------

def test_new_game_overwrites_existing_state(full_client, fake_redis):
    """
    Pre-populating session:<email> then calling POST /api/new_game must
    overwrite the key with a fresh game state (not preserve the old one).
    """
    email = "starbuck@bsg.mil"

    # Issue a valid token for this email
    token = issue_token(fake_redis, email)

    # Pre-populate an obviously fake game state JSON
    fake_state = '{"sentinel": "old_game_marker"}'
    fake_redis.set(f"session:{email}", fake_state)

    # Verify it was stored
    assert fake_redis.get(f"session:{email}") == fake_state

    # POST /api/new_game with the auth token
    response = full_client.post(
        "/api/new_game",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, f"Unexpected status: {response.status_code} — {response.text}"

    # The stored state must have changed (old sentinel is gone)
    new_state_raw = fake_redis.get(f"session:{email}")
    assert new_state_raw is not None
    assert "sentinel" not in new_state_raw, (
        "new_game did not overwrite the existing session state"
    )

    # Response body must be a valid GameState (has 'player' and 'galaxy' keys)
    data = response.json()
    assert "player" in data
    assert "galaxy" in data
