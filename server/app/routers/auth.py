"""Authentication routes: register, login, me, password."""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import get_conn, row_to_user
from app.security import (
    hash_password,
    verify_password,
    create_token,
    get_current_user_id,
)
from app.schemas import (
    RegisterRequest,
    LoginRequest,
    NicknameRequest,
    PasswordRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")


@router.post("/register")
def register(req: RegisterRequest):
    if not _USERNAME_RE.match(req.username):
        raise HTTPException(400, "用户名需3-20位字母数字下划线")
    if not (6 <= len(req.password) <= 32):
        raise HTTPException(400, "密码需6-32位")
    pw_hash, salt = hash_password(req.password)
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (req.username,)
        ).fetchone()
        if existing:
            raise HTTPException(409, "用户名已存在")
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """INSERT INTO users (username, password_hash, salt, role, created_at)
               VALUES (?, ?, ?, 'user', ?)""",
            (req.username, pw_hash, salt, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        user = row_to_user(row)
        token = create_token(user["id"])
        return {"token": token, "user": user}
    finally:
        conn.close()


@router.post("/login")
def login(req: LoginRequest):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (req.username,)
        ).fetchone()
        if not row:
            raise HTTPException(401, "用户名或密码错误")
        if not verify_password(req.password, row["password_hash"], row["salt"]):
            raise HTTPException(401, "用户名或密码错误")
        if row["banned"]:
            raise HTTPException(403, "账号已被封禁")
        user = row_to_user(row)
        token = create_token(user["id"])
        return {"token": token, "user": user}
    finally:
        conn.close()


@router.get("/me")
def get_me(user_id: int = Depends(get_current_user_id)):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        return row_to_user(row)
    finally:
        conn.close()


@router.put("/me")
def update_nickname(req: NicknameRequest, user_id: int = Depends(get_current_user_id)):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET nickname = ? WHERE id = ?", (req.nickname, user_id)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row_to_user(row)
    finally:
        conn.close()


@router.post("/password")
def change_password(req: PasswordRequest, user_id: int = Depends(get_current_user_id)):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        if not verify_password(req.old_password, row["password_hash"], row["salt"]):
            raise HTTPException(400, "原密码错误")
        pw_hash, salt = hash_password(req.new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (pw_hash, salt, user_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
