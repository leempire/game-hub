"""Admin routes: user management."""

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_conn, row_to_user
from app.security import get_current_user_id
from app.schemas import BanRequest, RoleRequest
from app import ws

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user_id: int = Depends(get_current_user_id)) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row or row["role"] != "admin":
            raise HTTPException(403, "需要管理员权限")
        return user_id
    finally:
        conn.close()


@router.get("/users")
def list_users(
    page: int = 1,
    size: int = 20,
    keyword: str = "",
    admin_id: int = Depends(_require_admin),
):
    conn = get_conn()
    try:
        offset = (page - 1) * size
        if keyword:
            like = f"%{keyword}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE ? OR nickname LIKE ?",
                (like, like),
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT * FROM users
                   WHERE username LIKE ? OR nickname LIKE ?
                   ORDER BY id DESC LIMIT ? OFFSET ?""",
                (like, like, size, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM users ORDER BY id DESC LIMIT ? OFFSET ?",
                (size, offset),
            ).fetchall()
        return {"total": total, "items": [row_to_user(r) for r in rows]}
    finally:
        conn.close()


@router.post("/users/{uid}/ban")
async def ban_user(uid: int, req: BanRequest, admin_id: int = Depends(_require_admin)):
    if uid == admin_id:
        raise HTTPException(400, "不能封禁自己")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        conn.execute(
            "UPDATE users SET banned = ? WHERE id = ?", (int(req.banned), uid)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()
    if req.banned:
        # token 从此验不过（security._verify 查 banned），但已经连着的 WS 不会
        # 再验一次 —— 当场断开，4001 让客户端别再重连
        await ws.kick_user(uid, "账号已被封禁")
    return row_to_user(row)


@router.post("/users/{uid}/role")
def change_role(uid: int, req: RoleRequest, admin_id: int = Depends(_require_admin)):
    if uid == admin_id:
        raise HTTPException(400, "不能修改自己的角色")
    if req.role not in ("admin", "user"):
        raise HTTPException(400, "角色只能是 admin 或 user")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (req.role, uid))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return row_to_user(row)
    finally:
        conn.close()
