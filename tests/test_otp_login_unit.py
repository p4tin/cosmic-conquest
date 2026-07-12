"""
Unit tests for specific OTP login scenarios.
"""
import os
import smtplib
from unittest.mock import MagicMock, patch

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_otp_login.config import OTPAuthConfig
from fastapi_otp_login.middleware import BearerTokenMiddleware
from fastapi_otp_login.router import get_auth_router
from fastapi_otp_login.utils import issue_token
import pytest



@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def auth_client(fake_redis):
    app = FastAPI()
    config = OTPAuthConfig(sender_email="test@test.com", smtp_username="test", smtp_password="test")
    app.include_router(get_auth_router(fake_redis, config))
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def full_client(fake_redis, monkeypatch):
    import src.main as main_module
    monkeypatch.setattr(main_module, "r", fake_redis)

    app = FastAPI()
    config = OTPAuthConfig(sender_email="test@test.com", smtp_username="test", smtp_password="test")
    app.include_router(get_auth_router(fake_redis, config))

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


def test_verify_otp_expired(auth_client):
    response = auth_client.post(
        "/api/auth/verify-otp",
        json={"email": "pilot@bsg.mil", "code": "123456"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "CODE EXPIRED OR NOT FOUND"


def test_smtp_failure_returns_503(auth_client):
    with patch("fastapi_otp_login.email.smtplib.SMTP") as mock_smtp_cls:
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


def test_missing_env_vars_raises_at_startup():
    import asyncio
    from src.main import validate_env

    with patch.dict(os.environ, {}, clear=False):
        env_backup = {}
        for var in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
            env_backup[var] = os.environ.pop(var, None)

        try:
            with pytest.raises(RuntimeError) as exc_info:
                asyncio.run(validate_env())
            assert "GMAIL_ADDRESS" in str(exc_info.value) or "GMAIL_APP_PASSWORD" in str(exc_info.value)
        finally:
            for var, val in env_backup.items():
                if val is not None:
                    os.environ[var] = val



def test_action_request_has_no_session_id():
    from src.main import ActionRequest
    model_fields = ActionRequest.model_fields
    assert "session_id" not in model_fields


def test_game_state_has_no_session_id():
    from src.main import GameState
    model_fields = GameState.model_fields
    assert "session_id" not in model_fields


def test_new_game_overwrites_existing_state(full_client, fake_redis):
    email = "starbuck@bsg.mil"
    token = issue_token(fake_redis, email)
    fake_state = '{"sentinel": "old_game_marker"}'
    fake_redis.set(f"session:{email}", fake_state)

    assert fake_redis.get(f"session:{email}") == fake_state

    response = full_client.post(
        "/api/new_game",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    new_state_raw = fake_redis.get(f"session:{email}")
    assert new_state_raw is not None
    assert "sentinel" not in new_state_raw

    data = response.json()
    assert "player" in data
    assert "galaxy" in data
