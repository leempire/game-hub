"""Participant migration, exact identities, atomic writes and hot restart keys."""

import copy
import json
import sqlite3

import pytest

from app import database, participants, persistence


@pytest.fixture()
def battle_db(tmp_path):
    old_path = database._db_path
    path = str(tmp_path / "battle.db")
    database.init_db(path)
    conn = database.get_conn()
    conn.executemany("""INSERT INTO users
                     (id, username, nickname, password_hash, salt, created_at)
                     VALUES (?, ?, ?, 'hash', 'salt', '2026-01-01')""",
                     [(1, "alice", "same"), (2, "alice_long", "alice")])
    conn.commit()
    conn.close()
    yield path
    database._db_path = old_path


def room(*, practice=False):
    return {
        "code": "123456", "game": "gomoku", "practice": practice,
        "players": [{"id": 1, "username": "alice", "nickname": "same"},
                    {"id": -2, "username": "bot_123456", "is_bot": True}],
        "game_state": {"winner": "black", "reason": "surrender", "board": [],
                       "player_colors": {1: "black", -2: "white"}},
    }


def read(sql, args=()):
    conn = database.get_conn()
    try:
        return [dict(row) for row in conn.execute(sql, args)]
    finally:
        conn.close()


def insert_legacy(conn, players, results, snapshot=None):
    return conn.execute("""INSERT INTO game_records
                        (game, room_code, players_json, results_json, snapshot_json,
                         started_at, ended_at)
                        VALUES ('gomoku', '123456', ?, ?, ?, '2026-01-01', '2026-01-02')""",
                        (json.dumps(players), json.dumps(results),
                         json.dumps(snapshot or {}))).lastrowid


def test_record_and_participants_saved_atomically_with_bot(battle_db):
    battle = room()
    database.save_game_record(battle)
    records = read("SELECT * FROM game_records")
    assert len(records) == 1
    assert records[0]["has_bots"] == 1
    rows = read("SELECT * FROM game_participants ORDER BY user_id DESC")
    assert [(p["user_id"], p["result"], p["rank"]) for p in rows] == [
        (1, "win", 1), (-2, "lose", 2),
    ]
    assert database.get_user_history(1)["total"] == 1
    assert database.get_user_history(2)["total"] == 0
    assert database.get_user_stats(1)["current_streak"] == 1


def test_hot_restart_duplicate_save_and_new_round(battle_db):
    battle = room()
    database.save_game_record(battle)
    # Persistence's JSON codec preserves scalar internal game fields.
    restored = copy.deepcopy(battle)
    database.save_game_record(restored)
    assert len(read("SELECT id FROM game_records")) == 1
    assert len(read("SELECT record_id FROM game_participants")) == 2
    battle["game_state"] = {key: value for key, value in battle["game_state"].items()
                            if key != "_record_key"}
    database.save_game_record(battle)
    assert len(read("SELECT id FROM game_records")) == 2


