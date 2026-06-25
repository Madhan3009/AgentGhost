import pytest
from fastapi import HTTPException
from jose import jwt
from agents.auth import (
    verify_password,
    verify_credentials,
    create_access_token,
    verify_jwt_token,
    pwd_context
)
from agents.config import JWT_SECRET, JWT_ALGORITHM


def test_verify_password():
    password = "secretpassword"
    hashed = pwd_context.hash(password)
    assert verify_password(password, hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_credentials():
    # Test valid dev credentials
    user = verify_credentials("admin", "ghostadmin")
    assert user is not None
    assert user["username"] == "admin"
    assert user["role"] == "reviewer"

    # Test invalid credentials
    assert verify_credentials("admin", "wrongpassword") is None
    assert verify_credentials("unknown_user", "ghostadmin") is None


def test_create_and_verify_token():
    token = create_access_token(subject="admin", role="reviewer")
    assert isinstance(token, str)

    # Verify signature and decode content
    payload = verify_jwt_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "reviewer"


def test_verify_invalid_token():
    # Tampered token
    token = create_access_token(subject="admin", role="reviewer")
    tampered = token + "bad"
    
    with pytest.raises(HTTPException) as exc_info:
        verify_jwt_token(tampered)
    assert exc_info.value.status_code == 401
    assert "Invalid or expired authentication token" in exc_info.value.detail

    # Random string instead of token
    with pytest.raises(HTTPException):
        verify_jwt_token("not-a-token")
