"""Explicit user identity overrides legacy bot-name guessing in ranking metadata."""

import pytest

from app import database, participants
from app.leaderboards import get_leaderboard


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "identity.db"))
    database.init_db(database._db_path)
    conn = database.get_conn()
    conn.executemany("""INSERT INTO users
                     (id, username, password_hash, salt, created_at)
                     VALUES (?, ?, 'h', 's', '2026-01-01')""",
                     [(1, "bot_ABC123"), (2, "other_player")])
    conn.commit()
    conn.close()


def test_real_user_with_legacy_bot_style_name_still_enters_public_rankings(isolated_db):
    room = {"code": "ABC123", "game": "gomoku", "players": [
        {"id": 1, "username": "bot_ABC123", "is_bot": False},
        {"id": 2, "username": "other_player", "is_bot": False}],
        "game_state": {"winner": "black", "reason": "five", "board": [],
                       "player_colors": {1: "black", 2: "white"}}}
    database.save_game_record(room)
    conn = database.get_conn()
    try:
        row = conn.execute("SELECT has_bots FROM game_records").fetchone()
        assert row["has_bots"] == 0
    finally:
        conn.close()
    assert get_leaderboard("gomoku", 1)["own"]["value"] == 1


def test_legacy_bot_name_without_ids_still_marks_bot_game(isolated_db):
    conn = database.get_conn()
    try:
        record = conn.execute("""INSERT INTO game_records
                             (game, room_code, players_json, results_json, snapshot_json, started_at, ended_at)
                             VALUES ('gomoku', 'ABC123', '[]', '[]', '{}', '2026-01-01', '2026-01-01')""")
        marked = participants.write_participants(conn, record.lastrowid,
            [{"username": "bot_ABC123"}], [{"username": "bot_ABC123", "result": "win"}], {}, "ABC123")
        assert marked is True
        row = conn.execute("SELECT user_id FROM game_participants WHERE record_id = ?", (record.lastrowid,)).fetchone()
        assert row["user_id"] < 0
    finally:
        conn.close()
