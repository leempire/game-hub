"""Public final replays require participant consent, opaque links and revocation."""
import json

import pytest
from fastapi.testclient import TestClient

from app import database, participants, sharing
from app.main import app
from app.security import get_current_user_id


@pytest.fixture
def share_db(tmp_path):
    old = database._db_path
    database.init_db(str(tmp_path / 'shares.db'))
    conn = database.get_conn()
    conn.execute("INSERT INTO users(id,username,password_hash,salt,created_at) VALUES (1,'alice','h','s','2026-01-01')")
    snapshot = {'game': 'uno', 'winner': 0, 'ranking': [{'seat': 0, 'rank': 1}],
                'hands': {'0': ['R1'], '1': ['W+4']}, 'draw_pile': ['G7'],
                'players': {'1': {'nickname': 'alice', '_rng': 'internal'}},
                'seat_uids': [1, 2], 'token': 'never-public', '_history': [1, 2]}
    cursor = conn.execute('''INSERT INTO game_records
        (game,room_code,players_json,results_json,snapshot_json,started_at,ended_at)
        VALUES ('uno','SECRET', ?, ?, ?, '2026-01-01','2026-01-02')''',
        (json.dumps([{'username': 'alice', 'user_id': 1, 'seat': 0, 'token': 'private'}]),
         json.dumps([{'username': 'alice', 'result': 'win'}]), json.dumps(snapshot)))
    record_id = cursor.lastrowid
    participants.backfill(conn)
    conn.commit()
    conn.close()
    yield record_id
    database._db_path = old


def test_share_requires_exact_participation_and_excludes_practice(share_db):
    with pytest.raises(PermissionError):
        sharing.create_share(share_db, 2)
    with pytest.raises(PermissionError):
        sharing.create_share(share_db + 1, 1)
    conn = database.get_conn()
    conn.execute('UPDATE game_records SET practice=1 WHERE id=?', (share_db,))
    conn.commit()
    conn.close()
    with pytest.raises(PermissionError):
        sharing.create_share(share_db, 1)


def test_public_snapshot_sanitized_idempotent_revocable_and_rotated(share_db):
    first = sharing.create_share(share_db, 1)
    assert first == sharing.create_share(share_db, 1)
    assert len(first['token']) == 32 and first['path'].endswith(first['token'])
    record = sharing.public_share(first['token'])
    assert record['game'] == 'uno' and record['snapshot']['winner'] == 0
    assert record['snapshot']['ranking'] == [{'seat': 0, 'rank': 1}]
    assert record['room_code'] == ''
    encoded = json.dumps(record)
    for private in ('SECRET', 'R1', 'W+4', 'G7', 'never-public', 'private', '_history', '_rng'):
        assert private not in encoded
    assert sharing.public_share(str(share_db)) is None
    sharing.revoke_share(share_db, 1)
    assert sharing.public_share(first['token']) is None
    second = sharing.create_share(share_db, 1)
    assert first['token'] != second['token']
    assert sharing.public_share(first['token']) is None
    assert sharing.public_share(second['token']) is not None


def test_anonymous_http_read_and_authenticated_create_revoke(share_db):
    client = TestClient(app)
    assert client.post(f'/api/history/{share_db}/share').status_code == 401
    app.dependency_overrides[get_current_user_id] = lambda: 1
    try:
        created = client.post(f'/api/history/{share_db}/share')
        assert created.status_code == 200
        token = created.json()['token']
    finally:
        app.dependency_overrides.pop(get_current_user_id)
    assert client.get(f'/api/history/shared/{token}').status_code == 200
    assert client.get('/api/history/shared/invalid').status_code == 404
    app.dependency_overrides[get_current_user_id] = lambda: 2
    try:
        assert client.delete(f'/api/history/{share_db}/share').status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user_id)
