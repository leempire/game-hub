"""Public score scopes, stable ties, UTC challenge completions and streaks."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import competition_records, database, daily_challenges, participants
from app.competition_rules import challenge_seed, default_room, rule_scope
from app.leaderboards import get_leaderboard


TODAY = "2026-11-04"


@pytest.fixture(autouse=True)
def battle_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_db_path", str(tmp_path / "competition.db"))
    database.init_db(database._db_path)
    monkeypatch.setattr(daily_challenges, "utc_today", lambda: TODAY)
    conn = database.get_conn()
    for uid in range(1, 6):
        conn.execute("""INSERT INTO users
                     (id, username, nickname, password_hash, salt, created_at)
                     VALUES (?, ?, ?, 'hash', 'salt', '2026-01-01')""",
                     (uid, f"player{uid}", f"P{uid}"))
    conn.commit()
    conn.close()


def insert(game, uid, *, result="win", score=None, elapsed=None, completed=True,
           challenge_date=None, practice=False, bots=False, settings=None, ended=None):
    room = default_room(game)
    if settings:
        room.update(settings)
    key, label = rule_scope(room)
    metrics = {"completed": completed}
    if game == "minesweeper":
        metrics["difficulty"] = room.get("difficulty", "intermediate")
    if score is not None:
        metrics["score"] = score
    if elapsed is not None:
        metrics["elapsed_ms"] = elapsed
    conn = database.get_conn()
    cursor = conn.execute("""INSERT INTO game_records
        (game, room_code, players_json, results_json, snapshot_json, started_at, ended_at,
         has_bots, practice, challenge_date, participants_indexed, competition_indexed,
         rules_key, rules_label)
        VALUES (?, '123456', '[]', '[]', '{}', ?, ?, ?, ?, ?, 1, 1, ?, ?)""",
        (game, TODAY, ended or datetime.now(timezone.utc).isoformat(), int(bots),
         int(practice), challenge_date, key, label))
    record_id = cursor.lastrowid
    conn.execute("""INSERT INTO game_participants VALUES (?, ?, ?, 1, ?)""",
                 (record_id, uid, result, json.dumps(metrics)))
    conn.commit()
    conn.close()
    return record_id


def test_win_ties_are_shared_rank_and_stable_user_order():
    for uid in (2, 1):
        insert("gomoku", uid)
        insert("gomoku", uid)
    insert("gomoku", 3)
    board = get_leaderboard("gomoku", 3)
    assert [(item["user_id"], item["value"], item["rank"]) for item in board["items"]] == [
        (1, 2, 1), (2, 2, 1), (3, 1, 3),
    ]
    assert board["own"]["user_id"] == 3


def test_excludes_entire_bot_games_practice_other_games_and_banned_users():
    insert("gomoku", 1)
    insert("gomoku", 1, bots=True)
    insert("gomoku", 1, practice=True)
    insert("reversi", 1)
    insert("gomoku", 2)
    conn = database.get_conn()
    conn.execute("UPDATE users SET banned = 1 WHERE id = 2")
    conn.commit()
    conn.close()
    board = get_leaderboard("gomoku", 1)
    assert [(item["user_id"], item["value"]) for item in board["items"]] == [(1, 1)]


def test_best_score_is_per_user_and_own_is_available_outside_limit():
    record = insert("2048", 1, score=500)
    insert("2048", 1, score=100)
    insert("2048", 2, score=400)
    insert("2048", 3, score=300)
    board = get_leaderboard("2048", 3, metric="score", limit=1)
    assert len(board["items"]) == 1
    assert board["items"][0]["value"] == 500
    assert board["items"][0]["record_id"] == record
    assert board["own"]["rank"] == 3
    assert board["own"]["value"] == 300


def test_score_scopes_separate_start_levels_and_player_counts():
    insert("tetris", 1, score=100)
    insert("tetris", 2, score=10000, settings={"tetris_start_level": 10})
    insert("tetris", 3, score=9000, settings={"players": [None, None]})
    board = get_leaderboard("tetris", 1, metric="score")
    assert [item["user_id"] for item in board["items"]] == [1]
    assert "1人" in board["rules_label"]


def test_time_board_requires_natural_clear_and_separates_mine_difficulties():
    insert("minesweeper", 1, elapsed=3000)
    insert("minesweeper", 2, elapsed=1000, completed=False)
    insert("minesweeper", 3, elapsed=2000, settings={"difficulty": "beginner"})
    board = get_leaderboard("minesweeper", 1, metric="time")
    assert [(item["user_id"], item["value"]) for item in board["items"]] == [(1, 3000)]
    beginner = get_leaderboard("minesweeper", 3, metric="time", difficulty="beginner")
    assert beginner["items"][0]["value"] == 2000


def test_time_windows_use_utc_timestamps():
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    insert("gomoku", 1, ended=old)
    insert("gomoku", 2)
    assert len(get_leaderboard("gomoku", 1)["items"]) == 2
    for window in ("day", "week", "month"):
        assert [item["user_id"] for item in get_leaderboard("gomoku", 1, window=window)["items"]] == [2]


def test_daily_boards_filter_date_completion_and_regular_play():
    insert("2048", 1, score=100)
    insert("2048", 1, score=200, challenge_date=TODAY)
    insert("2048", 1, score=300, challenge_date="2026-11-03")
    insert("2048", 2, score=10000, challenge_date=TODAY, completed=False)
    board = get_leaderboard("2048", 1, metric="score", challenge_date=TODAY)
    assert [(item["user_id"], item["value"]) for item in board["items"]] == [(1, 200)]
    assert get_leaderboard("2048", 1, metric="score")["items"][0]["value"] == 100


def test_daily_streak_deduplicates_retries_and_ignores_surrender_and_bot_games():
    for day in ("2026-11-02", "2026-11-03", TODAY, TODAY):
        insert("2048", 1, score=200, challenge_date=day)
    insert("2048", 1, score=500, challenge_date=TODAY, completed=False)
    insert("2048", 2, score=900, challenge_date=TODAY, bots=True)
    mine = next(item for item in daily_challenges.get_daily_status(1)["games"] if item["game"] == "2048")
    assert mine["completed"] is True
    assert mine["streak"] == 3
    assert mine["best_value"] == 200
    assert all(not item["completed"] for item in daily_challenges.get_daily_status(2)["games"])


def test_streak_keeps_yesterday_until_today_is_played_then_expires():
    insert("2048", 1, score=100, challenge_date="2026-11-03")
    assert daily_challenges.get_daily_status(1)["games"][0]["streak"] == 1
    insert("tetris", 1, score=100, challenge_date="2026-11-02")
    assert daily_challenges.get_daily_status(1)["games"][1]["streak"] == 0


def test_challenge_seeds_are_stable_and_scoped_to_game_and_date():
    seed = challenge_seed("2048", TODAY)
    assert seed == challenge_seed("2048", TODAY)
    assert seed != challenge_seed("tetris", TODAY)
    assert seed != challenge_seed("2048", "2026-11-03")


@pytest.mark.parametrize("kwargs", [
    {"game": "unknown"}, {"metric": "' OR 1=1"}, {"metric": "time"},
    {"window": "year"}, {"challenge_date": "2026-1-2"},
    {"game": "minesweeper", "difficulty": "unknown"},
])
def test_invalid_board_queries_are_rejected(kwargs):
    args = {"game": "gomoku", **kwargs}
    with pytest.raises(ValueError):
        get_leaderboard(user_id=1, **args)


def test_legacy_unknown_rules_do_not_enter_standard_boards():
    conn = database.get_conn()
    cursor = conn.execute("""INSERT INTO game_records
        (game, room_code, players_json, results_json, snapshot_json, started_at, ended_at)
        VALUES ('tetris', '123456', '[{"username":"player1"}]',
                '[{"username":"player1","result":"win","score":9999}]',
                '{}', '2026-11-04', '2026-11-04')""")
    participants.backfill(conn)
    competition_records.create_schema(conn)
    conn.commit()
    conn.close()
    assert get_leaderboard("tetris", 1, metric="score")["items"] == []
    assert database.get_user_history(1)["total"] == 1


def test_observed_rule_scopes_can_be_selected_without_mixing_scores():
    insert('tetris', 1, score=100)
    insert('tetris', 2, score=10000, settings={'tetris_start_level': 10})
    insert('tetris', 3, score=9000, settings={'players': [None, None]})
    board = get_leaderboard('tetris', 1, metric='score')
    assert len(board['scopes']) == 3
    for scope in board['scopes']:
        selected = get_leaderboard('tetris', 1, metric='score', rules_key=scope['key'])
        assert len(selected['items']) == 1
        assert selected['rules_key'] == scope['key']
    with pytest.raises(ValueError, match='房规'):
        get_leaderboard('tetris', 1, rules_key="' OR 1=1")



def test_mine_selectable_scopes_remain_within_requested_difficulty():
    insert('minesweeper', 1, elapsed=1000, settings={'difficulty': 'beginner'})
    insert('minesweeper', 2, elapsed=2000, settings={'difficulty': 'advanced'})
    easy = get_leaderboard('minesweeper', 1, metric='time', difficulty='beginner')
    hard = get_leaderboard('minesweeper', 2, metric='time', difficulty='advanced')
    assert len(easy['scopes']) == len(hard['scopes']) == 1
    with pytest.raises(ValueError, match='房规'):
        get_leaderboard('minesweeper', 1, metric='time', difficulty='beginner', rules_key=hard['rules_key'])
