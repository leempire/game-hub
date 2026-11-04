"""Real WebSockets exercise room variants, arbitration privacy and series persistence."""

from contextlib import ExitStack
from copy import deepcopy

import pytest

from app import database, persistence, room_manager as rm, security, ws as ws_module


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, '_db_path', str(tmp_path / 'variants.db'))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, '_jwt_secret', 'isolated-variants-secret-at-least-32-bytes')
    for name in ('_rooms', '_dc_tasks', '_bot_tasks'):
        monkeypatch.setattr(rm, name, {})
    monkeypatch.setattr(rm, '_game_end_callback', database.save_game_record)
    monkeypatch.setattr(rm, '_game_end_listeners', [])
    monkeypatch.setattr(ws_module, '_connections', {})
    monkeypatch.setattr(persistence, '_enabled', False)
    return ws_client


def user(client, name):
    response = client.post('/api/auth/register', json={'username': name, 'password': 'test123456'})
    assert response.status_code == 200, response.text
    data = response.json()
    return data['user']['id'], data['token'], {'Authorization': f"Bearer {data['token']}"}


def recv(socket, kind):
    for _ in range(30):
        message = socket.receive_json()
        if message['type'] == kind:
            return message
    raise AssertionError(f'Missing {kind}')


def room(client, game, count=2):
    users = [user(client, f'variant_{game}_{i}') for i in range(count)]
    response = client.post('/api/rooms', json={'game': game}, headers=users[0][2])
    assert response.status_code == 200
    code = response.json()['code']
    for member in users[1:]:
        assert client.post(f'/api/rooms/{code}/join', json={}, headers=member[2]).status_code == 200
    return code, users


def connect(client, stack, code, users):
    sockets = [stack.enter_context(client.websocket_connect(f'/ws/room/{code}?token={u[1]}')) for u in users]
    for socket in sockets:
        recv(socket, 'sync')
    return sockets


def start(sockets):
    for socket in sockets[1:]:
        socket.send_json({'type': 'ready'})
    sockets[0].send_json({'type': 'start'})
    return [recv(socket, 'game_start')['game'] for socket in sockets]


@pytest.mark.parametrize('game,key,value,size', [('gomoku', 'board_size', 9, 9),
                                                ('reversi', 'board_size', 10, 10),
                                                ('dots', 'dots', 6, 6)])
def test_setting_reaches_start_and_reconnect_sync(client, game, key, value, size):
    code, users = room(client, game)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        sockets[0].send_json({'type': 'room_setting', 'key': key, 'value': value})
        for socket in sockets:
            changed = recv(socket, 'lobby')['room']
            # Skip a lobby queued by the second connection if necessary.
            while next(item for item in changed['settings'] if item['key'] == key)['value'] != value:
                changed = recv(socket, 'lobby')['room']
        games = start(sockets)
        assert all(g.get('board_size', g.get('dots')) == size for g in games)
        if game != 'dots':
            assert all(len(g['board']) == size and g['legal'] for g in games)
            x, y = games[0]['legal'][0]
            sockets[0].send_json({'type': 'move', 'x': x, 'y': y})
            for socket in sockets:
                assert 'legal' in recv(socket, 'move')
        else:
            assert len(games[0]['h']) == size and len(games[0]['v'][0]) == size
        replacement = stack.enter_context(client.websocket_connect(f'/ws/room/{code}?token={users[0][1]}'))
        sync = recv(replacement, 'sync')['game']
        assert sync.get('board_size', sync.get('dots')) == size


def test_gomoku_forbidden_and_malformed_actions_do_not_mutate(client):
    code, users = room(client, 'gomoku')
    rm.set_room_setting(code, users[0][0], 'forbidden', True)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        start(sockets)
        gs = rm.get_room(code)['game_state']
        for x, y in ((6, 7), (8, 7), (7, 6), (7, 8)):
            gs['board'][y][x] = 1
        before = deepcopy(gs)
        sockets[0].send_json({'type': 'move', 'x': 7, 'y': 7})
        assert '禁手' in recv(sockets[0], 'error')['message']
        assert gs == before
        sockets[0].send_json({'type': 'move', 'x': True, 'y': 0})
        assert '整数' in recv(sockets[0], 'error')['message']
        assert gs == before


def test_popout_broadcasts_authoritative_board_and_settles_once(client):
    code, users = room(client, 'connect4')
    rm.set_room_setting(code, users[0][0], 'popout', True)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        games = start(sockets)
        assert all(g['popout'] and g['legal_actions'] for g in games)
        gs = rm.get_room(code)['game_state']
        for col in range(3):
            gs['board'][5][col], gs['board'][4][col] = 2, 1
        gs['board'][5][3], gs['board'][4][3], gs['board'][3][3] = 1, 2, 1
        sockets[0].send_json({'type': 'move', 'x': 3, 'action': 'pop'})
        for socket in sockets:
            moved = recv(socket, 'move')
            assert moved['action'] == 'pop' and moved['board'] == gs['board']
            assert recv(socket, 'game_over')['winner'] == 'black'
        assert rm.get_room(code)['status'] == 'waiting'
        assert database.get_user_history(users[0][0])['total'] == 1
        sockets[0].send_json({'type': 'move', 'x': 3, 'action': 'pop'})
        recv(sockets[0], 'error')
        assert database.get_user_history(users[0][0])['total'] == 1


