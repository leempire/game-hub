"""Public-score rule scopes and natural-completion metadata on battle records."""

import json

from app.competition_rules import rule_scope


_COLUMNS = {"rules_key": "TEXT", "rules_label": "TEXT", "competition_indexed": "INTEGER NOT NULL DEFAULT 0"}


def create_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(game_records)")}
    for name, definition in _COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE game_records ADD COLUMN {name} {definition}")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_records_rules
                    ON game_records(game, rules_key, practice, has_bots, ended_at, id)""")
    # Legacy scores with unknown rule settings remain in personal history, but
    # cannot silently enter a public board that claims a known standard scope.
    rows = conn.execute("""SELECT id, game, room_code, players_json, results_json, snapshot_json
                         FROM game_records WHERE competition_indexed = 0""").fetchall()
    for row in rows:
        try:
            players = json.loads(row["players_json"])
            snap = json.loads(row["snapshot_json"])
        except (TypeError, ValueError):
            players, snap = [], {}
        if not isinstance(players, list) or not isinstance(snap, dict):
            players, snap = [], {}
        # These legacy games had fixed rules; use actual dimensions to verify.
        room = _legacy_room(row["game"], players, snap)
        if room is not None:
            key, label = rule_scope(room)
            conn.execute("UPDATE game_records SET rules_key = ?, rules_label = ? WHERE id = ?",
                         (key, label, row["id"]))
        _enrich_legacy_completion(conn, row["id"], row["game"], snap)
        conn.execute("UPDATE game_records SET competition_indexed = 1 WHERE id = ?", (row["id"],))


def _legacy_room(game, players, snapshot):
    if not players or not isinstance(snapshot, dict):
        return None
    if game == "2048":
        return {"game": game, "players": players}
    if game == "gomoku" and snapshot.get("board_size") == 15:
        return {"game": game, "players": players}
    if game == "reversi" and snapshot.get("board_size") == 8:
        return {"game": game, "players": players}
    if game == "connect4" and snapshot.get("cols") == 7 and snapshot.get("rows") == 6:
        return {"game": game, "players": players}
    if game in ("xiangqi", "uttt") and snapshot.get("game") == game:
        return {"game": game, "players": players}
    return None


def _enrich_legacy_completion(conn, record_id, game, snapshot):
    rows = conn.execute("SELECT user_id, metrics_json FROM game_participants WHERE record_id = ?",
                        (record_id,)).fetchall()
    ranking = snapshot.get("ranking") or []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        completed = game == "minesweeper" and snapshot.get("reason") == "cleared"
        for entry in ranking:
            if isinstance(entry, dict) and entry.get("user_id", entry.get("uid")) == row["user_id"]:
                reason = entry.get("finish_reason")
                metrics["finish_reason"] = reason
                if game == "2048":
                    completed = reason in ("reach_2048", "stuck")
                elif game == "tetris":
                    completed = reason == "top_out"
        metrics["completed"] = bool(completed)
        conn.execute("UPDATE game_participants SET metrics_json = ? WHERE record_id = ? AND user_id = ?",
                     (json.dumps(metrics, ensure_ascii=False), record_id, row["user_id"]))


def enrich_record(conn, record_id: int, room: dict):
    """Called inside save_game_record's transaction after participant insertion."""
    key, label = rule_scope(room)
    conn.execute("""UPDATE game_records SET rules_key = ?, rules_label = ?, competition_indexed = 1
                    WHERE id = ?""", (key, label, record_id))
    gs, game = room["game_state"], room["game"]
    for seat, player in enumerate(room["players"]):
        uid = player["id"]
        row = conn.execute("""SELECT metrics_json FROM game_participants
                            WHERE record_id = ? AND user_id = ?""", (record_id, uid)).fetchone()
        if row is None:
            continue
        metrics = json.loads(row["metrics_json"])
        reason = gs.get("finish_reasons", {}).get(uid)
        if game == "minesweeper":
            metrics["completed"] = (gs.get("reason") == "cleared"
                                    and bool(gs.get("players", {}).get(seat, {}).get("alive")))
        elif game == "2048":
            metrics["completed"] = reason in ("reach_2048", "stuck")
        elif game == "tetris":
            metrics["completed"] = reason == "top_out"
            metrics["start_level"] = gs.get("start_level", 1)
        if reason is not None:
            metrics["finish_reason"] = reason
        conn.execute("UPDATE game_participants SET metrics_json = ? WHERE record_id = ? AND user_id = ?",
                     (json.dumps(metrics, ensure_ascii=False), record_id, uid))
