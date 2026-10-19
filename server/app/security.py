"""Password hashing (pbkdf2) and JWT authentication."""

import hashlib
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_jwt_secret: str = "secret"
_ALGORITHM = "HS256"
security_scheme = HTTPBearer()


def init_security(secret: str):
    """Set the JWT secret at startup."""
    global _jwt_secret
    _jwt_secret = secret


def hash_password(password: str) -> tuple[str, str]:
    """Hash a password with pbkdf2_hmac, returns (hash_hex, salt_hex)."""
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
    return pw_hash.hex(), salt.hex()


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    """Verify a password against stored hash and salt."""
    computed = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 100000
    )
    return computed.hex() == pw_hash


def create_token(user_id: int) -> str:
    """Create a JWT token for the given user."""
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, _jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> int:
    """Decode JWT and return user_id. Raises on invalid/expired."""
    try:
        payload = jwt.decode(token, _jwt_secret, algorithms=[_ALGORITHM])
        return int(payload["user_id"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token无效或已过期",
        )


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> int:
    """FastAPI dependency: extract and validate JWT, return user_id."""
    return decode_token(credentials.credentials)


def decode_token_ws(token: str) -> int | None:
    """Decode token for WebSocket (returns None on failure instead of raising)."""
    try:
        payload = jwt.decode(token, _jwt_secret, algorithms=[_ALGORITHM])
        return int(payload["user_id"])
    except Exception:
        return None
