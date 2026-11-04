"""Practice isolation, phrase validation and real read-only spectator WebSockets."""

import asyncio
from contextlib import ExitStack
from copy import deepcopy

import pytest
from starlette.websockets import WebSocketDisconnect

from app import database, persistence, room_manager as rm, security, ws as room_ws
from app import quick_chat, spectating


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, '_db_path', str(tmp_path / 'social.db'))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, '_jwt_secret', 'isolated-social-secret-at-least-32-bytes')
    for name in ('_rooms', '_dc_tasks', '_bot_tasks', '_tick_tasks'):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(rm, '_game_end_callback', database.save_game_record)
    monkeypatch.setattr(rm, '_game_end_listeners', [])
    monkeypatch.setattr(room_ws, '_connections', {})
    monkeypatch.setattr(spectating, '_grants', {})
    monkeypatch.setattr(spectating, '_watchers', {})
    monkeypatch.setattr(spectating, 'SAMPLE_SECONDS', 0.01)
    monkeypatch.setattr(quick_chat, '_last_sent', {})
    monkeypatch.setattr(persistence, '_enabled', False)
    return ws_client


def user(client, name):
    response = client.post('/api/auth/register', json={'username': name, 'password': 'test123456'})
    assert response.status_code == 200
    data = response.json()
    return {'id': data['user']['id'], 'token': data['token'],
            'headers': {'Authorization': f"Bearer {data['token']}"}}


def recv(socket, kind):
    for _ in range(30):
        message = socket.receive_json()
        if message['type'] == kind:
            return message
    raise AssertionError(f'Missing {kind}')


def create_room(client, game='uno', players=2, password=None):
    users = [user(client, f'social_{game}_{i}') for i in range(players)]
    response = client.post('/api/rooms', json={'game': game, 'password': password}, headers=users[0]['headers'])
    assert response.status_code == 200
    code = response.json()['code']
    for member in users[1:]:
        assert client.post(f'/api/rooms/{code}/join', json={'password': password}, headers=member['headers']).status_code == 200
    return code, users


def open_players(client, stack, code, users):
    sockets = [stack.enter_context(client.websocket_connect(f"/ws/room/{code}?token={u['token']}")) for u in users]
    for socket in sockets:
        recv(socket, 'sync')
    for socket in sockets[1:]:
        socket.send_json({'type': 'ready'})
    sockets[0].send_json({'type': 'start'})
    for socket in sockets:
        recv(socket, 'game_start')
    return sockets


def grant(client, code, watcher, password=None):
    return client.post(f'/api/rooms/{code}/watch', json={'password': password}, headers=watcher['headers'])


def watch_url(code, watcher, token):
    return f"/ws/watch/{code}?token={watcher['token']}&grant={token}"


@pytest.mark.parametrize('game', ['stasis', 'luzhanqi', 'battleship', 'uno'])
def test_practice_is_private_reused_and_unranked(client, game):
    owner, outsider = user(client, 'practice_owner'), user(client, 'practice_other')
    response = client.post(f'/api/practice/{game}', headers=owner['headers'])
    assert response.status_code == 200
    data = response.json()
    assert data['room']['practice'] and not data['reused']
    current = rm.get_room(data['code'])
    assert current['practice_owner_id'] == owner['id']
    assert len(current['players']) == 2 and current['players'][1]['is_bot']
    repeated = client.post(f'/api/practice/{game}', headers=owner['headers']).json()
    assert repeated['code'] == data['code'] and repeated['reused']
    # Explicit admission isolation must hold even if a password is somehow known.
    joined = client.post(f"/api/rooms/{data['code']}/join", json={'password': current['password']}, headers=outsider['headers'])
    assert joined.status_code in (403, 409)
    assert '练习' in joined.json()['detail']
    with client.websocket_connect(f"/ws/room/{data['code']}?token={owner['token']}") as socket:
        recv(socket, 'sync')
        socket.send_json({'type': 'start'})
        recv(socket, 'game_start')
        assert not client.get('/api/rooms', headers=outsider['headers']).json()['items']
        assert client.get('/api/history/mine', headers=owner['headers']).json()['total'] == 0
        if game == 'stasis':
            for player in current['players']:
                rm.surrender(data['code'], player['id'])
        else:
            rm.surrender(data['code'], owner['id'])
    conn = database.get_conn()
    try:
        stored = conn.execute("SELECT practice FROM game_records WHERE room_code = ?", (data["code"],)).fetchall()
        assert len(stored) == 1 and stored[0]["practice"] == 1
    finally:
        conn.close()
    assert database.get_user_history(owner['id'])['total'] == 0
    assert database.get_user_stats(owner['id'])['total'] == 0
    board = client.get(f'/api/leaderboards/{game}', headers=owner['headers']).json()
    assert board['items'] == []


def test_practice_rejects_unsupported_game_and_unauthenticated_request(client):
    owner = user(client, 'practice_owner')
    assert client.post('/api/practice/gomoku', headers=owner['headers']).status_code == 400
    assert client.post('/api/practice/uno').status_code in (401, 403)


