"""SQLite database initialization and helper functions."""

import json
import sqlite3
import os
from datetime import datetime, timezone


_db_path: str = "data/gamehub.db"


def init_db(db_path: str):
    """Initialize database path and create tables + seed admin."""
    global _db_path
    _db_path = db_path
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = get_conn()
    try:
        _create_tables(conn)
        conn.commit()
    finally:
        conn.close()


def get_conn() -> sqlite3.Connection:
    """Get a new SQLite connection with row_factory set."""
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _create_tables(conn: sqlite3.Connection):
    """Create users and game_records tables if not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            nickname TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            banned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game TEXT NOT NULL,
            room_code TEXT NOT NULL,
            players_json TEXT NOT NULL,
            results_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_records_players
        ON game_records(players_json)
    """)


def seed_admin(username: str, password_hash: str, salt: str):
    """Create admin user if not exists."""
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not existing:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO users (username, password_hash, salt, role, created_at)
                   VALUES (?, ?, ?, 'admin', ?)""",
                (username, password_hash, salt, now),
            )
            conn.commit()
    finally:
        conn.close()


def set_password(username: str, password_hash: str, salt: str) -> bool:
    """按用户名直接改密码（不校验旧密码），返回该用户是否存在。

    只给启动时的 `ADMIN_PASSWORD` 用：种子逻辑是「不存在才建」，于是线上那个用
    默认口令建出来的 admin 永远改不掉 —— 显式给了环境变量就说明意图是「以它为准」。
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
            (password_hash, salt, username),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def row_to_user(row: sqlite3.Row) -> dict:
    """Convert a database row to a user dict."""
    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"],
        "role": row["role"],
        "banned": bool(row["banned"]),
        "created_at": row["created_at"],
    }