def test_ludo_team_rules_and_animation_are_in_start_and_terminal_result(client):
    code, users = room(client, 'ludo', 4)
    for key, value in [('teams', '2v2'), ('capture', False), ('color_jump', False),
                        ('exact_finish', False), ('animation_speed', 'fast')]:
        rm.set_room_setting(code, users[0][0], key, value)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        games = start(sockets)
        assert all(g['seat_teams'] == [0, 1, 0, 1] and g['animation_scale'] == 0.6 for g in games)
        assert not games[0]['rules']['capture']
        sockets[0].send_json({'type': 'surrender'})
        for socket in sockets:
            result = recv(socket, 'game_over')
            assert result['reason'] == 'team_surrender'
            assert {row['seat'] for row in result['ranking'] if row['rank'] == 1} == {1, 3}


def test_uno_arbitration_is_authoritative_private_and_repeat_safe(client):
    code, users = room(client, 'uno', 3)
    rm.set_room_setting(code, users[0][0], 'wild4_challenge', True)
    rm.set_room_setting(code, users[0][0], 'uno_penalty', True)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        start(sockets)
        gs = rm.get_room(code)['game_state']
        gs['hands'] = {0: ['W+4', 'R2'], 1: ['B3', 'Y4'], 2: ['G1', 'B2']}
        gs['discard_pile'], gs['current_color'], gs['draw_pile'] = ['R5'], 'R', ['B2'] * 30
        sockets[0].send_json({'type': 'uno_play', 'card': 'W+4', 'color': 'G'})
        states = [recv(socket, 'uno_state') for socket in sockets]
        assert [state['can_challenge_wild4'] for state in states] == [False, True, False]
        assert all('guilty' not in repr(state) and 'wild4_pending' not in state for state in states)
        assert states[1]['hand'] == ['B3', 'Y4'] and states[2]['hand'] == ['G1', 'B2']
        before = deepcopy(gs)
        sockets[2].send_json({'type': 'uno_challenge_wild4'})
        recv(sockets[2], 'error')
        assert gs == before
        sockets[1].send_json({'type': 'uno_challenge_wild4'})
        for socket in sockets:
            event = recv(socket, 'uno_event')
            assert event['kind'] == 'challenge_wild4' and event['success']
            state = recv(socket, 'uno_state')
            assert state['hand_counts']['0'] == 5 and state['turn'] == 1
        before = deepcopy(gs)
        sockets[1].send_json({'type': 'uno_challenge_wild4'})
        recv(sockets[1], 'error')
        assert gs == before
        # A fresh legal play leaves one card; another seat can catch it once.
        gs['hands'][1], gs['current_color'] = ['R1', 'B1'], 'R'
        sockets[1].send_json({'type': 'uno_play', 'card': 'R1'})
        for socket in sockets:
            recv(socket, 'uno_state')
        sockets[2].send_json({'type': 'uno_catch', 'target': 1})
        for socket in sockets:
            event = recv(socket, 'uno_event')
            assert event['kind'] == 'catch_uno' and event['penalty'] == 2
            assert recv(socket, 'uno_state')['hand_counts']['1'] == 3


def test_uno_series_survives_lobby_restart_and_finishes_only_at_500(client):
    code, users = room(client, 'uno')
    rm.set_room_setting(code, users[0][0], 'score_target', 500)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        assert all(g['series']['scores'] == {str(u[0]): 0 for u in users} for g in start(sockets))
        room_state = rm.get_room(code)
        for hand_number in range(2):
            gs = room_state['game_state']
            gs['hands'], gs['discard_pile'], gs['current_color'] = {0: ['R1'], 1: ['W+4'] * 5}, ['R5'], 'R'
            sockets[0].send_json({'type': 'uno_play', 'card': 'R1'})
            for socket in sockets:
                result = recv(socket, 'game_over')
                assert result['series']['scores'][str(users[0][0])] == (hand_number + 1) * 250
                assert result['series']['finished'] == (hand_number == 1)
            assert room_state['status'] == 'waiting'
            encoded = persistence.dumps_state(room_state)
            restored = persistence.loads_state(encoded)
            assert restored['uno_series'] == room_state['uno_series']
            if hand_number == 0:
                fresh = start(sockets)
                assert fresh[0]['series']['scores'][str(users[0][0])] == 250
        assert database.get_user_history(users[0][0])['total'] == 2
        # Completed series resets on next room-host start.
        assert start(sockets)[0]['series']['scores'][str(users[0][0])] == 0


