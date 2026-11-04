"""Authenticated public leaderboard and personal daily challenge endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app import daily_challenges, room_manager as rm
from app.database import get_conn, row_to_user
from app.leaderboards import get_leaderboard
from app.security import get_current_user_id

router = APIRouter(prefix="/api", tags=["competition"])


@router.get("/leaderboards/{game}")
def leaderboard(game: str, metric: str = "wins", window: str = "all",
                difficulty: str = "intermediate", challenge_date: str | None = None,
                rules_key: str | None = None, limit: int = Query(20, ge=1, le=100),
                user_id: int = Depends(get_current_user_id)):
    try:
        return get_leaderboard(game, user_id, metric=metric, window=window,
                               difficulty=difficulty, challenge_date=challenge_date, limit=limit, rules_key=rules_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/challenges/today")
def challenges_today(user_id: int = Depends(get_current_user_id)):
    result = daily_challenges.get_daily_status(user_id)
    for item in result["games"]:
        for room in list(rm._rooms.values()):
            if (room.get("challenge_date") == result["date"] and room["game"] == item["game"]
                    and room["host_id"] == user_id
                    and any(p["id"] == user_id for p in room["players"])
                    and (room["status"] == "playing" or not room.get("round"))):
                item["room_code"] = room["code"]
                break
    return result


@router.post("/challenges/{game}/start")
def challenge_start(game: str, user_id: int = Depends(get_current_user_id)):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "用户不存在")
        user = row_to_user(row)
    finally:
        conn.close()
    try:
        return rm.room_to_dict(daily_challenges.start_challenge(user, game))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
