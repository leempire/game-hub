"""Fixed-truth engine, AI-host privacy, account history exclusion and paid WS flow."""

import asyncio
from contextlib import ExitStack
import json
from uuid import uuid4

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from app import ai_credits, ai_service, database, persistence, room_manager as rm
from app import turtle_soup_catalog as catalog, turtle_soup_host as host, ws as transport
from app.game import turtle_soup as soup


@pytest.fixture
def client(ws_client, tmp_path, monkeypatch):
    monkeypatch.setattr(database, '_db_path', str(tmp_path / 'soup.db'))
    database.init_db(database._db_path)
    monkeypatch.setattr(persistence, '_enabled', True)
    persistence.init()
    monkeypatch.setattr(rm, '_rooms', {})
    monkeypatch.setattr(rm, '_dc_tasks', {})
    monkeypatch.setattr(rm, '_game_end_callback', database.save_game_record)
    monkeypatch.setattr(host, '_tasks', {})
    monkeypatch.setattr(host, '_closing', False)
    monkeypatch.setenv('ARK_AGENT_API_KEY', 'mock-only')
    transport._register_dc_timeout_callback()
    return ws_client


def register(client):
    response = client.post('/api/auth/register', json={'username': 'soup_' + uuid4().hex[:10], 'password': 'test123456'})
    assert response.status_code == 200
    data = response.json()
    return data['user'], data['token']


def auth(token):
    return {'Authorization': 'Bearer ' + token}


def receive(socket, kind, predicate=lambda value: True):
    for _ in range(80):
        message = socket.receive_json()
        if message['type'] == kind and predicate(message):
            return message
    raise AssertionError('Missing ' + kind)


def action(state, text='这是一个可以判断的问题吗？', kind='ask', request_id=None):
    return {'type': 'soup_submit', 'match_id': state['match_id'], 'revision': state['revision'],
            'request_id': request_id or uuid4().hex, 'kind': kind, 'text': text}


def provider(monkeypatch, transform=lambda data, value: value):
    calls = []
    async def post(self, url, **kwargs):
        data = json.loads(kwargs['json']['messages'][1]['content'])
        calls.append(data)
        guessing = data['kind'] == 'guess'
        value = {'answer': 'yes', 'solved': guessing, 'contradiction': False,
                 'covered_facts': list(range(len(data['essential_facts']))) if guessing else []}
        value = transform(data, value)
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                      'prompt_tokens_details': {'cached_tokens': 100}},
            'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    return calls


def lobby(client, stack, count=2, credits=1000):
    people = [register(client) for _ in range(count)]
    user, token = people[0]
    ai_credits.set_balance(user['id'], credits, user['id'])
    response = client.post('/api/rooms', json={'game': 'turtle_soup'}, headers=auth(token))
    assert response.status_code == 200, response.text
    code = response.json()['code']
    for _, peer_token in people[1:]:
        assert client.post(f'/api/rooms/{code}/join', json={}, headers=auth(peer_token)).status_code == 200
    sockets = [stack.enter_context(client.websocket_connect(f'/ws/room/{code}?token={peer_token}'))
               for _, peer_token in people]
    for index, socket in enumerate(sockets):
        receive(socket, 'sync')
        if index:
            socket.send_json({'type': 'ready'})
    return code, people, sockets


def start(sockets):
    sockets[0].send_json({'type': 'start'})
    return [receive(socket, 'game_start')['game'] for socket in sockets]


def engine():
    return soup.create_game(catalog.all_puzzles()[0], [1, 2], 'match', 1)


def judgment(state, **values):
    return {'answer': 'yes', 'solved': True, 'contradiction': False,
            'covered_facts': list(range(len(state['essential_facts']))), **values}


def test_engine_only_explicit_guess_can_solve():
    state = engine()
    error, changed = soup.submit(state, 1, action(state))
    assert error is None and changed
    soup.apply_response(state, judgment(state))
    assert state['phase'] == 'playing'
    assert state['requests'][0]['answer'] == '是'
    soup.submit(state, 2, action(state, kind='guess'))
    soup.apply_response(state, judgment(state))
    assert state['phase'] == 'finished' and state['solved_by'] == 2
    assert soup.snapshot(state)['solution']


@pytest.mark.parametrize('kwargs', [
    {'covered_facts': []}, {'contradiction': True}, {'solved': False},
])
def test_partial_or_contradictory_guess_stays_open(kwargs):
    state = engine()
    soup.submit(state, 1, action(state, kind='guess'))
    soup.apply_response(state, judgment(state, **kwargs))
    assert state['phase'] == 'playing' and not state['requests'][0]['correct']


