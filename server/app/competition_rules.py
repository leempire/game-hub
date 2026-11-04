"""Canonical game-rule scopes for public scores and daily challenges."""

import hashlib
import json
from datetime import datetime, timezone

from app import room_settings

GAMES = (
    "gomoku", "reversi", "xiangqi", "luzhanqi", "connect4", "uttt", "dots",
    "battleship", "ludo", "uno", "minesweeper", "2048", "tetris", "stasis", "spider",
)
CHALLENGE_GAMES = ("2048", "tetris", "minesweeper")
_SINGLE_PLAYER = {"2048", "tetris", "minesweeper", "spider"}


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def challenge_seed(game: str, day: str) -> int:
    """Stable across processes/restarts: never use Python's randomized hash()."""
    if game == "minesweeper":
        # Keep the existing daily-room seed; the challenge additionally fixes
        # the safe first-reveal cell, making the entire mine layout identical.
        return int(day.replace("-", ""))
    digest = hashlib.sha256(f"gamehub:daily:v1:{day}:{game}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def default_room(game: str, difficulty: str = "intermediate", *, challenge=False) -> dict:
    room = {"game": game, "players": [None] * (1 if game in _SINGLE_PLAYER else 2)}
    if game == "minesweeper":
        room.update(difficulty="beginner" if challenge else difficulty, mine_daily=challenge)
    return room


def rule_scope(room: dict) -> tuple[str, str]:
    """Hash effective rule values, omitting only the derived UNO preset label."""
    game = room["game"]
    specs = room_settings.specs_for(game)
    # New disabled/default rules must not rewrite every existing standard
    # scope. Resolve defaults through read functions too (not spec.default),
    # since UNO's normalized rule defaults are derived by the registry.
    baseline = {"game": game}
    values = {}
    for spec in specs:
        if spec.key in ("preset", "animation_speed", "animation_scale", "allow_spectators"):
            continue
        value = room_settings.value_of(spec, room)
        if value != room_settings.value_of(spec, baseline):
            values[spec.key] = value
    values["player_count"] = len(room["players"])
    canonical = json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    key = hashlib.sha256(f"{game}:v1:{canonical}".encode()).hexdigest()
    labels = [f"{item['label']}·{item['value_label']}"
              for item in room_settings.items_for(game, room) if item.get("in_summary")
              and item["key"] not in ("animation_speed", "animation_scale", "allow_spectators")]
    label = " / ".join([f"{len(room['players'])}人", *(labels or ["标准规则"])])
    return key, label


def challenge_info(room: dict) -> dict | None:
    if not room.get("challenge_date"):
        return None
    info = {"date": room["challenge_date"]}
    if room["game"] == "minesweeper":
        info["first_reveal"] = {"x": 4, "y": 4}
    return info