def save_game_record(room: dict):
    """Persist a finished game to the game_records table.

    Safe to call multiple times for the same room — records are keyed on
    room_code + ended_at, so a duplicate insert is simply a no-op via
    INSERT OR IGNORE.
    """
    # 房间状态只有 waiting/playing，对局结束后回到 waiting，所以不能再用
    # 房间状态判断「是否打完了一局」——改看对局态本身：五子棋/翻转棋/点格棋/
    # 海战棋/2048 落 winner，扫雷/飞行棋落 finished。两者都没有说明还在进行中。
    gs = room.get("game_state")
    if not gs or not (gs.get("winner") or gs.get("finished")):
        return
    players = _build_players_record(room)
    results = _build_results_record(room)
    snapshot = _build_snapshot(room)
    started_at = room.get("game_started_at", room.get("created_at", _now_iso()))
    ended_at = _now_iso()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO game_records
               (game, room_code, players_json, results_json,
                snapshot_json, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                room["game"],
                room["code"],
                json.dumps(players, ensure_ascii=False),
                json.dumps(results, ensure_ascii=False),
                json.dumps(snapshot, ensure_ascii=False),
                started_at,
                ended_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_history(user_id: int, page: int = 1, size: int = 20) -> dict:
    """Return paginated history records for a user.

    A record belongs to a user when the user's username appears in
    players_json. We scan with LIKE '%username%' (room sizes are tiny so
    this is acceptable).
    """
    conn = get_conn()
    try:
        user_row = conn.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user_row:
            return {"items": [], "total": 0, "page": page, "size": size}
        username = user_row["username"]
        like = f'%"{username}"%'
        total = conn.execute(
            "SELECT COUNT(*) FROM game_records WHERE players_json LIKE ?",
            (like,),
        ).fetchone()[0]
        offset = (page - 1) * size
        rows = conn.execute(
            """SELECT id, game, room_code, players_json, results_json,
                      snapshot_json, started_at, ended_at
               FROM game_records
               WHERE players_json LIKE ?
               ORDER BY id DESC
               LIMIT ? OFFSET ?""",
            (like, size, offset),
        ).fetchall()
        items = [_row_to_record(r) for r in rows]
        return {"items": items, "total": total, "page": page, "size": size}
    finally:
        conn.close()


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "game": row["game"],
        "room_code": row["room_code"],
        "players": json.loads(row["players_json"]),
        "results": json.loads(row["results_json"]),
        "snapshot": json.loads(row["snapshot_json"]),
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


def _build_players_record(room: dict) -> list[dict]:
    """Build the players list stored with each record."""
    gs = room.get("game_state") or {}
    colors = gs.get("player_colors", {})
    players = []
    for idx, p in enumerate(room["players"]):
        entry: dict = {
            "username": p["username"],
            "nickname": p.get("nickname"),
            "seat": idx,
        }
        color = colors.get(p["id"])
        if color:
            entry["color"] = color
        players.append(entry)
    return players


def _build_results_record(room: dict) -> list[dict]:
    """Build the per-player result list."""
    gs = room.get("game_state") or {}
    winner = gs.get("winner")
    reason = gs.get("reason")
    colors = gs.get("player_colors", {})
    results = []
    if room["game"] == "minesweeper":
        ranking = room.get("_mine_ranking", [])
        if not ranking:
            # Fallback: compute ranking if not cached
            from app.minesweeper import compute_ranking
            players_info = [
                {"seat": idx, "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = compute_ranking(gs, players_info)
        # Rank 1 = "win", others = "lose"; ties at rank 1 all "win"
        rank_map = {e["username"]: e["rank"] for e in ranking}
        for p in room["players"]:
            r = "win" if rank_map.get(p["username"], 999) == 1 else "lose"
            results.append({"username": p["username"], "result": r})
        return results
    if room["game"] == "ludo":
        ranking = room.get("_ludo_ranking", [])
        if not ranking:
            from app.ludo import compute_ranking
            players_info = [
                {"seat": idx, "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = compute_ranking(gs, players_info)
        rank_map = {e["username"]: e["rank"] for e in ranking}
        for p in room["players"]:
            r = "win" if rank_map.get(p["username"], 999) == 1 else "lose"
            results.append({"username": p["username"], "result": r})
        return results
    if room["game"] == "uno":
        # 单局定胜负：出完手牌的那个人是胜者，其余按剩余手牌点数排名
        ranking = room.get("_uno_ranking", [])
        if not ranking:
            from app.uno import compute_ranking
            players_info = [
                {"seat": idx, "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = compute_ranking(gs, players_info)
        rank_map = {e["username"]: e["rank"] for e in ranking}
        for p in room["players"]:
            r = "win" if rank_map.get(p["username"], 999) == 1 else "lose"
            results.append({"username": p["username"], "result": r})
        return results
    if room["game"] == "2048":
        # 2048 是「各玩各的」跑分竞速，不是胜负对抗：**只有真的合出 2048 才算胜**，
        # 其余（分数最高但没合出来 / 卡死 / 认输）一律记「游戏结束」。
        # 不能沿用下面的 winner==color 判断——2048 的 gs["winner"] 记的是排名第一的
        # 颜色（见 _maybe_finish_2048），直接套会把「分高者」误标成胜方。
        from app.game2048 import WIN_TILE, max_tile
        for p in room["players"]:
            board = gs.get("boards", {}).get(p["id"], [])
            reached = max_tile(board) >= WIN_TILE
            results.append({
                "username": p["username"],
                "result": "win" if reached else "end",
            })
        return results
    # gomoku / reversi
    for p in room["players"]:
        color = colors.get(p["id"], "")
        if winner == "draw":
            r = "draw"
        elif winner == color:
            r = "win"
        else:
            r = "lose"
        results.append({"username": p["username"], "result": r})
    return results


def _build_snapshot(room: dict) -> dict:
    """Build the final-position snapshot stored with each record."""
    gs = room.get("game_state") or {}
    game = room["game"]
    if game == "gomoku":
        return {
            "game": "gomoku",
            "board_size": 15,
            "board": gs.get("board", []),
            "winner": gs.get("winner"),
            "reason": gs.get("reason"),
        }
    if game == "reversi":
        return {
            "game": "reversi",
            "board_size": 8,
            "board": gs.get("board", []),
            "winner": gs.get("winner"),
            "reason": gs.get("reason"),
        }
    if game == "minesweeper":
        from app.minesweeper import (
            get_revealed_list,
            get_flagged_list,
            compute_ranking,
        )
        ranking = room.get("_mine_ranking")
        if not ranking:
            players_info = [
                {"seat": idx, "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = compute_ranking(gs, players_info)
        return {
            "game": "minesweeper",
            "width": gs.get("width", 0),
            "height": gs.get("height", 0),
            "mines": gs.get("mines", 0),
            "revealed": get_revealed_list(gs),
            "flagged": get_flagged_list(gs),
            "ranking": ranking,
            "reason": gs.get("reason"),
        }
    if game == "battleship":
        from app.battleship import snapshot as bs_snapshot
        return bs_snapshot(gs)
    if game == "dots":
        from app.dots import snapshot as dots_snapshot
        return dots_snapshot(gs)
    if game == "xiangqi":
        from app.xiangqi import snapshot as xiangqi_snapshot
        return xiangqi_snapshot(gs)
    if game == "luzhanqi":
        # 快照是**全量真相**（含暗棋下被扣着的棋子）—— 对局已经结束，全量更有复盘价值。
        # 漏了这个分支不会报错，只会让战绩页点开一片空白（深空休眠踩过）。
        from app.luzhanqi import snapshot as luzhanqi_snapshot
        return luzhanqi_snapshot(gs)
    if game == "tetris":
        from app.tetris import snapshot as tetris_snapshot
        snap = tetris_snapshot(gs)
        # 与 2048 同理：跑分类游戏的快照要带 uid→身份，战绩页才能显示用户名
        snap["players"] = {
            str(p["id"]): {
                "username": p["username"],
                "nickname": p.get("nickname"),
                "is_bot": p.get("is_bot", False),
                "color": gs.get("player_colors", {}).get(p["id"], ""),
            }
            for p in room["players"]
        }
        return snap
    if game == "connect4":
        from app.connect4 import COLS, ROWS
        return {
            "game": "connect4",
            "cols": COLS,
            "rows": ROWS,
            "board": gs.get("board", []),
            "winner": gs.get("winner"),
            "reason": gs.get("reason"),
        }
    if game == "ludo":
        from app.ludo import compute_ranking
        ranking = room.get("_ludo_ranking")
        if not ranking:
            players_info = [
                {"seat": idx, "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = compute_ranking(gs, players_info)
        players_snap = {}
        for seat, p in gs["players"].items():
            players_snap[str(seat)] = {
                "pieces": list(p["pieces"]),
                "color": p["color"],
                "finished_count": p["finished_count"],
                "finished": p["finished"],
            }
        return {
            "game": "ludo",
            "num_players": gs.get("num_players", 0),
            "players": players_snap,
            "ranking": ranking,
            "reason": gs.get("reason"),
        }
    if game == "uno":
        from app.uno import snapshot as uno_snapshot, compute_ranking as uno_rank
        snap = uno_snapshot(gs)
        ranking = room.get("_uno_ranking")
        if not ranking:
            players_info = [
                {"seat": idx, "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = uno_rank(gs, players_info)
        # 排名里已经带了用户名/点数/剩余张数，战绩页照着渲染即可
        snap["ranking"] = ranking
        return snap
    if game == "2048":
        from app.game2048 import snapshot as g2048_snapshot
        snap = g2048_snapshot(gs)
        # 战绩页要显示用户名：2048 是多人跑分，颜色既不是身份也不是名次，
        # 光有 uid→盘面 的映射没法渲染「谁是谁」。这里把身份一起存进快照。
        snap["players"] = {
            str(p["id"]): {
                "username": p["username"],
                "nickname": p.get("nickname"),
                "is_bot": p.get("is_bot", False),
                "color": gs.get("player_colors", {}).get(p["id"], ""),
            }
            for p in room["players"]
        }
        return snap
    if game == "stasis":
        from app.stasis import snapshot as stasis_snapshot
        snap = stasis_snapshot(gs)
        # 排名（含用户名）由结算时算好存在房间上；没有就现算一份，保证快照自洽。
        ranking = room.get("_stasis_ranking")
        if not ranking:
            from app.stasis import compute_ranking as stasis_rank
            players_info = [
                {"seat": idx, "uid": p["id"], "username": p["username"],
                 "nickname": p.get("nickname")}
                for idx, p in enumerate(room["players"])
            ]
            ranking = stasis_rank(gs, players_info)
        snap["ranking"] = ranking
        return snap
    return {"game": game}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
