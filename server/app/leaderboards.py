"""Game-scoped public leaderboards over indexed participant records."""

from datetime import datetime, timedelta, timezone

from app.database import get_conn
from app.competition_rules import GAMES, default_room, rule_scope

_WINDOWS = {"all", "day", "week", "month"}
_METRICS = {"wins", "score", "time"}


def _cutoff(window: str) -> str | None:
    now = datetime.now(timezone.utc)
    if window == "all":
        return None
    if window == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return (now - timedelta(days=7 if window == "week" else 30)).isoformat()


def _validate(game, metric, window, difficulty, challenge_date):
    if game not in GAMES:
        raise ValueError("没有这个游戏")
    if metric not in _METRICS or window not in _WINDOWS:
        raise ValueError("不支持的榜单指标或时间范围")
    if metric == "score" and game not in ("2048", "tetris"):
        raise ValueError("这款游戏没有分数榜")
    if metric == "time" and game != "minesweeper":
        raise ValueError("目前仅扫雷支持最快通关榜")
    if game == "minesweeper" and difficulty not in ("beginner", "intermediate", "advanced"):
        raise ValueError("没有这个扫雷难度")
    if challenge_date:
        try:
            parsed = datetime.strptime(challenge_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("挑战日期应为 YYYY-MM-DD") from None
        if parsed.isoformat() != challenge_date:
            raise ValueError("挑战日期应为 YYYY-MM-DD")
        if game not in ("2048", "tetris", "minesweeper"):
            raise ValueError("这款游戏没有每日挑战")


def _query(game, metric, window, difficulty, challenge_date, limit, user_id, scope):
    rules_key, rules_label = scope
    conditions = ["r.game = ?", "r.practice = 0", "r.has_bots = 0", "p.user_id > 0",
                  "u.banned = 0", "r.rules_key = ?"]
    params = [game, rules_key]
    cutoff = _cutoff(window)
    if cutoff:
        conditions.append("r.ended_at >= ?")
        params.append(cutoff)
    if challenge_date:
        conditions.extend(["r.challenge_date = ?", "json_extract(p.metrics_json, '$.completed') = 1"])
        params.append(challenge_date)
    else:
        conditions.append("r.challenge_date IS NULL")
    if metric == "wins":
        conditions.append("p.result = 'win'")
        value = "1"
        aggregate = "COUNT(*)"
        direction = "DESC"
    else:
        key = "score" if metric == "score" else "elapsed_ms"
        value = f"json_extract(p.metrics_json, '$.{key}')"
        conditions.append(f"json_type(p.metrics_json, '$.{key}') IN ('integer', 'real')")
        conditions.append(f"{value} >= 0")
        aggregate = "MAX(metric_value)" if metric == "score" else "MIN(metric_value)"
        direction = "DESC" if metric == "score" else "ASC"
        if metric == "time":
            conditions.append("json_extract(p.metrics_json, '$.completed') = 1")
    sql = f"""
        WITH eligible AS (
            SELECT p.user_id, r.id AS record_id, {value} AS metric_value
            FROM game_participants p JOIN game_records r ON r.id = p.record_id
            JOIN users u ON u.id = p.user_id WHERE {' AND '.join(conditions)}
        ), grouped AS (
            SELECT user_id, {aggregate} AS value FROM eligible GROUP BY user_id
        ), ranked AS (
            SELECT g.*, u.username, u.nickname,
                   RANK() OVER (ORDER BY value {direction}) AS rank,
                   ROW_NUMBER() OVER (ORDER BY value {direction}, g.user_id ASC) AS position
            FROM grouped g JOIN users u ON u.id = g.user_id
        )
        SELECT *, (SELECT MIN(e.record_id) FROM eligible e
                   WHERE e.user_id = ranked.user_id AND e.metric_value = ranked.value) AS record_id
        FROM ranked WHERE position <= ? OR user_id = ? ORDER BY position
    """
    return sql, [*params, limit, user_id], rules_label


def get_leaderboard(game: str, user_id: int, *, metric="wins", window="all",
                    difficulty="intermediate", challenge_date=None, limit=20, rules_key=None) -> dict:
    _validate(game, metric, window, difficulty, challenge_date)
    if game == "minesweeper" and challenge_date:
        difficulty = "beginner"
    limit = max(1, min(int(limit), 100))
    default = rule_scope(default_room(game, difficulty, challenge=bool(challenge_date)))
    conn = get_conn()
    try:
        scopes = {default[0]: default[1]}
        if not challenge_date:
            for row in conn.execute("""SELECT DISTINCT r.rules_key, r.rules_label FROM game_records r
                    JOIN game_participants p ON p.record_id=r.id JOIN users u ON u.id=p.user_id
                    WHERE r.game=? AND r.practice=0 AND r.has_bots=0 AND r.challenge_date IS NULL
                    AND r.rules_key IS NOT NULL AND p.user_id>0 AND u.banned=0
                    AND (r.game <> 'minesweeper' OR json_extract(p.metrics_json, '$.difficulty') = ?)
                    ORDER BY r.rules_label, r.rules_key""", (game, difficulty)):
                scopes[row["rules_key"]] = row["rules_label"]
        if rules_key is not None and rules_key not in scopes:
            raise ValueError("没有这份房规的榜单")
        selected = rules_key or default[0]
        sql, params, rules_label = _query(game, metric, window, difficulty, challenge_date,
                                          limit, user_id, (selected, scopes[selected]))
        rows = [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()
    items, own = [], None
    for row in rows:
        position = row.pop("position")
        if metric == "wins":
            row["record_id"] = None
        if row["user_id"] == user_id:
            own = row
        if position <= limit:
            items.append(row)
    return {"game": game, "metric": metric, "window": window,
            "challenge_date": challenge_date, "difficulty": difficulty if game == "minesweeper" else None,
            "rules_label": rules_label, "rules_key": selected,
            "scopes": [{"key": key, "label": label} for key, label in scopes.items()],
            "items": items, "own": own}
