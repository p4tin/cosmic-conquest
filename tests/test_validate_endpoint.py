"""
Tests for GET /api/auth/validate endpoint.
"""
import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_otp_login.config import OTPAuthConfig
from fastapi_otp_login.router import get_auth_router
from fastapi_otp_login.utils import issue_token
import pytest


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)

@pytest.fixture()
def client(fake_redis):
    app = FastAPI()
    config = OTPAuthConfig(sender_email="test@test.com", smtp_username="test", smtp_password="test")
    app.include_router(get_auth_router(fake_redis, config))
    return TestClient(app, raise_server_exceptions=True)

def test_validate_valid_token_returns_200_and_email(client, fake_redis):
    email = "commander@bsg.mil"
    token = issue_token(fake_redis, email)
    response = client.get("/api/auth/validate", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == email

def test_validate_missing_header_returns_401(client):
    response = client.get("/api/auth/validate")
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"

def test_validate_unknown_token_returns_401(client):
    response = client.get("/api/auth/validate", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"

def test_validate_malformed_auth_header_returns_401(client):
    response = client.get("/api/auth/validate", headers={"Authorization": "Token sometoken"})
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"

def test_validate_expired_token_returns_401(client, fake_redis):
    email = "admiral@bsg.mil"
    token = issue_token(fake_redis, email)
    fake_redis.delete(f"token:{token}")
    response = client.get("/api/auth/validate", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"
