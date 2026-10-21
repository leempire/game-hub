"""Password hashing (pbkdf2) and JWT authentication."""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import database

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
    return hmac.compare_digest(computed.hex(), pw_hash)


def _pw_stamp(salt: str) -> str:
    """密码指纹：每次改密码 `hash_password` 都换新盐，指纹随之改变。

    写进 token 的 `pv`，验证时与库里的盐比对 —— 改了密码，之前签出去的 token
    当场作废（原来 7 天内都还能用，丢了号改密码也赶不走对方）。取盐的哈希而不是
    盐本身，token 是明文 base64，不往外露库里的东西。
    """
    return hashlib.sha256(salt.encode()).hexdigest()[:16]


def create_token(user_id: int, salt: str) -> str:
    """Create a JWT token for the given user (`salt` = 当前密码的盐)."""
    payload = {
        "user_id": user_id,
        "pv": _pw_stamp(salt),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, _jwt_secret, algorithm=_ALGORITHM)


def _verify(token: str) -> int | None:
    """验签 + 查库：用户还在、没被封、密码没改过。任何一条不满足返回 None。

    原来只验签不查库，封号只拦得住「重新登录」，手里已有 token 的人照样能开房、
    连 WS 打到 token 过期。查的是主键，一次很便宜。
    **没有 `pv` 的老 token 放行**（上线前签出去的，最多 7 天自然过期），
    否则一上线全员被踢回登录页。
    """
    try:
        payload = jwt.decode(token, _jwt_secret, algorithms=[_ALGORITHM])
        user_id = int(payload["user_id"])
    except Exception:
        return None
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT banned, salt FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or row["banned"]:
        return None
    pv = payload.get("pv")
    if pv is not None and not hmac.compare_digest(str(pv), _pw_stamp(row["salt"])):
        return None
    return user_id


def decode_token(token: str) -> int:
    """Decode JWT and return user_id. Raises 401 on invalid/expired/revoked."""
    user_id = _verify(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token无效或已过期",
        )
    return user_id


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> int:
    """FastAPI dependency: extract and validate JWT, return user_id.

    同步函数：要查库，FastAPI 会把它放进线程池，不占事件循环。"""
    return decode_token(credentials.credentials)


def decode_token_ws(token: str) -> int | None:
    """Decode token for WebSocket (returns None on failure instead of raising)."""
    return _verify(token)
