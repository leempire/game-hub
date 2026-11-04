"""Indexed battle participants and idempotent legacy record migration.

This module only uses the caller's connection: record and participant writes
share a transaction, including during upgrade. No hidden board state is copied.
"""

import json
import sqlite3


_RECORD_COLUMNS = {
    "record_key": "TEXT",
    "has_bots": "INTEGER NOT NULL DEFAULT 0",
    "practice": "INTEGER NOT NULL DEFAULT 0",
    "challenge_date": "TEXT",
    "participants_indexed": "INTEGER NOT NULL DEFAULT 0",
}


def create_schema(conn: sqlite3.Connection):
    """Upgrade existing databases without rewriting their record payloads."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(game_records)")}
    for name, definition in _RECORD_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE game_records ADD COLUMN {name} {definition}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_participants (
            record_id INTEGER NOT NULL REFERENCES game_records(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            result TEXT NOT NULL,
            rank INTEGER,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (record_id, user_id)
        )
    """)
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_participants_user_record
                    ON game_participants(user_id, record_id DESC)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_participants_result_user
                    ON game_participants(result, user_id, record_id)""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_records_key
                    ON game_records(record_key) WHERE record_key IS NOT NULL""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_records_competition
                    ON game_records(game, practice, has_bots, ended_at, id)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_records_challenge
                    ON game_records(challenge_date, game, practice, id)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_records_unindexed
                    ON game_records(participants_indexed, id)""")
    backfill(conn)


def _parse_list(raw: str) -> list:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _parse_dict(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def is_bot(player: dict) -> bool:
    uid = player.get("user_id", player.get("id"))
    return bool(player.get("is_bot") or (isinstance(uid, int) and uid < 0))


def _legacy_identity(conn, player: dict, room_code: str, seat: int) -> int | None:
    """Old records lack IDs; use exact account names, never display names."""
    uid = player.get("user_id", player.get("id"))
    if isinstance(uid, int) and not isinstance(uid, bool):
        return uid
    # Old bots all used bot_<room code>, including multiple bots in one room.
    if is_bot(player) or player.get("username") == f"bot_{room_code}":
        return -(seat + 1)
    row = conn.execute("SELECT id FROM users WHERE username = ?",
                       (player.get("username"),)).fetchone()
    return row["id"] if row else None


def _ranking_entry(snapshot: dict, player: dict, seat: int) -> dict:
    ranking = snapshot.get("ranking") or []
    for entry in ranking:
        if not isinstance(entry, dict):
            continue
        if entry.get("seat") == seat:
            return entry
        uid = player.get("user_id", player.get("id"))
        if uid is not None and entry.get("user_id", entry.get("uid")) == uid:
            return entry
    matches = [entry for entry in ranking if isinstance(entry, dict)
               and entry.get("username") == player.get("username")]
    return matches[0] if len(matches) == 1 else {}


def _metrics(snapshot: dict, result: dict, ranking: dict, uid: int) -> dict:
    # Only scalar public scores belong in the index. Never copy boards, hands,
    # cells, or the entire snapshot (which may contain final private information).
    names = ("score", "elapsed_ms", "difficulty", "lines", "level", "max_tile",
             "finished_count", "correct_flags", "wrong_flags", "revealed_count")
    metrics = {key: value for key in names
               if isinstance((value := result.get(key, ranking.get(key))), (int, float, str))
               and not isinstance(value, bool)}
    for key, source in (("score", "scores"), ("lines", "lines"), ("level", "levels")):
        mapping = snapshot.get(source) or {}
        if isinstance(mapping, dict):
            value = mapping.get(str(uid), mapping.get(uid))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics.setdefault(key, value)
    return metrics


def write_participants(conn, record_id: int, players: list, results: list,
                       snapshot: dict, room_code: str) -> bool:
    """Write indexed identities and metrics; return whether this was a bot game."""
    has_bots = False
    for seat, player in enumerate(players):
        if not isinstance(player, dict):
            continue
        uid = _legacy_identity(conn, player, room_code, seat)
        bot = is_bot(player) or player.get("username") == f"bot_{room_code}"
        has_bots = has_bots or bot
        if uid is None:
            continue  # Deleted/unknown historical accounts must not block migration.
        result = next((entry for entry in results if isinstance(entry, dict)
                       and entry.get("username") == player.get("username")), {})
        # Multiple old bots share a username. Current result builders preserve
        # seating order, so prefer that exact row when it matches.
        if seat < len(results) and isinstance(results[seat], dict):
            if results[seat].get("username") == player.get("username"):
                result = results[seat]
        ranking = _ranking_entry(snapshot, player, seat)
        outcome = result.get("result", "end")
        if outcome not in ("win", "lose", "draw", "end"):
            outcome = "end"
        rank = result.get("rank", ranking.get("rank"))
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            rank = {"win": 1, "draw": 1, "lose": 2}.get(outcome)
        metrics = _metrics(snapshot, result, ranking, uid)
        conn.execute("""INSERT OR IGNORE INTO game_participants
                     (record_id, user_id, result, rank, metrics_json)
                     VALUES (?, ?, ?, ?, ?)""",
                     (record_id, uid, outcome, rank,
                      json.dumps(metrics, ensure_ascii=False)))
    return has_bots


def backfill(conn: sqlite3.Connection):
    """Resume partial upgrades safely; each historical record is indexed once."""
    rows = conn.execute("""SELECT id, room_code, players_json, results_json, snapshot_json
                         FROM game_records WHERE participants_indexed = 0
                         ORDER BY id""").fetchall()
    for row in rows:
        has_bots = write_participants(conn, row["id"], _parse_list(row["players_json"]),
                                      _parse_list(row["results_json"]),
                                      _parse_dict(row["snapshot_json"]), row["room_code"])
        conn.execute("""UPDATE game_records SET has_bots = ?, participants_indexed = 1
                        WHERE id = ?""", (int(has_bots), row["id"]))