def test_engine_idempotence_stale_ids_and_text_limits():
    state = engine()
    data = action(state)
    assert soup.submit(state, 1, data) == (None, True)
    assert soup.submit(state, 1, data) == (None, False)
    assert soup.submit(state, 1, {**data, 'text': '修改'})[0]
    assert soup.submit(state, 2, action(state))[0]
    soup.fail_pending(state, '中断')
    for data in [action(state, text='x'*201), action(state, text='x'*501, kind='guess'),
                 action(state, text='\n'), {**action(state), 'revision': True},
                 {**action(state), 'match_id': 'old'}, {**action(state), 'request_id': 'bad'}]:
        assert soup.submit(state, 1, data)[0]
    assert soup.view_for(state, 1)['can_submit']


def test_view_and_record_whitelist():
    state = engine()
    public = soup.view_for(state, 1)
    assert not {'puzzle_id', 'solution', 'essential_facts', 'source', 'ai_owner_id'} & public.keys()
    soup.finish(state, 'revealed')
    final = soup.snapshot(state)
    assert final['solution']
    assert not {'requests', 'essential_facts', 'ai_owner_id', 'puzzle_id'} & final.keys()


def test_submission_limit_finishes_once():
    state = engine()
    for _ in range(soup.MAX_REQUESTS):
        soup.submit(state, 1, action(state))
        soup.apply_response(state, judgment(state, solved=False, covered_facts=[]))
    assert state['reason'] == 'limit'
    assert 'hint' not in state['requests'][-1]
    assert soup.submit(state, 1, action(state))[0]


def test_catalog_multiple_batches_and_source_metadata():
    puzzles = catalog.all_puzzles()
    assert len(puzzles) >= 15
    assert len(list(catalog.CATALOG_DIR.glob('*.json'))) >= 3
    assert len({p['family'] for p in puzzles}) == len(puzzles)
    for puzzle in puzzles:
        assert puzzle['source']['url'].startswith('https://')
        assert puzzle['source']['license'] and puzzle['essential_facts']


def test_mandatory_host_gate_even_solo(client, monkeypatch):
    with ExitStack() as stack:
        code, people, sockets = lobby(client, stack, 1, credits=0)
        assert rm.get_room(code)['max_players'] == 8
        sockets[0].send_json({'type': 'start'})
        assert '100' in receive(sockets[0], 'error')['message']
        ai_credits.set_balance(people[0][0]['id'], 100, people[0][0]['id'])
        monkeypatch.delenv('ARK_AGENT_API_KEY')
        sockets[0].send_json({'type': 'start'})
        assert '未配置' in receive(sockets[0], 'error')['message']
        monkeypatch.setenv('ARK_AGENT_API_KEY', 'mock')
        state = start(sockets)[0]
        assert state['surface'] and not state['requests']
        sockets[0].send_json({'type': 'add_bot'})
        assert '等待' in receive(sockets[0], 'error')['message']


def test_shared_game_privacy_billing_terminal_history_and_share(client, monkeypatch):
    calls = provider(monkeypatch, lambda data, value: {**value, 'solution': data['solution'], 'extra': '不要泄露'})
    with ExitStack() as stack:
        code, people, sockets = lobby(client, stack)
        states = start(sockets)
        state = states[0]
        secret = rm.get_room(code)['game_state']['solution']
        assert secret not in json.dumps(states, ensure_ascii=False)
        assert secret not in client.get(f'/api/rooms/{code}', headers=auth(people[0][1])).text
        sockets[0].send_json(action(state))
        answered = [receive(socket, 'soup_state', lambda m: m['game']['requests'][-1]['status']=='answered')['game']
                    for socket in sockets]
        assert answered[0]['requests'] == answered[1]['requests']
        assert secret not in json.dumps(answered, ensure_ascii=False)
        assert answered[0]['requests'][0]['answer'] == '是'
        sockets[1].send_json(action(answered[1], kind='guess', text='完整推理'))
        results = [receive(socket, 'game_over') for socket in sockets]
        assert all(result['reason']=='solved' and result['summary']['solution']==secret for result in results)
        assert rm.get_room(code)['status'] == 'waiting'
        owner = people[0][0]['id']
        assert ai_credits.balance_units(owner) == 1000 * ai_credits.UNITS - 2 * 65100
        assert len(calls) == 2
        assert ai_credits.balance_units(people[1][0]['id']) == 0
        record = client.get('/api/history/mine', headers=auth(people[0][1])).json()['items'][0]
        assert record['snapshot']['solution'] == secret
        share = client.post(f"/api/history/{record['id']}/share", headers=auth(people[0][1])).json()
        public = client.get('/api/history/shared/' + share['token']).json()
        assert 'solution' not in public['snapshot'] and secret not in json.dumps(public, ensure_ascii=False)
        assert public['snapshot']['source']['name']
        assert '/blob/' not in public['snapshot']['source']['url']
        for socket in sockets:
            receive(socket, 'lobby')
        sockets[1].send_json({'type':'ready'})
        next_states = start(sockets)
        assert next_states[0]['surface'] != state['surface']
        conn = database.get_conn()
        assert conn.execute('SELECT count(*) FROM turtle_soup_seen').fetchone()[0] == 4
        conn.close()


