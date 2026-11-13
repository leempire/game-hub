"""好友房间邀请：持久化、按房间现状实时校验。

邀请只是「请朋友来这个房」的一条记录 —— **真正入房仍走 `/api/rooms/{code}/join`**，
所以密码、满员、开局这些规则只需要在 join 里维护一份。这里额外挡住的几条
（密码房 / 挑战房 / 练习房 / 已开局 / 已满员）是为了让邀请按钮当场给出中文原因，
而不是等朋友点「加入」才失败。
"""
from fastapi import HTTPException

from app import room_manager as rm
from app.social_common import active_peer, blocked, connection, link, profile, timestamp

INVITE_RATE_WINDOW_SECONDS = 60
INVITE_RATE_LIMIT = 20


def _room_view(room):
    """只下发邀请页要显示的房间公开字段（**不含密码与内部状态**）。"""
    return {
        "code": room["code"],
        "name": room["name"],
        "game": room["game"],
        "status": room["status"],
        "players": len(room["players"]),
        "max_players": room["max_players"],
    }


def _invitable(room, inviter_id):
    """邀请发出时检查：这个房现在还能不能再进人。"""
    if room is None:
        return "房间不存在或已解散"
    if room.get("challenge_date") or room.get("practice"):
        return "挑战房和练习房不能邀请好友"
    if room["password"] is not None:
        return "密码房不能邀请好友，请先取消房间密码"
    if not any(p["id"] == inviter_id for p in room["players"]):
        return "你不在这个房间中"
    if room["status"] != "waiting":
        return "对局已开始，暂时不能邀请"
    if len(room["players"]) >= room["max_players"]:
        return "房间已满"
    return None


def _still_valid(room, actor, inviter_id):
    """列出邀请时再校验一次：房间可能已经解散 / 开局 / 满员 / 自己已经进房。"""
    if _invitable(room, inviter_id) is not None:
        return False
    ids = {p["id"] for p in room["players"]}
    return actor not in ids


def invite(actor, peer, code):
    code = (code or "").strip().upper()
    with connection(True) as conn:
        active_peer(conn, actor, peer)
        relation = link(conn, actor, peer)
        if relation is None or relation["status"] != "accepted":
            raise HTTPException(403, "成为好友后才能邀请")
        room = rm.get_room(code)
        reason = _invitable(room, actor)
        if reason == "房间不存在或已解散":
            raise HTTPException(404, reason)
        if reason:
            # 「发起人不在房里」是权限问题，其余都是房间当前不能进人。
            raise HTTPException(403 if reason == "你不在这个房间中" else 409, reason)
        if any(p["id"] == peer for p in room["players"]):
            raise HTTPException(409, "对方已经在房间里了")
        recent = conn.execute(
            "SELECT COUNT(*) FROM room_invites WHERE inviter_id=? AND created_at>=?",
            (actor, timestamp(INVITE_RATE_WINDOW_SECONDS)),
        ).fetchone()[0]
        if recent >= INVITE_RATE_LIMIT:
            raise HTTPException(429, "邀请太频繁，请稍后再试")
        now = timestamp()
        # 同一房间的待答邀请只留一条：换一位好友来邀就把发起人更新成他，
        # 免得上一位发起人离开后这条邀请被误判失效。用 UPSERT 而不是
        # 「先查再写」，并发两次邀请不会撞唯一索引。
        invite_id = conn.execute(
            """INSERT INTO room_invites(room_code,inviter_id,invitee_id,status,created_at,updated_at)
               VALUES(?,?,?,'pending',?,?)
               ON CONFLICT(room_code,invitee_id) WHERE status='pending'
               DO UPDATE SET inviter_id=excluded.inviter_id,
                             created_at=excluded.created_at,updated_at=excluded.updated_at
               RETURNING id""",
            (code, actor, peer, now, now),
        ).fetchone()["id"]
    return {"ok": True, "invite_id": invite_id}


def pending(actor):
    """我收到的、**此刻仍然有效**的房间邀请。失效的顺手标成 expired。"""
    with connection(True) as conn:
        rows = conn.execute(
            "SELECT * FROM room_invites WHERE invitee_id=? AND status='pending' ORDER BY id DESC",
            (actor,),
        ).fetchall()
        items = []
        for row in rows:
            room = rm.get_room(row["room_code"])
            inviter = conn.execute(
                "SELECT id,username,nickname FROM users WHERE id=? AND banned=0",
                (row["inviter_id"],),
            ).fetchone()
            relation = link(conn, row["inviter_id"], actor)
            valid = (
                inviter is not None
                and relation is not None
                and relation["status"] == "accepted"
                and not blocked(conn, actor, row["inviter_id"])
                and _still_valid(room, actor, row["inviter_id"])
            )
            if not valid:
                conn.execute(
                    "UPDATE room_invites SET status='expired',updated_at=? WHERE id=?",
                    (timestamp(), row["id"]),
                )
                continue
            items.append({
                "id": row["id"],
                "inviter": profile(inviter),
                "room": _room_view(room),
                "created_at": row["created_at"],
            })
        return {"items": items}


def count(actor):
    return len(pending(actor)["items"])


def decline(actor, invite_id):
    with connection(True) as conn:
        cursor = conn.execute(
            "UPDATE room_invites SET status='declined',updated_at=? "
            "WHERE id=? AND invitee_id=? AND status='pending'",
            (timestamp(), invite_id, actor),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "邀请不存在或已失效")
    return {"ok": True}