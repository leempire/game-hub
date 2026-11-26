"""Participant-authorized, revocable public links to sanitized final records."""

import json
import secrets

from app.database import get_conn, record_snapshot


# Public final-state fields only. Internal histories, RNG, credentials and live
# room membership never enter a share, even if future record builders add them.
_SNAPSHOT_FIELDS = {
    'game', 'board', 'board_size', 'rows', 'cols', 'width', 'height', 'mines',
    'winner', 'reason', 'moves', 'score', 'scores', 'ranking', 'revealed', 'flagged',
    'dots', 'h', 'v', 'boxes', 'players', 'num_players', 'pieces', 'boards',
    'max_tiles', 'lines', 'levels', 'garbage_received', 'player_colors',
    'placements', 'shots', 'phase', 'topology', 'mode', 'points', 'discard_top',
    'seat_uids', 'seat_teams', 'winner_team', 'emptied', 'rules', 'local_results',
    'active_board', 'last_move', 'revision', 'columns', 'stock_count', 'completed',
    'total_runs', 'deals', 'move_count', 'departed', 'map', 'clock_ms', 'entity',
    'rooms', 'result', 'difficulty', 'elapsed_ms', 'alive', 'finished',
}
_ABYSS_SUMMARY_FIELDS = {'game', 'seconds', 'map_size', 'level', 'kills', 'salvage', 'result', 'boss', 'boss_dead', 'players'}
_ABYSS_DIVER_FIELDS = {'id', 'name', 'kills', 'hp', 'out', 'weapons', 'passives', 'xp_collected', 'rescues', 'damage_dealt'}
_UNDERCOVER_SUMMARY_FIELDS = {'game', 'round', 'winner', 'reason'}
_GUANDAN_SUMMARY_FIELDS = {'game', 'players', 'levels', 'deal', 'winner', 'reason', 'deal_result'}
_PLAYER_FIELDS = {'user_id', 'username', 'nickname', 'seat', 'color', 'is_bot', 'ai_difficulty'}
_RESULT_FIELDS = {'username', 'result', 'rank', 'score', 'elapsed_ms', 'difficulty'}


def _ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS record_shares (
        token TEXT PRIMARY KEY,
        record_id INTEGER NOT NULL REFERENCES game_records(id) ON DELETE CASCADE,
        owner_id INTEGER NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0,
        UNIQUE(record_id, owner_id)
    )''')


def _authorized_record(conn, record_id: int, user_id: int):
    row = conn.execute('''SELECT r.* FROM game_records r
        JOIN game_participants p ON p.record_id = r.id
        WHERE r.id = ? AND p.user_id = ? AND r.practice = 0''',
        (record_id, user_id)).fetchone()
    if row is None:
        raise PermissionError('只能分享自己参与的正式对局')
    return row


def create_share(record_id: int, user_id: int) -> dict:
    """Repeated requests return the same active link; recreating rotates revoked tokens."""
    conn = get_conn()
    try:
        _ensure_schema(conn)
        _authorized_record(conn, record_id, user_id)
        old = conn.execute('SELECT token, revoked FROM record_shares WHERE record_id=? AND owner_id=?',
                           (record_id, user_id)).fetchone()
        if old and not old['revoked']:
            token = old['token']
        else:
            token = secrets.token_urlsafe(24)
            conn.execute('''INSERT INTO record_shares(token, record_id, owner_id) VALUES (?, ?, ?)
                ON CONFLICT(record_id, owner_id) DO UPDATE SET token=excluded.token, revoked=0''',
                (token, record_id, user_id))
        conn.commit()
        return {'token': token, 'path': f'/replay/{token}'}
    finally:
        conn.close()


def revoke_share(record_id: int, user_id: int) -> dict:
    conn = get_conn()
    try:
        _ensure_schema(conn)
        _authorized_record(conn, record_id, user_id)
        conn.execute('UPDATE record_shares SET revoked=1 WHERE record_id=? AND owner_id=?',
                     (record_id, user_id))
        conn.commit()
        return {'ok': True}
    finally:
        conn.close()


def _public_values(value):
    """Drop private state by key at every nesting depth, including newly added keys."""
    if isinstance(value, list):
        return [_public_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _public_values(item) for key, item in value.items()
                if not str(key).startswith('_') and key not in {
                    'hand', 'hands', 'draw_pile', 'stock', 'undo', 'undo_stack',
                    'password', 'password_hash', 'salt', 'token', 'jwt_secret', 'seed',
                }}
    return value


def public_share(token: str) -> dict | None:
    """Anonymous reads expose the final board, never the original room invitation."""
    if len(token) != 32:
        return None
    conn = get_conn()
    try:
        _ensure_schema(conn)
        row = conn.execute('''SELECT r.*, s.owner_id AS share_owner_id FROM record_shares s
            JOIN game_records r ON r.id = s.record_id
            WHERE s.token=? AND s.revoked=0 AND r.practice=0''', (token,)).fetchone()
        conn.commit()
        if row is None:
            return None
        snapshot = record_snapshot(row['snapshot_json'], row['share_owner_id'])
        fields = {'abyss': _ABYSS_SUMMARY_FIELDS, 'undercover': _UNDERCOVER_SUMMARY_FIELDS,
                  'guandan': _GUANDAN_SUMMARY_FIELDS}.get(row['game'], _SNAPSHOT_FIELDS)
        snapshot = _public_values({key: value for key, value in snapshot.items() if key in fields})
        if row['game'] == 'abyss':
            snapshot['players'] = [
                {key: value for key, value in player.items() if key in _ABYSS_DIVER_FIELDS}
                for player in snapshot.get('players', [])
            ]
        return {
            'id': row['id'], 'game': row['game'], 'room_code': '',
            'players': [{key: value for key, value in player.items() if key in _PLAYER_FIELDS}
                        for player in json.loads(row['players_json'])],
            # Individual outcomes paired with the winning faction expose roles.
            'results': [] if row['game'] == 'undercover' else [
                {key: value for key, value in result.items() if key in _RESULT_FIELDS}
                for result in json.loads(row['results_json'])],
            'snapshot': snapshot, 'started_at': row['started_at'], 'ended_at': row['ended_at'],
        }
    finally:
        conn.close()
