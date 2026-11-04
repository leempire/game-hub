"""Server-authoritative UTC daily challenge state and completion streaks."""

import json
import threading
from datetime import date, timedelta

from app.database import get_conn
from app import room_manager as rm
from app.competition_rules import CHALLENGE_GAMES, utc_today

_START_LOCK = threading.Lock()

TITLES = {"2048": "2048 每日挑战", "tetris": "方块每日挑战", "minesweeper": "扫雷每日挑战"}


def _streak(days: set[str], today: str) -> int:
    cursor = date.fromisoformat(today)
    if today not in days:
        cursor -= timedelta(days=1)
    count = 0
    while cursor.isoformat() in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def get_daily_status(user_id: int) -> dict:
    today = utc_today()
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT r.game, r.challenge_date, p.metrics_json FROM game_participants p
            JOIN game_records r ON r.id = p.record_id
            WHERE p.user_id = ? AND r.practice = 0 AND r.has_bots = 0
              AND r.challenge_date IS NOT NULL AND r.challenge_date <= ?
              AND json_extract(p.metrics_json, '$.completed') = 1
        """, (user_id, today)).fetchall()
    finally:
        conn.close()
    games = []
    for game in CHALLENGE_GAMES:
        mine = [row for row in rows if row["game"] == game]
        days = {row["challenge_date"] for row in mine}
        key = "elapsed_ms" if game == "minesweeper" else "score"
        values = [json.loads(row["metrics_json"]).get(key)
                  for row in mine if row["challenge_date"] == today]
        values = [value for value in values if isinstance(value, (int, float)) and value >= 0]
        best = (min(values) if game == "minesweeper" else max(values)) if values else None
        games.append({"game": game, "title": TITLES[game], "completed": today in days,
                      "streak": _streak(days, today), "best_value": best, "room_code": None})
    return {"date": today, "games": games}


def start_challenge(user: dict, game: str) -> dict:
    """Create/reuse a private personal lobby; normal WS start remains authoritative."""
    if game not in CHALLENGE_GAMES:
        raise ValueError("这款游戏没有每日挑战")
    with _START_LOCK:
        today = utc_today()
        for room in list(rm._rooms.values()):
            if (room.get("challenge_date") == today and room["game"] == game
                    and room["host_id"] == user["id"]
                    and any(p["id"] == user["id"] for p in room["players"])
                    and (room["status"] == "playing" or not room.get("round"))):
                return room
        room = rm.create_room(user, TITLES[game], None, game=game)
        room.update(challenge_date=today, max_players=1)
        if game == "minesweeper":
            room.update(difficulty="beginner", mine_daily=True)
        rm._touch_activity(room)
        return room
