"""Room REST routes: create, list, get, join, leave."""

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_conn, row_to_user
from app.security import get_current_user_id
from app.schemas import CreateRoomRequest, JoinRoomRequest
from app import room_manager as rm

router = APIRouter(prefix="/api/rooms", tags=["rooms"])
ROOM_NAME_MAX = 20
ROOM_PASSWORD_MAX = 32


def _get_user(user_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "用户不存在")
        return row_to_user(row)
    finally:
        conn.close()


@router.post("")
def create_room(req: CreateRoomRequest, user_id: int = Depends(get_current_user_id)):
    # 房名随房间列表广播给所有人：限长，空白当作没填（用默认房名）
    name = (req.name or "").strip() or None
    if name is not None and len(name) > ROOM_NAME_MAX:
        raise HTTPException(400, f"房间名最多{ROOM_NAME_MAX}个字")
    if req.password is not None and len(req.password) > ROOM_PASSWORD_MAX:
        raise HTTPException(400, f"房间密码最多{ROOM_PASSWORD_MAX}位")
    user = _get_user(user_id)
    try:
        room = rm.create_room(
            user, name, req.password or None,
            game=req.game,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return rm.room_to_dict(room)


@router.get("")
def list_rooms(
    game: str = "", user_id: int = Depends(get_current_user_id)
):
    rooms = rm.get_all_rooms()
    if game:
        rooms = [r for r in rooms if r["game"] == game]
    return {"items": [rm.room_to_dict(r) for r in rooms]}


@router.get("/{code}")
def get_room(code: str, user_id: int = Depends(get_current_user_id)):
    room = rm.get_room(code)
    if not room:
        raise HTTPException(404, "房间不存在")
    if room["game"] == "spider" and room["host_id"] != user_id:
        raise HTTPException(403, "这是其他玩家的单人牌局")
    # in_game：请求者还算不算这局的在局玩家。房间页拿它做**自愈**——万一漏收了
    # game_start 广播（僵尸连接），主动查一次就能自己进对局页，不会永远卡在房间页。
    return {**rm.room_to_dict(room), "in_game": rm.user_in_game(room, user_id)}


@router.post("/{code}/join")
def join_room(
    code: str, req: JoinRoomRequest, user_id: int = Depends(get_current_user_id)
):
    user = _get_user(user_id)
    try:
        room = rm.join_room(code, user, req.password)
    except ValueError as e:
        msg = str(e)
        if msg == "__NOT_FOUND__":
            raise HTTPException(404, "房间不存在")
        if msg == "房间密码错误":
            raise HTTPException(403, "房间密码错误")
        if msg in ("房间已满", "你已在房间中"):
            raise HTTPException(409, msg)
        raise HTTPException(409, msg)
    # Mark player connected if reconnecting
    rm.handle_player_reconnect(code, user["id"])
    return rm.room_to_dict(room)


@router.post("/{code}/leave")
def leave_room(code: str, user_id: int = Depends(get_current_user_id)):
    room = rm.get_room(code)
    if not room:
        return {"ok": True}
    if room["game"] == "spider" and room["host_id"] != user_id:
        raise HTTPException(403, "这是其他玩家的单人牌局")
    action, _ = rm.leave_room(code, user_id)
    return {"ok": True}
