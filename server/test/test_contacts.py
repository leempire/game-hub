"""Real HTTP consent, privacy, durable cursors and retry-safety tests."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest
from app import database, security, social_schema


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, '_db_path', str(tmp_path / 'contacts.db'))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, '_jwt_secret', 'contacts-test-key-that-is-at-least-32-bytes')
    return ws_client


def user(client, name):
    response = client.post('/api/auth/register', json={'username': name, 'password': 'test123456'})
    assert response.status_code == 200, response.text
    data = response.json()
    return {'id': data['user']['id'], 'username': name,
            'headers': {'Authorization': 'Bearer ' + data['token']}}


def people(client):
    return [user(client, name) for name in ('contact_alice', 'contact_bob', 'contact_carol')]


def req(client, actor, peer):
    return client.post('/api/social/requests', headers=actor['headers'], json={'user_id': peer['id']})


def respond(client, actor, peer, action):
    return client.post(f"/api/social/requests/{peer['id']}/{action}", headers=actor['headers'])


def link(client, actor, peer):
    assert req(client, actor, peer).status_code == 200
    assert respond(client, peer, actor, 'accept').status_code == 200


def send(client, actor, peer, n=0, body='你好'):
    return client.post(f"/api/social/chats/{peer['id']}", headers={**actor['headers'], 'Content-Type': 'application/json'},
                       content=json.dumps({'body': body, 'client_id': f'message_{n:08d}'}))


def history(client, actor, peer, **params):
    return client.get(f"/api/social/chats/{peer['id']}", headers=actor['headers'], params=params)


def inbox(client, actor):
    response = client.get('/api/social', headers=actor['headers'])
    assert response.status_code == 200, response.text
    return response.json()


def sql(statement, params=()):
    conn = database.get_conn()
    try:
        value = conn.execute(statement, params).fetchall()
        conn.commit()
        return value
    finally:
        conn.close()


def age_messages():
    sql("UPDATE direct_messages SET created_at='2000-01-01T00:00:00.000+00:00'")


def test_search_public_exact_and_hidden_states(client):
    a, b, c = people(client)
    response = client.get('/api/social/users', params={'q': b['username']}, headers=a['headers'])
    assert response.json()['items'] == [{'id': b['id'], 'username': b['username'], 'nickname': None, 'relation': 'none'}]
    for text in (a['username'], 'contact_', "' OR 1=1 --"):
        assert not client.get('/api/social/users', params={'q': text}, headers=a['headers']).json()['items']
    client.post(f"/api/social/blocks/{b['id']}", headers=a['headers'])
    for actor, peer in ((a, b), (b, a)):
        assert not client.get('/api/social/users', params={'q': peer['username']}, headers=actor['headers']).json()['items']
    sql('UPDATE users SET banned=1 WHERE id=?', (c['id'],))
    assert not client.get('/api/social/users', params={'q': c['username']}, headers=a['headers']).json()['items']
    assert client.get('/api/social').status_code in (401, 403)


def test_consent_idempotence_and_request_roles(client):
    a, b, c = people(client)
    assert req(client, a, a).status_code == 400
    assert req(client, a, b).json() == req(client, a, b).json() == {'status': 'pending'}
    assert len(inbox(client, a)['outgoing']) == len(inbox(client, b)['incoming']) == 1
    assert req(client, b, a).status_code == 409
    for actor, peer, action in ((a, b, 'accept'), (c, a, 'accept'), (b, a, 'cancel')):
        assert respond(client, actor, peer, action).status_code == 404
    assert send(client, a, b).status_code == 403
    assert respond(client, b, a, 'accept').status_code == 200
    assert respond(client, b, a, 'accept').status_code == 200
    assert req(client, a, b).json() == {'status': 'accepted'}
    assert len(inbox(client, a)['friends']) == len(inbox(client, b)['friends']) == 1
    assert not inbox(client, a)['incoming'] and not inbox(client, a)['outgoing']


@pytest.mark.parametrize('action,index', [('decline', 1), ('cancel', 0)])
def test_request_end_and_cooldown(client, action, index):
    a, b, _ = people(client)
    assert req(client, a, b).status_code == 200
    actor, peer = (a, b) if index == 0 else (b, a)
    assert respond(client, actor, peer, action).status_code == 200
    assert respond(client, actor, peer, action).status_code == 200
    assert not inbox(client, a)['outgoing'] and not inbox(client, b)['incoming']
    assert req(client, a, b).status_code == 429
    sql("UPDATE friend_links SET updated_at='2000-01-01T00:00:00.000+00:00'")
    assert req(client, b, a).status_code == 200
    assert inbox(client, a)['incoming'][0]['id'] == b['id']


def test_outsider_cannot_access_chat_or_read_receipts(client):
    a, b, c = people(client)
    link(client, a, b)
    secret = send(client, a, b).json()
    for peer in (a, b):
        assert history(client, c, peer).status_code == 403
        assert send(client, c, peer).status_code == 403
        assert client.post(f"/api/social/chats/{peer['id']}/read", headers=c['headers'], json={'through_id': secret['id']}).status_code == 403
    assert not inbox(client, c)['friends']
    received = history(client, b, a).json()
    assert received['items'][0]['client_id'] is None
    assert set(received['peer']) == {'id', 'username', 'nickname'}


def test_unread_only_seen_monotonic_and_receipt(client):
    a, b, _ = people(client)
    link(client, a, b)
    first = send(client, a, b, 1).json()
    assert inbox(client, b)['unread_total'] == 1
    loaded = history(client, b, a).json()['items']
    second = send(client, a, b, 2).json()
    response = client.post(f"/api/social/chats/{a['id']}/read", headers=b['headers'], json={'through_id': loaded[-1]['id']})
    assert response.json()['through_id'] == first['id']
    assert inbox(client, b)['unread_total'] == 1
    assert history(client, a, b).json()['peer_read_through'] == first['id']
    client.post(f"/api/social/chats/{a['id']}/read", headers=b['headers'], json={'through_id': 0})
    assert inbox(client, b)['unread_total'] == 1
    assert inbox(client, b)['friends'][0]['last_message']['id'] == second['id']
    client.post(f"/api/social/chats/{a['id']}/read", headers=b['headers'], json={'through_id': second['id']})
    assert inbox(client, b)['unread_total'] == inbox(client, a)['unread_total'] == 0


def test_cursor_pagination_has_no_gaps_or_cross_conversation_leaks(client):
    a, b, c = people(client)
    link(client, a, b)
    link(client, a, c)
    ids = []
    for n in range(13):
        age_messages()
        ids.append(send(client, a, b, n).json()['id'])
        if n == 5:
            assert send(client, a, c, 100, 'other').status_code == 200
    latest = history(client, a, b, limit=5).json()
    assert [m['id'] for m in latest['items']] == ids[-5:] and latest['has_more']
    older = history(client, a, b, before=ids[-5], limit=5).json()
    assert [m['id'] for m in older['items']] == ids[-10:-5] and older['has_more']
    oldest = history(client, a, b, before=ids[-10], limit=5).json()
    assert [m['id'] for m in oldest['items']] == ids[:3] and not oldest['has_more']
    found, cursor = [], 0
    while True:
        page = history(client, a, b, after=cursor, limit=4).json()
        found.extend(m['id'] for m in page['items'])
        if not page['has_more']:
            break
        cursor = page['items'][-1]['id']
    assert found == ids and len(set(found)) == 13
    assert history(client, a, b, after=0, before=1).status_code == 400


def test_send_idempotence_payload_conflict_and_sender_scoping(client):
    a, b, c = people(client)
    link(client, a, b)
    link(client, a, c)
    first = send(client, a, b).json()
    assert send(client, a, b).json() == first
    assert send(client, a, b, body='changed').status_code == 409
    assert send(client, a, c).status_code == 409
    assert send(client, b, a).status_code == 200
    assert len(history(client, a, b).json()['items']) == 2


def test_rate_limit_exempts_idempotent_retries(client):
    a, b, _ = people(client)
    link(client, a, b)
    for n in range(10):
        assert send(client, a, b, n).status_code == 200
    assert send(client, a, b, 11).status_code == 429
    assert send(client, a, b, 0).status_code == 200
    age_messages()
    assert send(client, a, b, 11).status_code == 200


@pytest.mark.parametrize('text,status', [('', 422), ('   ', 400), ('x'*1001, 422), ('bad\x00text', 400),
                                        ('bad\x7ftext', 400), ('bad\x85text', 400), ('\ud800', 400)])
def test_invalid_text_never_becomes_server_error(client, text, status):
    a, b, _ = people(client)
    link(client, a, b)
    assert send(client, a, b, body=text).status_code == status
    assert not history(client, a, b).json()['items']


def test_strict_payload_and_cursor_validation(client):
    a, b, _ = people(client)
    link(client, a, b)
    for body in ({'body': 'ok', 'client_id': 'short'}, {'body': ['text'], 'client_id': 'valid_id'},
                 {'body': 'text', 'client_id': 'valid_id', 'sender_id': b['id']}):
        assert client.post(f"/api/social/chats/{b['id']}", headers=a['headers'], json=body).status_code == 422
    assert client.post('/api/social/requests', headers=a['headers'], json={'user_id': True}).status_code == 422
    assert client.post(f"/api/social/chats/{b['id']}/read", headers=a['headers'], json={'through_id': True}).status_code == 422
    for params in ({'after': -1}, {'before': 0}, {'after': 2**63}, {'limit': 101}):
        assert history(client, a, b, **params).status_code == 422


def test_remove_block_both_directions_and_unblock_not_restore(client):
    a, b, _ = people(client)
    link(client, a, b)
    assert send(client, a, b).status_code == 200
    assert client.delete(f"/api/social/friends/{b['id']}", headers=a['headers']).status_code == 200
    assert not inbox(client, b)['friends']
    assert history(client, a, b).status_code == history(client, b, a).status_code == 403
    sql("UPDATE friend_links SET updated_at='2000-01-01T00:00:00.000+00:00'")
    link(client, a, b)
    assert len(history(client, a, b).json()['items']) == 1
    for actor, peer in ((a, b), (b, a)):
        assert client.post(f"/api/social/blocks/{peer['id']}", headers=actor['headers']).status_code == 200
    assert len(inbox(client, a)['blocked']) == len(inbox(client, b)['blocked']) == 1
    for actor, peer in ((a, b), (b, a)):
        assert send(client, actor, peer).status_code == history(client, actor, peer).status_code == req(client, actor, peer).status_code == 403
    client.delete(f"/api/social/blocks/{b['id']}", headers=a['headers'])
    assert req(client, a, b).status_code == 403
    client.delete(f"/api/social/blocks/{a['id']}", headers=b['headers'])
    assert history(client, a, b).status_code == 403
    assert not inbox(client, a)['friends']


def test_banned_peer_and_revoked_password_auth(client):
    a, b, _ = people(client)
    link(client, a, b)
    sql('UPDATE users SET banned=1 WHERE id=?', (b['id'],))
    assert history(client, a, b).status_code == send(client, a, b).status_code == 404
    assert client.get('/api/social', headers=b['headers']).status_code == 401
    sql('UPDATE users SET banned=0 WHERE id=?', (b['id'],))
    assert client.post('/api/auth/password', headers=a['headers'], json={'old_password': 'test123456', 'new_password': 'newpassword123'}).status_code == 200
    assert history(client, a, b).status_code == client.get('/api/social', headers=a['headers']).status_code == 401


def test_schema_restart_preserves_history_and_unread(client):
    a, b, _ = people(client)
    link(client, a, b)
    item = send(client, a, b).json()
    database.init_db(database._db_path)
    database.init_db(database._db_path)
    assert history(client, b, a).json()['items'][0]['id'] == item['id']
    assert inbox(client, b)['unread_total'] == 1


def test_schema_does_not_commit_callers_transaction():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE users(id INTEGER PRIMARY KEY)')
    conn.execute('INSERT INTO users VALUES(1)')
    social_schema.create_schema(conn)
    assert conn.in_transaction
    conn.rollback()
    assert not conn.execute('SELECT * FROM users').fetchall()
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='friend_links'").fetchall()
    conn.close()


def test_concurrent_nonce_and_cross_request_serialization(client):
    a, b, _ = people(client)
    barrier = threading.Barrier(2)
    def apply(actor, peer):
        barrier.wait(timeout=5)
        return req(client, actor, peer).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        x, y = pool.submit(apply, a, b), pool.submit(apply, b, a)
        assert sorted((x.result(timeout=10), y.result(timeout=10))) == [200, 409]
    pending = sql('SELECT requester_id,status FROM friend_links')[0]
    requester, receiver = (a, b) if pending['requester_id'] == a['id'] else (b, a)
    assert pending['status'] == 'pending'
    assert respond(client, receiver, requester, 'accept').status_code == 200
    barrier = threading.Barrier(4)
    def retry():
        barrier.wait(timeout=5)
        return send(client, a, b).json()['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(retry) for _ in range(4)]
        ids = [future.result(timeout=10) for future in futures]
    assert len(set(ids)) == len(history(client, a, b).json()['items']) == 1


def test_accept_block_race_never_leaves_chat_capability(client):
    a, b, _ = people(client)
    assert req(client, a, b).status_code == 200
    barrier = threading.Barrier(2)
    def accept():
        barrier.wait(timeout=5)
        return respond(client, b, a, 'accept').status_code
    def block():
        barrier.wait(timeout=5)
        return client.post(f"/api/social/blocks/{b['id']}", headers=a['headers']).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        x, y = pool.submit(accept), pool.submit(block)
        assert x.result(timeout=10) in (200, 403)
        assert y.result(timeout=10) == 200
    assert not inbox(client, a)['friends'] and not inbox(client, b)['friends']
    assert history(client, a, b).status_code == 403


def test_friend_capacity_checked_again_at_accept(client, monkeypatch):
    from app import social_common
    a, b, c = people(client)
    monkeypatch.setattr(social_common, 'FRIEND_LIMIT', 1)
    assert req(client, a, c).status_code == 200
    link(client, b, c)
    assert respond(client, c, a, 'accept').status_code == 409
    assert len(inbox(client, c)['friends']) == 1
    assert inbox(client, c)['incoming'][0]['id'] == a['id']
    assert req(client, b, a).status_code == 409


def test_friend_request_rate_limit_is_persistent(client):
    a = user(client, 'request_sender')
    targets = []
    for n in range(21):
        sql('''INSERT INTO users(username,password_hash,salt,created_at)
               SELECT ?,password_hash,salt,created_at FROM users WHERE id=?''', (f'request_peer_{n}', a['id']))
        targets.append({'id': sql('SELECT id FROM users WHERE username=?', (f'request_peer_{n}',))[0]['id']})
    for target in targets[:20]:
        assert req(client, a, target).status_code == 200
    database.init_db(database._db_path)
    assert req(client, a, targets[-1]).status_code == 429
    assert len(inbox(client, a)['outgoing']) == 20


def test_deleted_users_cascade_social_data(client):
    a, b, _ = people(client)
    link(client, a, b)
    item = send(client, a, b).json()
    client.post(f"/api/social/chats/{a['id']}/read", headers=b['headers'], json={'through_id': item['id']})
    sql('DELETE FROM users WHERE id=?', (b['id'],))
    assert not inbox(client, a)['friends']
    for table in ('friend_links', 'direct_messages', 'direct_reads'):
        assert not sql(f'SELECT * FROM {table}')
    assert history(client, a, b).status_code == 404


def test_lightweight_summary_matches_inbox_through_read_remove_and_ban(client):
    a, b, c = people(client)
    link(client, a, b)
    item = send(client, a, b).json()
    assert req(client, c, b).status_code == 200
    def summary():
        response = client.get('/api/social/summary', headers=b['headers'])
        assert response.status_code == 200
        value = response.json()
        full = inbox(client, b)
        assert value == {'unread_total': full['unread_total'], 'incoming_total': len(full['incoming'])}
        return value
    assert summary() == {'unread_total': 1, 'incoming_total': 1}
    client.post(f"/api/social/chats/{a['id']}/read", headers=b['headers'], json={'through_id': item['id']})
    assert summary()['unread_total'] == 0
    assert send(client, a, b, 2).status_code == 200
    sql('UPDATE users SET banned=1 WHERE id=?', (a['id'],))
    assert summary()['unread_total'] == 0
    sql('UPDATE users SET banned=0 WHERE id=?', (a['id'],))
    assert summary()['unread_total'] == 1
    client.post(f"/api/social/blocks/{a['id']}", headers=b['headers'])
    assert summary()['unread_total'] == 0
    sql('UPDATE users SET banned=1 WHERE id=?', (c['id'],))
    assert summary()['incoming_total'] == 0


@pytest.mark.parametrize('extra', [{'client_id': '\ud800'}, {'extra': '\ud800'}, {'extra': {'\ud800': 'value'}}])
def test_escaped_surrogates_cannot_break_validation_error_encoding(client, extra):
    a, b, _ = people(client)
    link(client, a, b)
    payload = {'body': 'valid', 'client_id': 'valid_nonce', **extra}
    response = client.post(f"/api/social/chats/{b['id']}", headers={**a['headers'], 'Content-Type': 'application/json'},
                           content=json.dumps(payload))
    assert response.status_code == 400
