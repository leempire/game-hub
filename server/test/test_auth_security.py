"""账号安全：token 吊销（改密码 / 封号）、登录限流、输入长度。

原来 token 只验签不查库：改了密码、被封了号，手里的旧 token 照样能用到 7 天
过期；改密码页还写着「密码改了，手里这张 token 就不该再继续用」，服务端却没兑现。

Run: pytest test/test_auth_security.py -v
"""

import os
import sys
import time

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import room_manager as rm
from app import security
from app.database import get_conn
from app.routers import auth as auth_router

PWD = "test123456"


@pytest.fixture(autouse=True)
def _clean():
    rm._rooms.clear()
    rm._dc_tasks.clear()
    rm._bot_tasks.clear()
    auth_router._login_fails.clear()
    yield
    auth_router._login_fails.clear()


@pytest.fixture
def client(ws_client):
    return ws_client


_seq = [0]


def _name(prefix):
    _seq[0] += 1
    return f"{prefix}{int(time.time() * 1000) % 100000}_{_seq[0]}"


def _register(client, username, password=PWD):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    d = r.json()
    return d["token"], d["user"]["id"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _login(client, username, password=PWD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _make_admin(client):
    token, uid = _register(client, _name("adm"))
    conn = get_conn()
    try:
        conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()
    return token


class TestPasswordChangeRevokes:
    def test_old_token_rejected_after_change(self, client):
        name = _name("pw")
        old, _ = _register(client, name)
        other = _login(client, name).json()["token"]      # 另一台设备上的会话
        r = client.post("/api/auth/password", headers=_h(old),
                        json={"old_password": PWD, "new_password": "newpass123"})
        assert r.status_code == 200
        assert client.get("/api/auth/me", headers=_h(old)).status_code == 401
        assert client.get("/api/auth/me", headers=_h(other)).status_code == 401, \
            "别的设备上的旧会话也必须作废（改密码就是为了把别人赶出去）"
        new = _login(client, name, "newpass123").json()["token"]
        assert client.get("/api/auth/me", headers=_h(new)).status_code == 200

    def test_old_token_cannot_open_websocket(self, client):
        token, _ = _register(client, _name("pww"))
        code = client.post("/api/rooms", json={"game": "gomoku"}, headers=_h(token)).json()["code"]
        client.post("/api/auth/password", headers=_h(token),
                    json={"old_password": PWD, "new_password": "newpass123"})
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(f"/ws/room/{code}?token={token}") as ws:
                ws.receive_json()
        assert e.value.code == 4001

    def test_new_password_length_enforced(self, client):
        token, _ = _register(client, _name("pwl"))
        for bad in ("", "12345", "x" * 33):
            r = client.post("/api/auth/password", headers=_h(token),
                            json={"old_password": PWD, "new_password": bad})
            assert r.status_code == 400, bad
        assert client.get("/api/auth/me", headers=_h(token)).status_code == 200, \
            "改密失败不能顺带把 token 弄失效"

    def test_legacy_token_without_stamp_still_works(self, client):
        """上线前签出的 token 没有 `pv`：放行到自然过期，不然一上线全员掉登录。"""
        _, uid = _register(client, _name("leg"))
        legacy = jwt.encode({"user_id": uid, "exp": int(time.time()) + 3600},
                            security._jwt_secret, algorithm="HS256")
        assert client.get("/api/auth/me", headers=_h(legacy)).status_code == 200


class TestBan:
    def test_ban_revokes_token_and_kicks_live_socket(self, client):
        admin = _make_admin(client)
        token, uid = _register(client, _name("ban"))
        code = client.post("/api/rooms", json={"game": "gomoku"}, headers=_h(token)).json()["code"]
        with client.websocket_connect(f"/ws/room/{code}?token={token}") as ws:
            assert ws.receive_json()["type"] == "sync"
            r = client.post(f"/api/admin/users/{uid}/ban", headers=_h(admin), json={"banned": True})
            assert r.status_code == 200 and r.json()["banned"] is True
            with pytest.raises(WebSocketDisconnect) as e:
                for _ in range(5):
                    ws.receive_json()
            assert e.value.code == 4001, "封号要当场断开已连着的 WS，并用 4001 让客户端别重连"
        assert client.get("/api/auth/me", headers=_h(token)).status_code == 401
        assert client.get("/api/rooms", headers=_h(token)).status_code == 401

    def test_unban_restores_access(self, client):
        admin = _make_admin(client)
        token, uid = _register(client, _name("unb"))
        client.post(f"/api/admin/users/{uid}/ban", headers=_h(admin), json={"banned": True})
        assert client.get("/api/auth/me", headers=_h(token)).status_code == 401
        client.post(f"/api/admin/users/{uid}/ban", headers=_h(admin), json={"banned": False})
        assert client.get("/api/auth/me", headers=_h(token)).status_code == 200


class TestLoginThrottle:
    def test_locks_after_repeated_failures(self, client):
        name = _name("thr")
        _register(client, name)
        for _ in range(auth_router.LOGIN_MAX_FAILS):
            assert _login(client, name, "wrongpass").status_code == 401
        assert _login(client, name).status_code == 429, \
            "连错上限次之后，连正确密码也要先拒掉"
        other = _name("thr2")                 # 别的账号不受影响
        _register(client, other)
        assert _login(client, other).status_code == 200

    def test_success_resets_counter(self, client):
        name = _name("ths")
        _register(client, name)
        for _ in range(2):
            for _ in range(auth_router.LOGIN_MAX_FAILS - 1):
                _login(client, name, "wrongpass")
            assert _login(client, name).status_code == 200

    def test_window_expires(self, client, monkeypatch):
        name = _name("thw")
        _register(client, name)
        for _ in range(auth_router.LOGIN_MAX_FAILS):
            _login(client, name, "wrongpass")
        assert _login(client, name).status_code == 429
        real = time.time
        monkeypatch.setattr(auth_router.time, "time",
                            lambda: real() + auth_router.LOGIN_WINDOW_S + 1)
        assert _login(client, name).status_code == 200


class TestInputLimits:
    def test_nickname_length_and_blank(self, client):
        token, _ = _register(client, _name("nk"))
        assert client.put("/api/auth/me", headers=_h(token),
                          json={"nickname": "长" * 17}).status_code == 400
        r = client.put("/api/auth/me", headers=_h(token), json={"nickname": "  小明  "})
        assert r.status_code == 200 and r.json()["nickname"] == "小明"
        r = client.put("/api/auth/me", headers=_h(token), json={"nickname": "   "})
        assert r.status_code == 200 and r.json()["nickname"] is None

    def test_room_name_and_password_length(self, client):
        token, _ = _register(client, _name("rn"))
        assert client.post("/api/rooms", headers=_h(token),
                           json={"game": "gomoku", "name": "房" * 21}).status_code == 400
        assert client.post("/api/rooms", headers=_h(token),
                           json={"game": "gomoku", "password": "p" * 33}).status_code == 400
        r = client.post("/api/rooms", headers=_h(token), json={"game": "gomoku", "name": "   "})
        assert r.status_code == 200 and r.json()["name"].endswith("的房间")
