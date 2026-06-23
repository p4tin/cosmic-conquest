"""
Tests for GET /api/auth/validate endpoint.

Covers:
  - Req 8.1: endpoint exists and accepts Authorization: Bearer header
  - Req 8.2: valid, non-expired token → HTTP 200 + correct email
  - Req 8.3: missing / invalid / expired token → HTTP 401
"""

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the auth router and patch the Redis client before it runs
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.auth_utils import issue_token


# ---------------------------------------------------------------------------
# App fixture: minimal FastAPI app with only the auth router mounted and
# the Redis client swapped for fakeredis so no real Redis is needed.
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def client(fake_redis, monkeypatch):
    """TestClient wired to a fakeredis instance instead of real Redis."""
    # Patch the `r` used inside src/auth.py
    import src.auth as auth_module
    monkeypatch.setattr(auth_module, "r", fake_redis)

    app = FastAPI()
    app.include_router(auth_module.router)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Req 8.2 — valid token returns 200 + email
# ---------------------------------------------------------------------------

def test_validate_valid_token_returns_200_and_email(client, fake_redis):
    """A valid, non-expired Bearer token must yield HTTP 200 with the email."""
    email = "commander@bsg.mil"
    token = issue_token(fake_redis, email)

    response = client.get(
        "/api/auth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == email


# ---------------------------------------------------------------------------
# Req 8.3 — missing Authorization header → 401
# ---------------------------------------------------------------------------

def test_validate_missing_header_returns_401(client):
    """No Authorization header must yield HTTP 401."""
    response = client.get("/api/auth/validate")
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"


# ---------------------------------------------------------------------------
# Req 8.3 — invalid / unknown token → 401
# ---------------------------------------------------------------------------

def test_validate_unknown_token_returns_401(client):
    """A token not present in Redis must yield HTTP 401."""
    response = client.get(
        "/api/auth/validate",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"


# ---------------------------------------------------------------------------
# Req 8.3 — malformed Authorization header (no 'Bearer ' prefix) → 401
# ---------------------------------------------------------------------------

def test_validate_malformed_auth_header_returns_401(client):
    """An Authorization header without 'Bearer ' prefix must yield HTTP 401."""
    response = client.get(
        "/api/auth/validate",
        headers={"Authorization": "Token sometoken"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"


# ---------------------------------------------------------------------------
# Req 8.3 — expired token (key deleted from Redis) → 401
# ---------------------------------------------------------------------------

def test_validate_expired_token_returns_401(client, fake_redis):
    """A token that has been deleted (simulating expiry) must yield HTTP 401."""
    email = "admiral@bsg.mil"
    token = issue_token(fake_redis, email)

    # Simulate expiry by deleting the key
    fake_redis.delete(f"token:{token}")

    response = client.get(
        "/api/auth/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "AUTHENTICATION REQUIRED"