def test_quick_chat_is_whitelisted_member_only_and_rate_limited(client):
    code, users = create_room(client)
    outsider = user(client, 'outsider')
    with ExitStack() as stack:
        sockets = open_players(client, stack, code, users)
        url = f'/api/rooms/{code}/quick-chat'
        assert client.post(url, json={'id': 'hello'}, headers=outsider['headers']).status_code == 403
        assert client.post(url, json={'id': 'hello', 'text': 'arbitrary'}, headers=users[0]['headers']).status_code == 422
        assert client.post(url, json={'id': 'not_a_phrase'}, headers=users[0]['headers']).status_code == 400
        response = client.post(url, json={'id': 'hello'}, headers=users[0]['headers'])
        assert response.status_code == 200
        for socket in sockets:
            message = recv(socket, 'quick_chat')
            assert message['id'] == 'hello' and message['text'] == '大家好，一起玩吧'
            assert message['user_id'] == users[0]['id']
        assert client.post(url, json={'id': 'thanks'}, headers=users[0]['headers']).status_code == 429
        # A fake client time cannot alter the server-side cooldown.
        assert client.post(url, json={'id': 'thanks', 'now': 99999999}, headers=users[0]['headers']).status_code == 422
        quick_chat._last_sent[(code, users[0]['id'])] -= 2.1
        assert client.post(url, json={'id': 'thanks'}, headers=users[0]['headers']).status_code == 200


def test_full_uno_room_can_be_watched_without_seat_or_private_cards(client):
    code, users = create_room(client, players=4)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    with ExitStack() as stack:
        sockets = open_players(client, stack, code, users)
        current = rm.get_room(code)
        before = deepcopy(current)
        authorized = grant(client, code, observer)
        assert authorized.status_code == 200
        ticket = authorized.json()['grant']
        with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
            message = recv(watcher, 'watch_sync')
            assert message['read_only'] and message['game']['game'] == 'uno'
            assert len(message['room']['players']) == 4
            forbidden = {'hand', 'hands', 'draw_pile', 'playable', 'jumpable', 'can_draw',
                         'can_pass', 'your_seat', 'your_team', 'wild4_pending', 'guilty'}
            assert not forbidden.intersection(message['game'])
            assert current == before and observer['id'] not in room_ws._connections.get(code, {})
            watcher.send_json({'type': 'uno_play', 'card': 'R1'})
            assert '只读' in recv(watcher, 'error')['message']
            assert current == before
            watcher.send_json({'type': 'ping'})
            recv(watcher, 'pong')
            assert client.post(f'/api/rooms/{code}/quick-chat', json={'id': 'hello'}, headers=observer['headers']).status_code == 403
            client.post(f'/api/rooms/{code}/quick-chat', json={'id': 'nice'}, headers=users[0]['headers'])
            assert recv(watcher, 'quick_chat')['text'] == '打得漂亮'
        assert current['players'] == before['players']
        with client.websocket_connect(watch_url(code, observer, ticket)) as resumed:
            assert recv(resumed, 'watch_sync')['read_only']
        assert current['players'] == before['players']
        # Ordinary room WS stays member-only, even with a valid watching grant.
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(f"/ws/room/{code}?token={observer['token']}"):
                pass
        assert error.value.code == 4003
        assert len(sockets) == 4


def test_grants_require_password_login_permission_and_are_user_bound(client):
    code, users = create_room(client, password='私密口令')
    observer, other = user(client, 'watcher'), user(client, 'other_watcher')
    assert grant(client, code, observer, '私密口令').status_code == 403
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    assert grant(client, code, observer, 'wrong').status_code == 403
    response = grant(client, code, observer, '私密口令')
    assert response.status_code == 200
    ticket = response.json()['grant']
    for path, expected in [(watch_url(code, other, ticket), 4003),
                           (f'/ws/watch/{code}?grant={ticket}', 4001),
                           (watch_url(code, observer, 'invalid'), 4003)]:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path):
                pass
        assert error.value.code == expected
    # Even a missed rapid off/on permission cycle invalidates the old grant.
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', False)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    assert not spectating.valid_grant(ticket, observer['id'], code)
    fresh = grant(client, code, observer, '私密口令').json()['grant']
    assert fresh != ticket


def test_permission_and_room_close_disconnect_watchers_and_updates_are_public(client):
    code, users = create_room(client)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    ticket = grant(client, code, observer).json()['grant']
    with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
        assert recv(watcher, 'watch_sync')['game'] is None
        rm.set_room_setting(code, users[0]['id'], 'allow_spectators', False)
        with pytest.raises(WebSocketDisconnect) as error:
            watcher.receive_json()
        assert error.value.code == 4003
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    ticket = grant(client, code, observer).json()['grant']
    with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
        recv(watcher, 'watch_sync')
        rm._rooms.pop(code)
        with pytest.raises(WebSocketDisconnect) as error:
            watcher.receive_json()
        assert error.value.code == 4003