def test_invalid_model_answer_still_charged_without_leaking(client, monkeypatch):
    provider(monkeypatch, lambda data, value: {**value, 'answer': data['solution']})
    with ExitStack() as stack:
        code, people, sockets = lobby(client, stack, 1)
        state = start(sockets)[0]
        sockets[0].send_json(action(state))
        failed = receive(sockets[0], 'soup_state', lambda m: m['game']['requests'][-1]['status']=='failed')['game']
        assert failed['can_submit']
        assert 'solution' not in failed
        assert ai_credits.balance_units(people[0][0]['id']) == 1000 * ai_credits.UNITS - 65100


def test_catalog_union_exclusion_persists_across_rooms_and_exhaustion(client, monkeypatch):
    users = [register(client)[0] for _ in range(2)]
    choices = catalog.all_puzzles()[:3]
    monkeypatch.setattr(catalog, 'all_puzzles', lambda: choices)
    first = catalog.draw_for_players([users[0]['id']], 'one')
    second = catalog.draw_for_players([users[1]['id']], 'two')
    # The combined room excludes both histories; all returned families are new to both.
    third = catalog.draw_for_players([u['id'] for u in users], 'three')
    assert third['family'] not in {first['family'], second['family']}
    conn = database.get_conn()
    for user in users:
        for puzzle in choices:
            conn.execute('INSERT OR IGNORE INTO turtle_soup_seen(user_id,puzzle_key,match_id) VALUES (?,?,?)',
                         (user['id'],puzzle['family'],'known'))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match='已玩过全部'):
        catalog.draw_for_players([u['id'] for u in users], 'exhausted')


def test_restore_interrupts_pending_without_paid_replay(client, monkeypatch):
    calls = provider(monkeypatch)
    user, _ = register(client)
    ai_credits.set_balance(user['id'], 100, user['id'])
    room = rm.create_room(user, None, None, 'turtle_soup')
    room['players'][0]['connected'] = True
    state = rm.start_game(room['code'], user['id'])
    soup.submit(state, user['id'], action(state))
    persistence.save_room(room)
    rm.restore_rooms()
    restored = rm.get_room(room['code'])
    host.recover(restored)
    assert restored['game_state']['requests'][0]['status']=='failed'
    assert restored['game_state']['pending_id'] is None
    assert not calls


def test_busy_and_duplicate_submission_issue_only_one_request(client, monkeypatch):
    entered, release = client.portal.call(asyncio.Event), client.portal.call(asyncio.Event)
    calls = provider(monkeypatch)
    original = httpx.AsyncClient.post
    async def delayed(self, url, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, url, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, 'post', delayed)
    with ExitStack() as stack:
        _, people, sockets = lobby(client, stack)
        state = start(sockets)[0]
        data = action(state)
        sockets[0].send_json(data)
        busy = [receive(socket, 'soup_state', lambda m: m['game']['busy'])['game'] for socket in sockets]
        client.portal.call(entered.wait)
        sockets[0].send_json(data)
        duplicate = receive(sockets[0], 'sync')
        assert len(duplicate['game']['requests'])==1
        sockets[1].send_json(action(busy[1]))
        assert '正在回答' in receive(sockets[1], 'error')['message']
        client.portal.call(release.set)
        receive(sockets[0], 'soup_state', lambda m: m['game']['requests'][0]['status']=='answered')
        assert len(calls)==1
        assert ai_credits.balance_units(people[0][0]['id'])==1000*ai_credits.UNITS-65100


def test_zero_can_make_last_call_then_negative_blocks_and_reveal_works(client, monkeypatch):
    calls = provider(monkeypatch)
    with ExitStack() as stack:
        _, people, sockets = lobby(client, stack, 1)
        state = start(sockets)[0]
        uid = people[0][0]['id']
        ai_credits.set_balance(uid, 0, uid)
        sockets[0].send_json(action(state))
        state = receive(sockets[0], 'soup_state', lambda m: not m['game']['ai_enabled'])['game']
        assert state['can_reveal'] and not state['can_submit']
        sockets[0].send_json(action(state))
        assert '积分不足' in receive(sockets[0], 'error')['message']
        assert len(calls)==1
        sockets[0].send_json({'type':'soup_reveal','match_id':state['match_id'],'revision':state['revision']})
        assert receive(sockets[0], 'game_over')['reason']=='revealed'
        assert len(calls)==1


