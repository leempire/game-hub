"""AI money boundaries, admin permissions, provider usage and private bot actions."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from app import ai_credits, ai_service, database, room_manager as rm, room_timing, undercover_agent, undercover_bots
from app.game import undercover
from app.security import create_token


def _run_async(coroutine):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(database, '_db_path', str(tmp_path / 'ai.db'))
    database.init_db(database._db_path)
    conn = database.get_conn()
    for uid, role in ((1, 'admin'), (2, 'user')):
        conn.execute('''INSERT INTO users(id,username,password_hash,salt,role,created_at)
                        VALUES (?,?,?,'salt',?,'now')''', (uid, f'user{uid}', 'hash', role))
    conn.commit()
    conn.close()
    monkeypatch.setenv('ARK_AGENT_API_KEY', 'test-only')
    monkeypatch.setattr(rm, '_rooms', {})
    monkeypatch.setattr(room_timing, '_tasks', {})
    monkeypatch.setattr(room_timing, '_anchors', {})
    monkeypatch.setattr(undercover_bots, '_tasks', {})
    monkeypatch.setattr(undercover_bots, '_closing', False)
    return 1


def _room():
    room = rm.create_room({'id': 1, 'username': 'user1', 'nickname': 'Host'}, None, None, 'undercover')
    ai_credits.set_balance(1, 100, 1)
    room['players'][0]['connected'] = True
    for _ in range(3):
        rm.add_bot(room['code'], 1)
    return room


def test_migration_and_exact_accounting(account):
    database.init_db(database._db_path)
    assert ai_credits.balance_units(1) == 0
    ai_credits.set_balance(1, Decimal('1'), 1)
    assert ai_credits.begin_call('operation', 1, 'model')
    assert not ai_credits.begin_call('operation', 1, 'model')
    usage = {'prompt_tokens': 1_000_000, 'completion_tokens': 1_000_000,
             'prompt_tokens_details': {'cached_tokens': 500_000}}
    ai_credits.settle_call('operation', usage)
    expected = Decimal('1') - Decimal('2') - Decimal('100') - Decimal('800')
    assert ai_credits.balance_units(1) == int(expected * ai_credits.UNITS)
    ai_credits.settle_call('operation', usage)
    assert ai_credits.balance_units(1) == int(expected * ai_credits.UNITS)
    with pytest.raises(ValueError, match='积分不足'):
        ai_credits.begin_call('next', 1, 'model')
    ai_credits.set_balance(1, 0, 1)
    assert ai_credits.begin_call('at-zero', 1, 'model')


@pytest.mark.parametrize('usage', [
    {'prompt_tokens': -1, 'completion_tokens': 1},
    {'prompt_tokens': 1, 'completion_tokens': True},
    {'prompt_tokens': 1, 'completion_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 2}},
])
def test_bad_usage_is_not_charged(account, usage):
    ai_credits.begin_call('bad', 1, 'model')
    with pytest.raises(ValueError):
        ai_credits.settle_call('bad', usage)
    assert ai_credits.balance_units(1) == 0


def test_start_gate_only_paid_bots(account, monkeypatch):
    room = _room()
    for amount in ('0', '99.999996', '-1'):
        ai_credits.set_balance(1, amount, 1)
        with pytest.raises(ValueError, match='至少为 100'):
            rm.start_game(room['code'], 1)
        assert room['status'] == 'waiting'
    ai_credits.set_balance(1, 100, 1)
    gs = rm.start_game(room['code'], 1)
    assert gs['ai_owner_id'] == 1
    assert len({p['username'] for p in room['players']}) == 4
    assert room_timing.undercover_online(room)
    room['players'][0]['connected'] = False
    assert not room_timing.undercover_online(room)
    # A human-only room does not need either a credential or points.
    room['status'] = 'waiting'
    for index, player in enumerate(room['players']):
        player.update(is_bot=False, connected=True, id=index+1)
    monkeypatch.delenv('ARK_AGENT_API_KEY')
    ai_credits.set_balance(1, -1, 1)
    assert rm.start_game(room['code'], 1)


def test_admin_api_permissions_and_self_balance(account, ws_client):
    admin = {'Authorization': 'Bearer ' + create_token(1, 'salt')}
    user = {'Authorization': 'Bearer ' + create_token(2, 'salt')}
    path = '/api/admin/users/2/ai-credits'
    assert ws_client.post(path, json={'ai_credits': 2}, headers=user).status_code == 403
    assert ws_client.post(path, json={'ai_credits': '123.456788'}, headers=admin).json()['ai_credits'] == 123.456788
    assert ws_client.get('/api/auth/me', headers=user).json()['ai_credits'] == 123.456788
    for value in ('NaN', 'Infinity', '0.00000001', '99999999999999999999'):
        assert ws_client.post(path, json={'ai_credits': value}, headers=admin).status_code in (400, 422)
    assert ws_client.post('/api/admin/users/999/ai-credits', json={'ai_credits': 1}, headers=admin).status_code == 404


def _mock_provider(monkeypatch, content='{"text":"经常出现在日常生活里"}'):
    payloads = []

    async def post(self, url, **kwargs):
        payloads.append((url, kwargs['json']))
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'usage': {'prompt_tokens': 100, 'completion_tokens': 10,
                      'prompt_tokens_details': {'cached_tokens': 50}},
            'choices': [{'message': {'content': content(kwargs['json']) if callable(content) else content}}],
        })
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    return payloads


def test_adapter_low_and_charge_before_parse(account, monkeypatch):
    payloads = _mock_provider(monkeypatch, 'invalid json')
    with pytest.raises(ValueError):
        _run_async(undercover_agent.decide({'your_word': '雨伞'}, 1, 'invalid'))
    assert ai_credits.balance_units(1) == -(50 + 50*50 + 10*200)
    url, payload = payloads[0]
    assert url == ai_service.BASE_URL + '/chat/completions'
    assert payload['reasoning_effort'] == 'low'
    assert payload['model'] == 'deepseek-v4.1-flash-economy'


def test_worker_clue_privacy_and_vote(account, monkeypatch):
    ai_credits.set_balance(1, 100, 1)
    room = _room()
    gs = rm.start_game(room['code'], 1)
    bot = room['players'][1]['id']
    gs['pending_speakers'] = [bot]
    def respond(payload):
        view = json.loads(payload['messages'][1]['content'])
        return json.dumps({'text': '经常出现在日常生活里'} if view['can_clue']
                          else {'target_id': view['candidate_ids'][0]})
    payloads = _mock_provider(monkeypatch, respond)
    updates, errors = [], []
    _run_async(undercover_bots._run(room, updates.append, lambda uid, msg: errors.append(msg)))
    assert updates
    sent = json.loads(payloads[0][1]['messages'][1]['content'])
    assert sent['your_word'] == undercover.view_for(gs, bot)['your_word']
    assert not {'roles', 'word_pair', 'votes', 'ai_owner_id'} & sent.keys()
    assert gs['phase'] == 'vote'
    assert len(gs['votes']) == 3
    assert not errors


def test_stale_answer_charged_but_not_applied(account, monkeypatch):
    ai_credits.set_balance(1, 100, 1)
    payloads = _mock_provider(monkeypatch)
    async def scenario():
        assert await undercover_agent.decide({}, 1, 'once', lambda: False) is None
        assert not payloads
        await undercover_agent.decide({}, 1, 'once')
        assert await undercover_agent.decide({}, 1, 'once') is None
        assert len(payloads) == 1
    _run_async(scenario())


def test_same_owner_serialized_and_negative_stops_next(account, monkeypatch):
    _mock_provider(monkeypatch)
    async def scenario():
        results = await asyncio.gather(
            undercover_agent.decide({}, 1, 'first'),
            undercover_agent.decide({}, 1, 'second'), return_exceptions=True)
        assert sum(isinstance(result, ValueError) for result in results) == 1
    _run_async(scenario())


def test_common_service_registration_and_local_bots_exempt(account, monkeypatch):
    local = rm.create_room({'id': 1, 'username': 'user1', 'nickname': None}, None, None, 'gomoku')
    rm.add_bot(local['code'], 1)
    monkeypatch.delenv('ARK_AGENT_API_KEY')
    ai_credits.set_balance(1, -100, 1)
    ai_service.require_game_start(local)
    monkeypatch.setattr(ai_service, 'PAID_BOT_GAMES', frozenset({'gomoku'}))
    with pytest.raises(ValueError, match='未配置'):
        ai_service.require_game_start(local)
    monkeypatch.setenv('ARK_AGENT_API_KEY', 'test-only')
    with pytest.raises(ValueError, match='至少为 100'):
        ai_service.require_game_start(local)


def test_timeout_usage_unknown_and_no_replay(account, monkeypatch):
    calls = []
    async def timeout(self, url, **kwargs):
        calls.append(url)
        raise httpx.ReadTimeout('mock timeout')
    monkeypatch.setattr(httpx.AsyncClient, 'post', timeout)
    async def scenario():
        with pytest.raises(httpx.ReadTimeout):
            await ai_service.call_json([], 1, 'timeout')
        assert await ai_service.call_json([], 1, 'timeout') is None
    _run_async(scenario())
    assert len(calls) == 1
    assert ai_credits.balance_units(1) == 0
    conn = database.get_conn()
    assert conn.execute('SELECT status FROM ai_usage').fetchone()[0] == 'unknown'
    conn.close()


def test_bot_answer_expired_while_waiting_is_billed_only(account, monkeypatch):
    ai_credits.set_balance(1, 100, 1)
    room = _room()
    gs = rm.start_game(room['code'], 1)
    bot = room['players'][1]['id']
    gs['pending_speakers'] = [bot, 1]
    old_phase = gs['phase_id']
    original_post = httpx.AsyncClient.post
    _mock_provider(monkeypatch)
    fake_post = httpx.AsyncClient.post
    async def expire(self, url, **kwargs):
        response = await fake_post(self, url, **kwargs)
        gs['remaining_ms'] = 0
        rm.expire_undercover(room)
        return response
    monkeypatch.setattr(httpx.AsyncClient, 'post', expire)
    updates = []
    _run_async(undercover_bots._run(room, updates.append, lambda uid, msg: None))
    assert gs['phase_id'] > old_phase
    assert gs['clues'][-1]['text'] is None
    assert ai_credits.balance_units(1) < 100 * ai_credits.UNITS
    assert not updates
    monkeypatch.setattr(httpx.AsyncClient, 'post', original_post)


def test_eliminated_ai_observer_resumes_but_bots_alone_pause(account, monkeypatch):
    ai_credits.set_balance(1, 100, 1)
    room = _room()
    gs = rm.start_game(room['code'], 1)
    gs['alive'].remove(1)
    gs['pending_speakers'] = [uid for uid in gs['pending_speakers'] if uid != 1]
    assert room_timing.undercover_online(room)
    room['players'][0]['connected'] = False
    assert not room_timing.undercover_online(room)
    resumed = []
    monkeypatch.setattr(room_timing, 'reset_deadline', lambda room: resumed.append(True))
    assert rm.handle_player_reconnect(room['code'], 1)
    assert resumed


def test_worker_repeat_after_restore_never_replays(account, monkeypatch):
    ai_credits.set_balance(1, 100, 1)
    room = _room()
    gs = rm.start_game(room['code'], 1)
    bot = room['players'][1]['id']
    gs['pending_speakers'] = [bot, 1]
    # Invalid clue still used tokens, gets one attempt and waits for normal timeout.
    payloads = _mock_provider(monkeypatch, '{"text":""}')
    for _ in range(2):
        _run_async(undercover_bots._run(room, lambda room: None, lambda uid, msg: None))
    assert len(payloads) == 1
    assert gs['pending_speakers'][0] == bot



def test_graceful_shutdown_finishes_inflight_billing(account, monkeypatch):
    ai_credits.set_balance(1, 100, 1)
    room = _room()
    gs = rm.start_game(room['code'], 1)
    bot = room['players'][1]['id']
    gs['pending_speakers'] = [bot, 1]
    _mock_provider(monkeypatch)
    fake_post = httpx.AsyncClient.post
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(self, url, **kwargs):
            entered.set()
            await release.wait()
            return await fake_post(self, url, **kwargs)
        monkeypatch.setattr(httpx.AsyncClient, 'post', delayed)
        undercover_bots.schedule(room, lambda room: None, lambda uid, msg: None)
        await entered.wait()
        stopping = asyncio.create_task(undercover_bots.shutdown())
        await asyncio.sleep(0)
        release.set()
        await stopping
        assert not undercover_bots._tasks
    _run_async(scenario())
    assert ai_credits.balance_units(1) < 100 * ai_credits.UNITS
    assert gs['pending_speakers'][0] == 1



def test_add_paid_bot_low_credit_message(account):
    room = rm.create_room({'id': 1, 'username': 'user1', 'nickname': None}, None, None, 'undercover')
    with pytest.raises(ValueError, match='^AI积分不足$'):
        rm.add_bot(room['code'], 1)
    assert len(room['players']) == 1
    ai_credits.set_balance(1, 100, 1)
    rm.add_bot(room['code'], 1)
    assert len(room['players']) == 2


def test_undercover_agent_private_plan_never_becomes_public(monkeypatch):
    captured=[]
    async def call(messages,*args):
        captured.append(json.loads(messages[1]['content']))
        return {'plan':{'camp':'undercover','civilian_word':'雨伞'},'text':'下雨出门会带上','secret':'hidden'}
    monkeypatch.setattr(ai_service,'call_json',call)
    view={'your_id':1,'your_word':'雨衣','can_clue':True,'roles':{1:'spy'},'words':['雨伞','雨衣'],
          'clues':[{'round':1,'user_id':2,'text':'手里举着','private_word':'雨伞'}]}
    result=_run_async(undercover_agent.decide(view,1,'test-operation'))
    assert result=={'text':'下雨出门会带上'}
    assert 'roles' not in captured[0] and 'words' not in captured[0]
    assert 'private_word' not in captured[0]['clues'][0]