def test_stasis_watch_has_no_player_identity_or_state_mutation(client):
    code, users = create_room(client, 'stasis')
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    with ExitStack() as stack:
        open_players(client, stack, code, users)
        current = rm.get_room(code)
        before_players = deepcopy(current['players'])
        ticket = grant(client, code, observer).json()['grant']
        with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
            view = recv(watcher, 'watch_sync')['game']
            assert view['game'] == 'stasis' and 'map' in view
            assert 'your_uid' not in view and 'your_seat' not in view
            assert current['players'] == before_players
            watcher.send_json({'type': 'stasis_build', 'kind': 'turret', 'cell': '0,0'})
            assert '只读' in recv(watcher, 'error')['message']
            assert current['players'] == before_players


def test_spectator_allowlist_removes_future_private_fields(client, monkeypatch):
    code, users = create_room(client)
    current = rm.get_room(code)
    for player in current['players']:
        player['ready'] = True
    rm.start_game(code, users[0]['id'])
    original = spectating.uno.game_to_sync

    def future_view(*args):
        return {**original(*args), 'future_secret': 'hidden', 'hand': ['R9'], 'playable': ['R9']}

    monkeypatch.setattr(spectating.uno, 'game_to_sync', future_view)
    view = spectating.public_game(current)
    assert 'future_secret' not in view and 'hand' not in view and 'playable' not in view


def test_watcher_receives_real_uno_updates_without_action_fields(client):
    code, users = create_room(client)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    with ExitStack() as stack:
        sockets = open_players(client, stack, code, users)
        gs = rm.get_room(code)['game_state']
        gs['hands'], gs['discard_pile'], gs['current_color'] = {0: ['R1', 'B2'], 1: ['G3', 'Y4']}, ['R5'], 'R'
        ticket = grant(client, code, observer).json()['grant']
        with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
            assert recv(watcher, 'watch_sync')['game']['turn'] == 0
            sockets[0].send_json({'type': 'uno_play', 'card': 'R1'})
            recv(sockets[0], 'uno_state')
            updated = recv(watcher, 'watch_state')['game']
            assert updated['discard_top'] == 'R1' and updated['hand_counts']['0'] == 1
            assert updated['turn'] == 1 and 'hand' not in updated and 'playable' not in updated


def test_spectator_send_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(spectating, 'SEND_TIMEOUT', 0.01)

    class SlowSocket:
        async def send_json(self, message):
            await asyncio.Event().wait()

    async def exercise():
        assert not await spectating._send(SlowSocket(), asyncio.Lock(), {'type': 'watch_state'})

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.close()


def test_spectator_rechecks_revoked_login_and_removes_connection(client, monkeypatch):
    code, users = create_room(client)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    ticket = grant(client, code, observer).json()['grant']
    monkeypatch.setattr(spectating, 'AUTH_CHECK_SECONDS', 0)
    with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
        recv(watcher, 'watch_sync')
        conn = database.get_conn()
        try:
            conn.execute('UPDATE users SET banned = 1 WHERE id = ?', (observer['id'],))
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(WebSocketDisconnect) as error:
            watcher.receive_json()
        assert error.value.code == 4001
    assert not spectating._watchers.get(code)


def test_changes_during_initial_watch_send_are_not_lost(client, monkeypatch):
    code, users = create_room(client)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    ticket = grant(client, code, observer).json()['grant']
    original = spectating._send

    async def change_after_initial_send(socket, lock, payload, **kwargs):
        result = await original(socket, lock, payload, **kwargs)
        if payload.get('type') == 'watch_sync':
            rm.get_room(code)['name'] = '更新后的房间'
        return result

    monkeypatch.setattr(spectating, '_send', change_after_initial_send)
    with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
        assert recv(watcher, 'watch_sync')['room']['name'] != '更新后的房间'
        assert recv(watcher, 'watch_state')['room']['name'] == '更新后的房间'


def test_existing_kick_path_disconnects_watching_sessions_immediately(client, monkeypatch):
    code, users = create_room(client)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    ticket = grant(client, code, observer).json()['grant']
    monkeypatch.setattr(spectating, 'AUTH_CHECK_SECONDS', 3600)
    with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
        recv(watcher, 'watch_sync')
        # The production ban route uses this same kick function.
        client.portal.call(room_ws.kick_user, observer['id'], '账号已被封禁')
        with pytest.raises(WebSocketDisconnect) as error:
            watcher.receive_json()
        assert error.value.code == 4001
    assert ticket not in spectating._grants and not spectating._watchers.get(code)


def test_password_change_immediately_disconnects_watcher_without_polling(client, monkeypatch):
    code, users = create_room(client)
    rm.set_room_setting(code, users[0]['id'], 'allow_spectators', True)
    observer = user(client, 'watcher')
    ticket = grant(client, code, observer).json()['grant']
    monkeypatch.setattr(spectating, 'AUTH_CHECK_SECONDS', 3600)
    with client.websocket_connect(watch_url(code, observer, ticket)) as watcher:
        recv(watcher, 'watch_sync')
        response = client.post('/api/auth/password', json={
            'old_password': 'test123456', 'new_password': 'changed123456',
        }, headers=observer['headers'])
        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect) as error:
            watcher.receive_json()
        assert error.value.code == 4001
    assert ticket not in spectating._grants and not spectating._watchers.get(code)