def test_payer_departure_ends_match_and_late_response_charges_original_owner(client, monkeypatch):
    entered, release = client.portal.call(asyncio.Event), client.portal.call(asyncio.Event)
    provider(monkeypatch)
    original = httpx.AsyncClient.post
    async def delayed(self, url, **kwargs):
        entered.set()
        await release.wait()
        return await original(self, url, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, 'post', delayed)
    async def drain():
        await asyncio.gather(*list(host._tasks.values()))
    with ExitStack() as stack:
        code, people, sockets = lobby(client, stack)
        state = start(sockets)[0]
        sockets[0].send_json(action(state))
        client.portal.call(entered.wait)
        assert client.post(f'/api/rooms/{code}/leave', headers=auth(people[0][1])).status_code==200
        result = receive(sockets[1], 'game_over')
        assert result['reason']=='payer_left'
        assert rm.get_room(code)['host_id']==people[1][0]['id']
        client.portal.call(release.set)
        client.portal.call(drain)
        assert ai_credits.balance_units(people[0][0]['id'])==1000*ai_credits.UNITS-65100
        assert ai_credits.balance_units(people[1][0]['id'])==0
        assert rm.get_room(code)['game_state']['summary']['reason']=='payer_left'


def test_nonhost_reveal_and_outsider_actions_rejected(client, monkeypatch):
    provider(monkeypatch)
    with ExitStack() as stack:
        code, people, sockets = lobby(client, stack)
        state = start(sockets)[0]
        sockets[1].send_json({'type':'soup_reveal','match_id':state['match_id'],'revision':state['revision']})
        assert '只有房主' in receive(sockets[1], 'error')['message']
        outsider, token = register(client)
        assert client.post(f'/api/rooms/{code}/join', json={}, headers=auth(token)).status_code==409
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f'/ws/room/{code}?token={token}') as socket:
                socket.receive_json()
        assert rm.get_room(code)['status']=='playing'


def test_all_offline_while_queued_does_not_call_provider(client, monkeypatch):
    calls = provider(monkeypatch)
    async def hold(uid):
        lock=ai_service._host_lock(uid)
        await lock.acquire()
        return lock
    async def drain():
        await asyncio.gather(*list(host._tasks.values()))
    with ExitStack() as stack:
        code, people, sockets = lobby(client, stack, 1)
        state = start(sockets)[0]
        lock=client.portal.call(hold, people[0][0]['id'])
        sockets[0].send_json(action(state))
        receive(sockets[0], 'soup_state', lambda m:m['game']['busy'])
    client.portal.call(lock.release)
    client.portal.call(drain)
    assert not calls
    assert rm.get_room(code)['game_state']['requests'][0]['status']=='failed'
    assert ai_credits.balance_units(people[0][0]['id'])==1000*ai_credits.UNITS


def answer_turn(state, progress='off_track', **extra):
    assert soup.submit(state, 1, action(state)) == (None, True)
    soup.apply_response(state, judgment(state, solved=False, progress=progress, **extra))
    return state['requests'][-1]


def test_progressive_hints_cooldown_cap_and_private_ladder():
    state = engine()
    for index in range(12):
        request = answer_turn(state, hint=state['solution'])
        expected = index in (2, 5, 8)
        assert ('hint' in request) == expected
        if expected:
            assert request['hint'] == state['hints'][index // 3]
    assert state['hint_state']['used'] == 3
    public = soup.view_for(state, 2)
    assert not {'hints', 'hint_state', 'progress'} & public.keys()
    assert sum('hint' in r for r in public['requests']) == 3
    soup.finish(state, 'revealed')
    assert not {'hints', 'hint_state'} & soup.snapshot(state).keys()


def test_stalled_fallback_and_progress_resets_counter():
    state = engine()
    for _ in range(5):
        assert 'hint' not in answer_turn(state, 'stalled')
    assert 'hint' not in answer_turn(state, 'on_track')
    for _ in range(5):
        assert 'hint' not in answer_turn(state, 'stalled')
    assert 'hint' in answer_turn(state, 'stalled')


def test_no_hint_on_solve_failed_request_or_submission_limit():
    state = engine()
    for _ in range(2):
        answer_turn(state)
    soup.submit(state, 1, action(state))
    soup.fail_pending(state, '网络中断')
    assert state['hint_state']['used'] == 0
    soup.submit(state, 1, action(state, kind='guess'))
    soup.apply_response(state, judgment(state, progress='unclear'))
    assert 'hint' not in state['requests'][-1]
    assert state['reason'] == 'solved'


def test_catalog_has_reviewed_three_stage_hints():
    for puzzle in catalog.all_puzzles():
        assert len(puzzle['hints']) == 3
        assert len(set(puzzle['hints'])) == 3
        assert all(0 < len(hint) <= 100 for hint in puzzle['hints'])
