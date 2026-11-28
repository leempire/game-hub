"""Delete explicitly marked test accounts transactionally; never infer by name."""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone


def selected_users(conn, batch=None):
    sql = 'SELECT id,username FROM users WHERE temporary_test=1'
    args = ()
    if batch is not None:
        sql += ' AND test_batch=?'
        args = (batch,)
    return conn.execute(sql, args).fetchall()


def _prepare(conn, ids):
    conn.execute('CREATE TEMP TABLE cleanup_users(id INTEGER PRIMARY KEY)')
    conn.executemany('INSERT INTO cleanup_users VALUES (?)', [(uid,) for uid in ids])
    # A test administrator's changes to a real account remain meaningful audit data.
    conflict = conn.execute('''SELECT count(*) FROM ai_credit_adjustments
        WHERE admin_id IN (SELECT id FROM cleanup_users)
        AND user_id NOT IN (SELECT id FROM cleanup_users)''').fetchone()[0]
    if conflict:
        raise ValueError('测试管理员曾调整真实玩家积分，请先人工核对审计记录')
    room_codes = []
    has_rooms = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rooms'").fetchone()
    for code, raw in conn.execute('SELECT code,data FROM rooms') if has_rooms else []:
        room = json.loads(raw)
        humans = {p['id'] for p in room.get('players', []) if not p.get('is_bot')}
        if humans & ids:
            if not humans <= ids:
                raise ValueError('测试玩家仍在真实玩家房间中，请先让测试玩家退出')
            room_codes.append(code)
    return room_codes


def cleanup(db_path, backup_dir, batch=None):
    """Caller must stop the backend first; backup precedes any mutation."""
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        ids = {row[0] for row in selected_users(conn, batch)}
        if not ids:
            return {'deleted_users': 0, 'backup': None}
        folder = Path(backup_dir)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        backup = folder / f'gamehub-before-test-cleanup-{stamp}.db'
        backup.touch(mode=0o600, exist_ok=False)
        with sqlite3.connect(backup) as target:
            conn.backup(target)
        conn.execute('BEGIN IMMEDIATE')
        codes = _prepare(conn, ids)
        # Wholly test records can go; mixed records stay available to real players.
        record_ids = []
        for rid, raw in conn.execute('SELECT id,players_json FROM game_records'):
            players = json.loads(raw)
            humans = {p['user_id'] for p in players if p.get('user_id', 0) > 0 and not p.get('is_bot')}
            if humans and humans <= ids:
                record_ids.append((rid,))
        if codes:
            conn.executemany('DELETE FROM rooms WHERE code=?', [(code,) for code in codes])
        conn.execute('DELETE FROM ai_usage WHERE user_id IN (SELECT id FROM cleanup_users)')
        conn.execute('''DELETE FROM ai_credit_adjustments
            WHERE user_id IN (SELECT id FROM cleanup_users)''')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_shares'").fetchone():
            conn.execute('DELETE FROM record_shares WHERE owner_id IN (SELECT id FROM cleanup_users)')
        conn.execute('DELETE FROM game_participants WHERE user_id IN (SELECT id FROM cleanup_users)')
        conn.executemany('DELETE FROM game_records WHERE id=?', record_ids)
        conn.execute('DELETE FROM users WHERE id IN (SELECT id FROM cleanup_users)')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('清理后发现外键异常，已回滚')
        conn.commit()
        return {'deleted_users': len(ids), 'deleted_records': len(record_ids),
                'deleted_rooms': len(codes), 'backup': str(backup)}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
