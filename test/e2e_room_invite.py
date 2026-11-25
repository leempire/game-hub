"""好友房间邀请：真实浏览器 + 临时后端。

两条路径都验：受邀者点「加入房间」直接进房（走普通 join），或点「忽略」把邀请消掉。
Run: python test/e2e_room_invite.py
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import requests
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
WEB = os.environ.get('GAMEHUB_WEB_URL', 'http://localhost:50540')


def serve(port, database):
    sys.path.insert(0, str(ROOT / 'server'))
    from app.cfg import config
    import uvicorn
    config.update(db_path=database, admin={}, jwt_secret='isolated-room-invite-jwt-key-at-least-32-bytes')
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def wait_for(check, timeout=20, page=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        if page is None:
            time.sleep(0.1)
        else:
            page.wait_for_timeout(100)
    raise AssertionError('condition did not become true')


def headers(auth):
    return {'Authorization': 'Bearer ' + auth['token']}


def context(browser, api, auth, width=390):
    ctx = browser.new_context(viewport={'width': width, 'height': 844})
    ctx.route('**/api/**', lambda route: route.continue_(
        url=api + urlsplit(route.request.url).path +
        ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
    ctx.add_init_script('''const OriginalSocket = window.WebSocket;
      window.WebSocket = class extends OriginalSocket { constructor(url, protocols) {
        const parsed = new URL(url);
        super(parsed.pathname.startsWith('/ws/') ? %s + parsed.pathname + parsed.search : url, protocols);
      }};''' % json.dumps(api.replace('http://', 'ws://')))
    ctx.add_init_script('localStorage.setItem("gamehub_token", %s);localStorage.setItem("gamehub_user", %s);'
                        % (json.dumps(auth['token']), json.dumps(json.dumps(auth['user']))))
    return ctx


def inbox(api, auth):
    response = requests.get(api + '/api/social', headers=headers(auth))
    response.raise_for_status()
    return response.json()


def checks(api):
    accounts = []
    for name in ('invite_host', 'invite_friend'):
        response = requests.post(api + '/api/auth/register', json={'username': name, 'password': 'test123456'})
        response.raise_for_status()
        accounts.append(response.json())
    alice, bob = accounts
    aid, bid = alice['user']['id'], bob['user']['id']
    assert requests.post(api + '/api/social/requests', headers=headers(alice),
                         json={'user_id': bid}).status_code == 200
    assert requests.post(api + f'/api/social/requests/{aid}/accept', headers=headers(bob)).status_code == 200

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ca, cb = context(browser, api, alice), context(browser, api, bob)
        a, b = ca.new_page(), cb.new_page()

        created = requests.post(api + '/api/rooms', headers=headers(alice),
                                json={'game': 'gomoku', 'name': '周末房'}).json()
        code = created['code']
        a.goto(WEB + f'/room/{code}')
        expect(a.get_by_test_id('room-invite-open')).to_be_visible(timeout=25000)
        a.get_by_test_id('room-invite-open').click()
        expect(a.get_by_test_id(f'invite-friend-{bid}')).to_be_visible(timeout=15000)
        a.get_by_test_id(f'invite-friend-{bid}').click()
        expect(a.get_by_test_id(f'invite-friend-{bid}')).to_contain_text('已邀请')
        wait_for(lambda: inbox(api, bob)['invites'])
        invite = inbox(api, bob)['invites'][0]
        assert invite['room']['code'] == code and invite['room']['name'] == '周末房'
        assert invite['inviter']['id'] == aid
        print('[01] room member invites a friend from the lobby', flush=True)

        b.goto(WEB + '/social')
        card = b.get_by_test_id(f'social-invite-{invite["id"]}')
        expect(card).to_be_visible(timeout=20000)
        expect(card).to_contain_text('周末房')
        expect(card).to_contain_text('五子棋')
        b.get_by_test_id(f'social-invite-join-{invite["id"]}').click()
        expect(b.get_by_text('玩家 (2/2)', exact=True)).to_be_visible(timeout=25000)
        wait_for(lambda: len(requests.get(api + f'/api/rooms/{code}',
                                          headers=headers(alice)).json()['players']) == 2)
        assert inbox(api, bob)['invites'] == []
        print('[02] invitee joins through the normal room path and the invitation clears', flush=True)

        second = requests.post(api + '/api/rooms', headers=headers(alice),
                               json={'game': 'gomoku', 'name': '再来一局'}).json()
        a.goto(WEB + f'/room/{second["code"]}')
        expect(a.get_by_test_id('room-invite-open')).to_be_visible(timeout=25000)
        a.get_by_test_id('room-invite-open').click()
        a.get_by_test_id(f'invite-friend-{bid}').click()
        wait_for(lambda: inbox(api, bob)['invites'])
        pending = inbox(api, bob)['invites'][0]
        b.goto(WEB + '/social')
        expect(b.get_by_test_id(f'social-invite-{pending["id"]}')).to_be_visible(timeout=20000)
        for width in (320, 360, 390):
            b.set_viewport_size({'width': width, 'height': 780})
            expect(b.get_by_test_id(f'social-invite-{pending["id"]}')).to_be_visible()
            assert b.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            b.screenshot(path=str(ROOT / f'tmp/checks/room-invite-{width}.png'))
        b.get_by_test_id(f'social-invite-decline-{pending["id"]}').click()
        expect(b.get_by_test_id(f'social-invite-{pending["id"]}')).to_have_count(0, timeout=15000)
        wait_for(lambda: inbox(api, bob)['invites'] == [])
        summary = requests.get(api + '/api/social/summary', headers=headers(bob)).json()
        assert summary['invite_total'] == 0
        print('[03] ignore clears the invite, badge count and narrow layouts', flush=True)
        browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix='gamehub-room-invite-') as directory:
        database = str(Path(directory) / 'invite.db')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        api = f'http://127.0.0.1:{port}'
        log_path = ROOT / 'tmp/log/room-invite-browser-backend.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        (ROOT / 'tmp/checks').mkdir(parents=True, exist_ok=True)
        with log_path.open('w') as log:
            process = subprocess.Popen([sys.executable, __file__, '--serve', str(port), database],
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                wait_for(lambda: process.poll() is None and ready(api))
                checks(api)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def ready(api):
    try:
        return requests.get(api + '/api/app/version', timeout=0.3).ok
    except requests.RequestException:
        return False


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--serve':
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        main()