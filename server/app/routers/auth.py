"""Authentication routes: register, login, me, password."""

import re
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

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
NICKNAME_MAX = 16

# 登录失败限流：同一 (用户名, IP) 在窗口内连错 LOGIN_MAX_FAILS 次就先拒掉，
# 窗口过了自动解除。按 (用户名, IP) 而不是只按用户名：别人在自己机器上乱试
# 锁不住你；只按 IP 又拦不住对单个账号的慢速爆破。登录成功清零。
# 只放内存：重启清空无所谓，它防的是脚本连刷，不是持久的封禁。
LOGIN_MAX_FAILS = 10
LOGIN_WINDOW_S = 600
_login_fails: dict[tuple[str, str], list[float]] = {}
_login_lock = threading.Lock()     # 同步路由跑在线程池里


def _check_password_rule(password: str):
    if not (6 <= len(password) <= 32):
        raise HTTPException(400, "密码需6-32位")


def _recent_fails(key: tuple[str, str], now: float) -> list[float]:
    fails = [t for t in _login_fails.get(key, ()) if now - t < LOGIN_WINDOW_S]
    if fails:
        _login_fails[key] = fails
    else:
        _login_fails.pop(key, None)
    return fails


@router.post("/register")
def register(req: RegisterRequest):
    if not _USERNAME_RE.match(req.username):
        raise HTTPException(400, "用户名需3-20位字母数字下划线")
    _check_password_rule(req.password)
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
        token = create_token(user["id"], row["salt"])
        return {"token": token, "user": user}
    finally:
        conn.close()


@router.post("/login")
def login(req: LoginRequest, request: Request):
    key = (req.username, request.client.host if request.client else "")
    with _login_lock:
        if len(_recent_fails(key, time.time())) >= LOGIN_MAX_FAILS:
            raise HTTPException(429, "密码错误次数过多，请 10 分钟后再试")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (req.username,)
        ).fetchone()
        if not row or not verify_password(
            req.password, row["password_hash"], row["salt"]
        ):
            with _login_lock:
                _login_fails.setdefault(key, []).append(time.time())
            raise HTTPException(401, "用户名或密码错误")
        with _login_lock:
            _login_fails.pop(key, None)
        if row["banned"]:
            raise HTTPException(403, "账号已被封禁")
        user = row_to_user(row)
        token = create_token(user["id"], row["salt"])
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
    # 昵称会随房间广播给所有人、显示在各个对局屏的名牌上：不限长度的话一条
    # 几 MB 的昵称就能把整房的 lobby 消息撑爆。空白当作「清除昵称」。
    nickname = (req.nickname or "").strip() or None
    if nickname is not None and len(nickname) > NICKNAME_MAX:
        raise HTTPException(400, f"昵称最多{NICKNAME_MAX}个字")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET nickname = ? WHERE id = ?", (nickname, user_id)
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
        _check_password_rule(req.new_password)
        # 换新盐 ⇒ 之前签出去的 token 全部作废（见 security._pw_stamp）
        pw_hash, salt = hash_password(req.new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (pw_hash, salt, user_id),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