def test_series_resets_on_seat_roster_or_rules_change_and_is_idempotent(client):
    code, users = room(client, 'uno')
    rm.set_room_setting(code, users[0][0], 'score_target', 500)
    current = rm.get_room(code)
    current['players'][1]['ready'] = True
    gs = rm.start_game(code, users[0][0])
    gs['hands'], gs['winner'], gs['finished'], gs['reason'] = {0: [], 1: ['W+4']}, 0, True, 'emptied'
    rm._finalize_uno_game(current)
    assert current['uno_series']['scores'][str(users[0][0])] == 50
    rm._settle_uno_series(current, gs)
    assert current['uno_series']['scores'][str(users[0][0])] == 50
    # Same roster in a different seat assignment is a new series.
    rm._swap_seats(current, users[0][0], users[1][0])
    rm._touch_activity(current)
    assert 'uno_series' not in current
    for player in current['players']:
        player['ready'] = True
    rm.start_game(code, users[0][0])
    assert current['uno_series']['scores'] == {str(u[0]): 0 for u in users}
    rm.surrender(code, users[0][0])
    rm.set_room_setting(code, users[0][0], 'wild4_strict', True)
    assert 'uno_series' not in current


def test_variant_snapshots_and_series_round_results_are_indexed(client):
    for game, key, value, expected in [('gomoku', 'board_size', 9, 9),
                                        ('reversi', 'board_size', 10, 10),
                                        ('connect4', 'popout', True, None)]:
        code, users = room(client, game)
        rm.set_room_setting(code, users[0][0], key, value)
        current = rm.get_room(code)
        current['players'][1]['ready'] = True
        rm.start_game(code, users[0][0])
        rm.surrender(code, users[0][0])
        snapshot = database.get_user_history(users[0][0])['items'][0]['snapshot']
        if expected:
            assert snapshot['board_size'] == expected and len(snapshot['board']) == expected
        else:
            assert snapshot['popout']
    code, users = room(client, 'uno')
    rm.set_room_setting(code, users[0][0], 'score_target', 500)
    current = rm.get_room(code)
    current['players'][1]['ready'] = True
    gs = rm.start_game(code, users[0][0])
    gs['hands'], gs['discard_pile'], gs['current_color'] = {0: ['R1'], 1: ['W+4']}, ['R5'], 'R'
    assert rm.uno_play_action(code, users[0][0], 'R1')['ok']
    saved = database.get_user_history(users[0][0])['items'][0]
    assert saved['snapshot']['series']['scores'][str(users[0][0])] == 50
    assert all(result['result'] == 'end' for result in saved['results'])
    assert next(r for r in saved['results'] if r['username'] == 'variant_uno_0')['round_result'] == 'win'
    conn = database.get_conn()
    try:
        values = conn.execute('SELECT result FROM game_participants WHERE record_id = ?', (saved['id'],)).fetchall()
        assert [r['result'] for r in values] == ['end', 'end']
    finally:
        conn.close()


def test_uno_team_winners_are_both_saved_as_wins(client):
    code, users = room(client, 'uno', 4)
    rm.set_room_setting(code, users[0][0], 'teams', '2v2')
    current = rm.get_room(code)
    for player in current['players']:
        player['ready'] = True
    gs = rm.start_game(code, users[0][0])
    gs.update(finished=True, reason='team_emptied', winner=0, winner_team=0,
              hands={0: [], 1: ['R1'], 2: [], 3: ['B2']})
    rm._finalize_uno_game(current)
    saved = database.get_user_history(users[2][0])['items'][0]
    results = {r['username']: r['result'] for r in saved['results']}
    assert results == {'variant_uno_0': 'win', 'variant_uno_1': 'lose',
                       'variant_uno_2': 'win', 'variant_uno_3': 'lose'}


def test_black_with_no_legal_point_loses_once_after_white_move(client):
    code, users = room(client, 'gomoku')
    rm.set_room_setting(code, users[0][0], 'board_size', 9)
    rm.set_room_setting(code, users[0][0], 'forbidden', True)
    with ExitStack() as stack:
        sockets = connect(client, stack, code, users)
        start(sockets)
        gs = rm.get_room(code)['game_state']
        gs['board'] = [[1 if (x + 2 * y) % 4 < 2 else 2 for x in range(9)] for y in range(9)]
        for x in (1, 2, 3, 5, 6, 7):
            gs['board'][4][x] = 1
        gs['board'][4][4] = gs['board'][0][2] = 0
        gs['next'], gs['moves'] = 'white', [{'x': 0, 'y': 0, 'color': 'black'}] * 79
        sockets[1].send_json({'type': 'move', 'x': 2, 'y': 0})
        for socket in sockets:
            assert recv(socket, 'move')['legal'] == []
            result = recv(socket, 'game_over')
            assert result['winner'] == 'white' and result['reason'] == 'no_legal_moves'
        assert rm.get_room(code)['status'] == 'waiting'
        assert database.get_user_history(users[0][0])['total'] == 1
