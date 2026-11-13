"""好友房间邀请：授权、房间现状校验、收件箱、忽略与失效。"""
import pytest

from app import database, room_manager as rm, security


@pytest.fixture(autouse=True)
def _clean_rooms():
    rm._rooms.clear()
    rm._dc_tasks.clear()
    rm._bot_tasks.clear()
    yield


@pytest.fixture
def client(ws_client, monkeypatch, tmp_path):
    monkeypatch.setattr(database, '_db_path', str(tmp_path / 'invites.db'))
    database.init_db(database._db_path)
    monkeypatch.setattr(security, '_jwt_secret', 'invites-test-key-that-is-at-least-32-bytes')
    return ws_client


def user(client, name):
    response = client.post('/api/auth/register', json={'username': name, 'password': 'test123456'})
    assert response.status_code == 200, response.text
    data = response.json()
    return {'id': data['user']['id'], 'username': name,
            'headers': {'Authorization': 'Bearer ' + data['token']}}


def people(client):
    return [user(client, name) for name in ('invite_alice', 'invite_bob', 'invite_carol')]


def friends(client, a, b):
    assert client.post('/api/social/requests', headers=a['headers'], json={'user_id': b['id']}).status_code == 200
    assert client.post(f"/api/social/requests/{a['id']}/accept", headers=b['headers']).status_code == 200


def room(client, actor, game='gomoku', password=None):
    body = {'game': game}
    if password is not None:
        body['password'] = password
    response = client.post('/api/rooms', headers=actor['headers'], json=body)
    assert response.status_code == 200, response.text
    return response.json()


def invite(client, actor, peer, code):
    return client.post('/api/social/invites', headers=actor['headers'],
                       json={'user_id': peer['id'], 'room_code': code})


def inbox(client, actor):
    response = client.get('/api/social', headers=actor['headers'])
    assert response.status_code == 200, response.text
    return response.json()


def summary(client, actor):
    response = client.get('/api/social/summary', headers=actor['headers'])
    assert response.status_code == 200, response.text
    return response.json()


def age_links():
    """把上次关系变更时间拨回一分钟前，绕开「再次申请至少间隔一分钟」的限流。"""
    conn = database.get_conn()
    try:
        conn.execute("UPDATE friend_links SET updated_at='2000-01-01T00:00:00.000+00:00'")
        conn.commit()
    finally:
        conn.close()


def test_invite_is_friend_only_and_appears_in_inbox_and_summary(client):
    a, b, c = people(client)
    created = room(client, a)
    assert invite(client, a, b, created['code']).status_code == 403
    friends(client, a, b)
    assert invite(client, a, b, created['code']).status_code == 200
    # 重复邀请幂等：同一房间对同一个人只保留一条待答记录。
    assert invite(client, a, b, created['code']).status_code == 200
    items = inbox(client, b)['invites']
    assert len(items) == 1
    assert items[0]['inviter']['id'] == a['id']
    assert items[0]['room'] == {'code': created['code'], 'name': created['name'],
                                'game': 'gomoku', 'status': 'waiting',
                                'players': 1, 'max_players': 2}
    assert summary(client, b)['invite_total'] == 1
    assert summary(client, a)['invite_total'] == 0
    # 不是好友的人看不到邀请，也不会有邀请计数。
    assert inbox(client, c)['invites'] == []
    assert invite(client, a, a, created['code']).status_code == 400


def test_invite_validation_rejects_unjoinable_rooms(client):
    a, b, c = people(client)
    friends(client, a, b)
    friends(client, a, c)
    created = room(client, a)
    # 受邀者已经在房间里
    assert client.post(f"/api/rooms/{created['code']}/join", headers=b['headers'], json={}).status_code == 200
    assert invite(client, a, b, created['code']).status_code == 409
    # 房号不存在 / 格式不对
    assert invite(client, a, c, 'ZZZZZZ').status_code == 404
    assert invite(client, a, c, 'ABC').status_code == 422
    # 发起人不在房间里
    other = room(client, c)
    assert invite(client, a, b, other['code']).status_code == 403

    password_room = room(client, a, password='secret')
    assert invite(client, a, c, password_room['code']).status_code == 409

    practice = client.post('/api/practice/uno', headers=a['headers'])
    assert practice.status_code == 200, practice.text
    assert invite(client, a, c, practice.json()['code']).status_code == 409

    challenge = client.post('/api/challenges/2048/start', headers=a['headers'])
    assert challenge.status_code == 200, challenge.text
    assert invite(client, a, c, challenge.json()['code']).status_code == 409

    # 房间满了
    full = room(client, a)
    assert client.post(f"/api/rooms/{full['code']}/join", headers=b['headers'], json={}).status_code == 200
    assert invite(client, a, c, full['code']).status_code == 409

    # 对局开始后不能再邀请
    playing = room(client, a)
    rm.get_room(playing['code'])['status'] = 'playing'
    assert invite(client, a, c, playing['code']).status_code == 409


def test_decline_join_and_room_disappearance_expire_invites(client):
    a, b, _ = people(client)
    friends(client, a, b)
    created = room(client, a)
    assert invite(client, a, b, created['code']).status_code == 200
    invite_id = inbox(client, b)['invites'][0]['id']

    # 只有受邀者本人能忽略；别人拿到 id 也没用。
    assert client.post(f'/api/social/invites/{invite_id}/decline', headers=a['headers']).status_code == 404
    assert client.post(f'/api/social/invites/{invite_id}/decline', headers=b['headers']).status_code == 200
    assert inbox(client, b)['invites'] == []
    assert summary(client, b)['invite_total'] == 0
    assert client.post(f'/api/social/invites/{invite_id}/decline', headers=b['headers']).status_code == 404

    # 再邀一次后真的进房：邀请从收件箱消失，join 仍走普通接口。
    assert invite(client, a, b, created['code']).status_code == 200
    assert client.post(f"/api/rooms/{created['code']}/join", headers=b['headers'], json={}).status_code == 200
    assert inbox(client, b)['invites'] == []
    assert len(inbox(client, b)['friends']) == 1

    # 房间解散：残留的待答邀请在下一次读取时失效，不会变成僵尸红点。
    second = room(client, a)
    assert invite(client, a, b, second['code']).status_code == 200
    rm._rooms.pop(second['code'])
    assert inbox(client, b)['invites'] == []
    assert summary(client, b)['invite_total'] == 0


def test_block_or_unfriend_hides_existing_invitation(client):
    a, b, _ = people(client)
    friends(client, a, b)
    created = room(client, a)
    assert invite(client, a, b, created['code']).status_code == 200
    assert client.post(f"/api/social/blocks/{a['id']}", headers=b['headers']).status_code == 200
    assert inbox(client, b)['invites'] == []
    assert summary(client, b)['invite_total'] == 0

    # 解除拉黑不会把旧邀请复活。
    assert client.delete(f"/api/social/blocks/{a['id']}", headers=b['headers']).status_code == 200
    assert inbox(client, b)['invites'] == []

    # 删除好友同样让待答邀请失效。
    age_links()
    friends(client, a, b)
    assert invite(client, a, b, created['code']).status_code == 200
    assert client.delete(f"/api/social/friends/{b['id']}", headers=a['headers']).status_code == 200
    assert inbox(client, b)['invites'] == []