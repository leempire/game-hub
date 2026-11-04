"""Friend requests and durable private chat through real mobile browser UI.

Uses a temporary backend/database and existing web server; never restarts services.
Run: python test/e2e_contacts.py
"""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
WEB = os.environ.get('GAMEHUB_WEB_URL', 'http://localhost:50540')


def serve(port, database):
    sys.path.insert(0, str(ROOT / 'server'))
    from app.cfg import config
    import uvicorn
    config.update(db_path=database, admin={}, jwt_secret='isolated-contacts-test-jwt-key-at-least-32-bytes')
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def wait_for(check, timeout=15, page=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        if page is None: time.sleep(0.1)
        else: page.wait_for_timeout(100)
    raise AssertionError('condition did not become true')


def headers(auth):
    return {'Authorization': 'Bearer ' + auth['token']}


def context(browser, api, auth, width=360):
    ctx = browser.new_context(viewport={'width': width, 'height': 780})
    ctx.route('**/api/**', lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
              ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
    ctx.add_init_script('localStorage.setItem("gamehub_token", %s);localStorage.setItem("gamehub_user", %s);'
                        % (json.dumps(auth['token']), json.dumps(json.dumps(auth['user']))))
    return ctx


def seed_messages(database, sender, peer, count):
    # Dataset fixture only: exercise real API pagination without sleeping through burst limits.
    now = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec='milliseconds')
    with sqlite3.connect(database) as conn:
        start = conn.execute('SELECT COALESCE(MAX(id),0) FROM direct_messages').fetchone()[0]
        for index in range(count):
            conn.execute('''INSERT INTO direct_messages
                (sender_id,recipient_id,user_low,user_high,client_id,body,created_at) VALUES(?,?,?,?,?,?,?)''',
                         (sender, peer, min(sender, peer), max(sender, peer), f'fixture_{start}_{index}',
                          f'历史消息 {start}-{index}', now))
        return [row[0] for row in conn.execute('SELECT id FROM direct_messages WHERE id>? ORDER BY id', (start,))]


def checks(api, database):
    auth = []
    for name in ('contacts_alice', 'contacts_bob', 'contacts_outsider'):
        response = requests.post(api + '/api/auth/register', json={'username': name, 'password': 'test123456'})
        response.raise_for_status()
        auth.append(response.json())
    alice, bob, stranger = auth
    aid, bid = alice['user']['id'], bob['user']['id']
    inbox = lambda account: requests.get(api + '/api/social', headers=headers(account)).json()
    history = lambda account, peer: requests.get(api + f'/api/social/chats/{peer}', headers=headers(account)).json()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ca, cb = context(browser, api, alice), context(browser, api, bob)
        a, b = ca.new_page(), cb.new_page()
        def wait_ui(check, timeout=15): return wait_for(check, timeout, page=a)
        a.goto(WEB + '/social'); b.goto(WEB + '/social')
        expect(a.get_by_test_id('social-add-friend')).to_be_visible()
        # The requested navigation order must hold on a narrow viewport.
        labels = [a.get_by_text(name, exact=True).last.bounding_box() for name in ('房间', '社交', '我的')]
        assert labels[0]['x'] < labels[1]['x'] < labels[2]['x']
        a.get_by_test_id('social-add-friend').click()
        a.get_by_test_id('social-search-input').fill(bob['user']['username'])
        a.get_by_test_id('social-search-submit').click()
        a.get_by_test_id(f'social-search-action-{bid}').click()
        wait_ui(lambda: any(x['id'] == bid for x in inbox(alice)['outgoing']))
        b.get_by_test_id('social-segment-requests').click()
        b.get_by_test_id(f'social-accept-{aid}').click()
        wait_ui(lambda: len(inbox(alice)['friends']) == 1)
        print('[01] four tabs in order; exact search, request and explicit acceptance', flush=True)

        # Return through the real tab route to close the search modal and open a chat.
        a.goto(WEB + '/social')
        a.get_by_test_id('social-segment-friends').click()
        a.get_by_test_id(f'social-chat-{bid}').click()
        text = '<b>你好</b>\n一起开局吗？'
        a.get_by_test_id('chat-input').fill(text)
        a.get_by_test_id('chat-send').click()
        wait_ui(lambda: len(history(alice, bid)['items']) == 1)
        sent = history(alice, bid)['items'][0]
        expect(a.get_by_test_id(f'chat-message-{sent["id"]}')).to_contain_text('你好')
        wait_ui(lambda: inbox(bob)['unread_total'] == 1)
        expect(b.locator('[aria-label="社交，1条未读或好友申请"]')).to_be_visible(timeout=15000)
        b.get_by_test_id('social-segment-friends').click()
        b.get_by_test_id(f'social-chat-{aid}').click()
        expect(b.get_by_test_id(f'chat-message-{sent["id"]}')).to_contain_text('<b>你好</b>')
        wait_ui(lambda: history(alice, bid)['peer_read_through'] >= sent['id'])
        expect(a.get_by_test_id(f'chat-message-{sent["id"]}')).to_contain_text('已读', timeout=15000)
        assert b.locator('b').count() == 0, 'message text must never become HTML'
        a.screenshot(path=str(ROOT / 'tmp/checks/contacts-chat-360.png'))
        print('[02] real text send/receive, unread and read receipt, plain text rendering', flush=True)

        # Persisted history survives closing the client. Initial page is bounded; older pages load.
        cb.close()
        ids = seed_messages(database, aid, bid, 65)
        wait_ui(lambda: inbox(bob)['unread_total'] == 65)
        cb = context(browser, api, bob)
        b = cb.new_page(); b.goto(WEB + f'/chat/{aid}')
        expect(b.get_by_test_id(f'chat-message-{ids[-1]}')).to_be_visible(timeout=15000)
        b.get_by_test_id('chat-load-older').click()
        expect(b.get_by_test_id(f'chat-message-{sent["id"]}')).to_contain_text('你好')
        b.reload()
        expect(b.get_by_test_id(f'chat-message-{ids[-1]}')).to_be_visible(timeout=15000)
        if b.get_by_test_id('chat-jump-latest').is_visible():
            b.get_by_test_id('chat-jump-latest').click()
        expect(b.get_by_test_id(f'chat-message-{ids[-1]}')).to_be_in_viewport(timeout=15000)
        print('[03] offline unread, deep link/reload persistence and older-page history', flush=True)

        observed = set()
        def capture(response):
            if f'/api/social/chats/{aid}?' in response.url and 'after=' in response.url and response.status == 200:
                observed.update(item['id'] for item in response.json()['items'])
        b.on('response', capture)
        backlog = seed_messages(database, aid, bid, 130)
        try:
            expect(b.get_by_test_id(f'chat-message-{backlog[-1]}')).to_be_in_viewport(timeout=20000)
        except AssertionError:
            b.screenshot(path=str(ROOT / 'tmp/checks/contacts-scroll-failure.png'))
            print('SCROLL_DIAGNOSTIC', b.evaluate("""id => {
                const target=document.querySelector(`[data-testid="chat-message-${id}"]`);
                const rows=[];let node=target;
                while(node){const box=node.getBoundingClientRect();const css=getComputedStyle(node);
                  rows.push({tag:node.tagName,id:node.dataset.testid,y:box.y,height:box.height,
                    scrollTop:node.scrollTop,scrollHeight:node.scrollHeight,clientHeight:node.clientHeight,
                    overflow:css.overflowY});node=node.parentElement;}
                return {rows,innerHeight,scrollY};
            }""", backlog[-1]), flush=True)
            raise
        assert set(backlog).issubset(observed), 'incremental pages must not skip a backlog over 100'
        print('[04] incremental polling drains more than one page without skipping messages', flush=True)

        # A rejected send retains the draft and reuses its nonce on the explicit retry.
        attempts = []
        def reject_once(route):
            if route.request.method == 'POST':
                attempts.append(route.request.post_data_json['client_id'])
                if len(attempts) == 1:
                    route.fulfill(status=503, json={'detail': '临时不可用，请重试'})
                    return
            route.fallback()
        a.route(f'**/api/social/chats/{bid}', reject_once)
        a.get_by_test_id('chat-input').fill('失败后保留内容')
        a.get_by_test_id('chat-send').click()
        expect(a.get_by_test_id('chat-send-error')).to_be_visible()
        expect(a.get_by_test_id('chat-input')).to_have_value('失败后保留内容')
        a.get_by_test_id('chat-send').click()
        expect(a.get_by_test_id('chat-input')).to_have_value('')
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        a.unroute(f'**/api/social/chats/{bid}', reject_once)

        long_text = '长消息' * 100 + 'A' * 500
        a.set_viewport_size({'width': 320, 'height': 780})
        a.get_by_test_id('chat-input').fill(long_text)
        a.get_by_test_id('chat-send').click()
        expect(a.get_by_test_id('chat-input')).to_have_value('')
        assert a.evaluate('document.documentElement.scrollWidth <= innerWidth')
        long_id = next(item['id'] for item in history(alice, bid)['items'] if item['body'] == long_text)
        long_row = a.get_by_test_id(f'chat-message-{long_id}')
        expect(long_row).to_be_in_viewport(timeout=15000)
        def long_is_at_bottom():
            box = long_row.bounding_box(); composer = a.get_by_test_id('chat-input').bounding_box()
            return bool(box and composer and 0 <= composer['y'] - box['y'] - box['height'] <= 80)
        wait_ui(long_is_at_bottom)
        a.screenshot(path=str(ROOT / 'tmp/checks/contacts-chat-long-320.png'))

        # Drop a real committed POST response; a retry or poll reconciliation must not duplicate it.
        dropped = {'value': False}
        def lose_response(route):
            if route.request.method == 'POST' and not dropped['value']:
                dropped['value'] = True
                route.fetch(url=api + f'/api/social/chats/{bid}')
                route.abort('failed')
            else:
                route.fallback()
        a.route(f'**/api/social/chats/{bid}', lose_response)
        a.get_by_test_id('chat-input').fill('网络重试只出现一次')
        a.get_by_test_id('chat-send').click()
        expect(a.get_by_test_id('chat-input')).to_have_value('', timeout=15000)
        wait_ui(lambda: sum(item['body'] == '网络重试只出现一次' for item in history(alice, bid)['items']) == 1)
        a.wait_for_timeout(2500)
        assert sum(item['body'] == '网络重试只出现一次' for item in history(alice, bid)['items']) == 1
        print('[05] committed response loss recovers with one durable message', flush=True)

        outsider = context(browser, api, stranger)
        c = outsider.new_page(); c.goto(WEB + f'/chat/{aid}')
        expect(c.get_by_test_id('chat-error')).to_be_visible(timeout=15000)
        assert text not in c.locator('body').inner_text()
        assert requests.get(api + f'/api/social/chats/{aid}', headers=headers(stranger)).status_code == 403
        a.goto(WEB + '/social'); a.get_by_test_id('social-segment-friends').click()
        a.get_by_test_id(f'social-more-{bid}').click()
        a.get_by_test_id('social-block').click(); a.get_by_test_id('social-confirm').click()
        wait_ui(lambda: len(inbox(alice)['blocked']) == 1 and not inbox(alice)['friends'])
        expect(b.get_by_test_id('chat-error')).to_be_visible(timeout=15000)
        assert requests.post(api + f'/api/social/chats/{aid}', headers=headers(bob),
                             json={'body': 'blocked', 'client_id': 'blocked_attempt'}).status_code == 403
        a.get_by_test_id('social-blocked-open').click()
        a.get_by_test_id(f'social-unblock-{bid}').click()
        wait_ui(lambda: not inbox(alice)['blocked'])
        assert not inbox(alice)['friends'], 'unblocking does not recreate friendship'
        print('[06] outsider isolation, bilateral block enforcement and explicit unblock', flush=True)

        for width in (320, 360, 390):
            a.set_viewport_size({'width': width, 'height': 780})
            a.goto(WEB + '/social')
            expect(a.get_by_test_id('social-add-friend')).to_be_visible()
            assert a.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            a.screenshot(path=str(ROOT / f'tmp/checks/contacts-{width}.png'))
        browser.close()
        print('[07] narrow layouts 320/360/390px have no horizontal overflow', flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix='gamehub-contacts-browser-') as directory:
        database = str(Path(directory) / 'contacts.db')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        api = f'http://127.0.0.1:{port}'
        log_path = ROOT / 'tmp/log/contacts-browser-backend.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        (ROOT / 'tmp/checks').mkdir(parents=True, exist_ok=True)
        with log_path.open('w') as log:
            process = subprocess.Popen([sys.executable, __file__, '--serve', str(port), database], stdout=log, stderr=subprocess.STDOUT)
            try:
                wait_for(lambda: process.poll() is None and ready(api))
                checks(api, database)
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)


def ready(api):
    try: return requests.get(api + '/api/app/version', timeout=0.3).ok
    except requests.RequestException: return False


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--serve': serve(int(sys.argv[2]), sys.argv[3])
    else: main()
