"""
Ghost Requirement Agent — JWT Authentication Module
====================================================
Issues and validates HS256 JWT Bearer tokens to protect mutation
endpoints (approve, dismiss) against unauthorized access.

Dev mode: Hardcoded admin credentials allow easy local testing.
Prod mode: Replace `verify_credentials()` with a PostgreSQL user
           table lookup or an OIDC/Vault identity provider.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from agents.config import JWT_SECRET, JWT_ALGORITHM, JWT_TOKEN_EXPIRY_MINUTES, IS_DEVELOPMENT

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Password hashing (bcrypt)
# ─────────────────────────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ─────────────────────────────────────────────────────────────────────────────
# Dev credentials
# In production: replace this dict with a DB query.
# ─────────────────────────────────────────────────────────────────────────────

_DEV_USERS: dict = {
    "admin": {
        "username": "admin",
        "hashed_password": pwd_context.hash("ghostadmin"),
        "role": "reviewer",
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# HTTP Bearer scheme (auto_error=True → FastAPI raises 403 if header missing)
# ─────────────────────────────────────────────────────────────────────────────

bearer_scheme = HTTPBearer(auto_error=True)


# ─────────────────────────────────────────────────────────────────────────────
# Credential helpers
# ─────────────────────────────────────────────────────────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against its bcrypt hash."""
    return pwd_context.verify(plain, hashed)


def verify_credentials(username: str, password: str) -> Optional[dict]:
    """
    Validate username + password.
    Returns the user record dict on success, or None on failure.

    Production: replace _DEV_USERS lookup with PostgreSQL query.
    """
    user = _DEV_USERS.get(username)
    if not user:
        logger.warning(f"[Auth] Login attempt for unknown user: '{username}'")
        return None
    if not verify_password(password, user["hashed_password"]):
        logger.warning(f"[Auth] Invalid password for user: '{username}'")
        return None
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Token operations
# ─────────────────────────────────────────────────────────────────────────────

def create_access_token(subject: str, role: str = "reviewer") -> str:
    """
    Sign a HS256 JWT containing: sub, role, iat, exp.

    Args:
        subject:  The authenticated user identifier (username / user_id).
        role:     User role — currently 'reviewer' only.

    Returns:
        Signed JWT string.
    """
    now = datetime.utcnow()
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_TOKEN_EXPIRY_MINUTES),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    logger.info(f"[Auth] Issued JWT for subject='{subject}' role='{role}' "
                f"expires_in={JWT_TOKEN_EXPIRY_MINUTES}m")
    return token


def verify_jwt_token(token: str) -> dict:
    """
    Decode and validate a JWT Bearer token.

    Raises:
        HTTPException(401): on missing, expired, or tampered tokens.

    Returns:
        Decoded payload dict (contains 'sub', 'role', 'exp', 'iat').
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token. Please obtain a new token via POST /api/auth/token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        subject: str = payload.get("sub")
        if not subject:
            logger.warning("[Auth] JWT missing 'sub' claim")
            raise credentials_exception
        return payload
    except JWTError as e:
        logger.warning(f"[Auth] JWT validation failed: {e}")
        raise credentials_exception


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Dependency
# ─────────────────────────────────────────────────────────────────────────────

def require_reviewer(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    FastAPI route dependency that enforces JWT authentication.

    Usage:
        @app.post("/api/dashboard/actions/{id}/approve")
        def approve(action_id: str, current_user: dict = Depends(require_reviewer)):
            approved_by = current_user["sub"]

    Raises:
        HTTPException(401): if token is missing, expired, or invalid.
        HTTPException(403): if HTTP Bearer header is absent entirely.
    """
    return verify_jwt_token(credentials.credentials)