def test_failed_participant_write_rolls_back_record_and_can_retry(battle_db, monkeypatch):
    battle = room()
    writer = participants.write_participants

    def fail(*args):
        raise RuntimeError("synthetic participant write failure")

    monkeypatch.setattr(participants, "write_participants", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        database.save_game_record(battle)
    assert read("SELECT id FROM game_records") == []
    monkeypatch.setattr(participants, "write_participants", writer)
    database.save_game_record(battle)
    assert len(read("SELECT id FROM game_records")) == 1


def test_practice_does_not_affect_history_or_streak(battle_db):
    battle = room(practice=True)
    database.save_game_record(battle)
    assert read("SELECT practice FROM game_records")[0]["practice"] == 1
    assert database.get_user_history(1)["total"] == 0
    stats = database.get_user_stats(1)
    assert stats["total"] == stats["wins"] == stats["current_streak"] == 0


def test_backfill_exact_username_no_nickname_or_substring_match(battle_db):
    conn = database.get_conn()
    insert_legacy(conn, [{"username": "alice_long", "nickname": "alice"},
                         {"username": "deleted_account"},
                         {"username": "bot_123456"}],
                  [{"username": "alice_long", "result": "win", "score": 123},
                   {"username": "deleted_account", "result": "lose"},
                   {"username": "bot_123456", "result": "lose"}])
    participants.backfill(conn)
    participants.backfill(conn)
    conn.commit()
    conn.close()
    assert database.get_user_history(1)["total"] == 0
    assert database.get_user_history(2)["total"] == 1
    assert database.get_user_stats(2)["best_scores"] == {"gomoku": 123}
    assert read("SELECT has_bots, participants_indexed FROM game_records") == [
        {"has_bots": 1, "participants_indexed": 1}]
    assert len(read("SELECT user_id FROM game_participants")) == 2


def test_migration_of_database_created_before_participant_schema(tmp_path):
    path = str(tmp_path / "old.db")
    old_path = database._db_path
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, nickname TEXT,
                            password_hash TEXT, salt TEXT, role TEXT, banned INTEGER,
                            created_at TEXT);
        CREATE TABLE game_records (id INTEGER PRIMARY KEY, game TEXT, room_code TEXT,
                            players_json TEXT, results_json TEXT, snapshot_json TEXT,
                            started_at TEXT, ended_at TEXT);
        INSERT INTO users VALUES (1, 'alice', NULL, 'h', 's', 'user', 0, '2026-01-01');
    """)
    insert_legacy(conn, [{"username": "alice"}], [{"username": "alice", "result": "win"}])
    # Bad old data must not prevent the rest of the upgrade or future startups.
    conn.execute("""INSERT INTO game_records VALUES
                 (2, 'gomoku', '654321', 'broken', '{}', '{}', '', '')""")
    conn.commit()
    conn.close()
    try:
        database.init_db(path)
        database.init_db(path)
        assert database.get_user_history(1)["total"] == 1
        assert len(read("SELECT * FROM game_participants")) == 1
        assert all(row["participants_indexed"] == 1
                   for row in read("SELECT participants_indexed FROM game_records"))
    finally:
        database._db_path = old_path


def test_participant_metrics_copy_only_scalar_scores(battle_db):
    conn = database.get_conn()
    record_id = insert_legacy(
        conn, [{"user_id": 1, "username": "alice"}],
        [{"username": "alice", "result": "win", "score": 600,
          "hands": ["R5"], "board": [[2]], "difficulty": "beginner"}],
        {"ranking": [{"seat": 0, "rank": 3, "lines": 20}],
         "boards": {"1": [[2]]}, "scores": {"1": 500}},
    )
    participants.backfill(conn)
    conn.commit()
    conn.close()
    indexed = read("SELECT * FROM game_participants WHERE record_id = ?", (record_id,))[0]
    assert indexed["rank"] == 3
    assert json.loads(indexed["metrics_json"]) == {
        "score": 600, "difficulty": "beginner", "lines": 20,
    }


def test_user_query_uses_participant_index(battle_db):
    database.save_game_record(room())
    conn = database.get_conn()
    detail = " ".join(row[3] for row in conn.execute("""EXPLAIN QUERY PLAN
        SELECT r.id FROM game_participants p JOIN game_records r ON r.id = p.record_id
        WHERE p.user_id = ? AND r.practice = 0""", (1,)))
    conn.close()
    assert "idx_participants_user_record" in detail


def test_real_persisted_final_room_before_save_callback_deduplicates(battle_db, monkeypatch):
    # _set_game_over persists the final room BEFORE invoking record callbacks.
    # A crash after the INSERT may restore a snapshot without _record_key.
    monkeypatch.setattr(persistence, "_enabled", False)
    persistence.init()
    battle = room()
    battle["game_started_at"] = "2026-11-04T10:00:00.123456+00:00"
    persistence.save_room(battle)
    database.save_game_record(battle)
    restored = persistence.load_all_rooms()[0]
    assert "_record_key" not in restored["game_state"]
    database.save_game_record(restored)
    assert len(read("SELECT id FROM game_records")) == 1
    assert len(read("SELECT record_id FROM game_participants")) == 2
    restored["game_started_at"] = "2026-11-04T10:01:00.123456+00:00"
    restored["game_state"].pop("_record_key")
    database.save_game_record(restored)
    assert len(read("SELECT id FROM game_records")) == 2
