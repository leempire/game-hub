"""Layer-five real browser acceptance, using a private backend and temporary database.

Run against the existing Expo web bundle: python test/e2e_social.py
No production server, account or database is changed.
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
import websocket
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'tmp/checks'
BASE = os.environ.get('GAMEHUB_WEB_URL', 'http://localhost:50540')


def serve(port, database):
    sys.path.insert(0, str(ROOT / 'server'))
    from app.cfg import config
    import uvicorn
    config.update(db_path=database, admin={}, jwt_secret='isolated-social-browser-test-secret-long-enough')
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def receive(ws, predicate):
    for _ in range(100):
        message = json.loads(ws.recv())
        if message.get('type') == 'error':
            raise AssertionError(message.get('message'))
        if predicate(message):
            return message
    raise AssertionError('Expected authoritative websocket message missing')


def browser_checks(api):
    users = []
    for index in range(3):
        response = requests.post(api + '/api/auth/register', json={'username': f'social_{index}', 'password': 'test123456'})
        response.raise_for_status()
        users.append(response.json())
    def headers(seat):
        return {'Authorization': 'Bearer ' + users[seat]['token']}
    def get(path, seat=0):
        response = requests.get(api + path, headers=headers(seat)); response.raise_for_status(); return response.json()
    def post(path, seat=0, body=None):
        response = requests.post(api + path, headers=headers(seat), json=body or {}); response.raise_for_status(); return response.json()
    def connect(code, seat):
        connection = websocket.create_connection(api.replace('http://', 'ws://') + f'/ws/room/{code}?token=' + users[seat]['token'], timeout=10)
        receive(connection, lambda message: message['type'] == 'sync')
        sockets.append(connection)
        return connection

    contexts, sockets, browser_errors = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        def page_for(seat, width=360):
            context = browser.new_context(viewport={'width': width, 'height': 800})
            contexts.append(context)
            context.route('**/api/**', lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
                ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
            context.add_init_script('''const Original = window.WebSocket;
              window.WebSocket = class extends Original { constructor(url, protocols) {
                const parsed = new URL(url); super(parsed.pathname.startsWith('/ws/') ? %s + parsed.pathname + parsed.search : url, protocols);
                (window.__socialSockets ||= []).push(this);
              }};
              localStorage.setItem('gamehub_token', %s); localStorage.setItem('gamehub_user', %s);''' % (
                json.dumps(api.replace('http://', 'ws://')), json.dumps(users[seat]['token']), json.dumps(json.dumps(users[seat]['user']))))
            page = context.new_page(); page.set_default_timeout(20000)
            page.on('pageerror', lambda error: browser_errors.append(str(error)))
            messages = []
            page.on('websocket', lambda ws: ws.on('framereceived', lambda frame: messages.append(json.loads(frame))))
            return page, messages
        def skip(page):
            page.get_by_test_id('game-intro').wait_for()
            page.get_by_test_id('guide-skip').click()
            expect(page.get_by_test_id('game-intro')).not_to_be_visible()
        def narrow(page):
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Horizontal viewport overflow'
        def packet(page, messages, kind):
            for _ in range(150):
                found = [message for message in messages if message.get('type') == kind]
                if found:
                    return found[-1]
                page.wait_for_timeout(100)
            raise AssertionError('Missing browser websocket message: ' + kind)
        def running_room(game):
            code = post('/api/rooms', body={'game': game})['code']
            host = connect(code, 0)
            host.send(json.dumps({'type': 'room_setting', 'key': 'allow_spectators', 'value': True}))
            receive(host, lambda message: message['type'] == 'lobby' and message['room'].get('allow_spectators'))
            post(f'/api/rooms/{code}/join', 1)
            peer = connect(code, 1)
            peer.send(json.dumps({'type': 'ready'}))
            receive(host, lambda message: message['type'] == 'lobby' and all(player['ready'] or player['id'] == users[0]['user']['id'] for player in message['room']['players']))
            host.send(json.dumps({'type': 'start'}))
            receive(host, lambda message: message['type'] == 'game_start')
            host.close()
            return code, peer
        try:
            # The guide lives in the real Room screen and persists its skip per account/game.
            code = post('/api/rooms', body={'game': 'uno'})['code']
            page, messages = page_for(0)
            page.goto(BASE + '/room/' + code)
            page.get_by_test_id('game-intro').wait_for()
            page.get_by_test_id('guide-next').click()
            expect(page.get_by_text('新手指引 · 2 / 3', exact=True)).to_be_visible()
            skip(page)
            page.reload()
            page.get_by_test_id('start-practice').wait_for()
            expect(page.get_by_test_id('game-intro')).not_to_be_visible()
            page.get_by_text('新手指引', exact=True).click()
            page.get_by_test_id('guide-next').click()
            page.get_by_test_id('guide-next').click()
            expect(page.get_by_text('新手指引 · 3 / 3', exact=True)).to_be_visible()
            narrow(page)
            page.wait_for_timeout(350)  # Capture the settled native-modal fade, not its first animation frame.
            page.screenshot(path=str(OUT / 'social-guide-uno-360.png'), full_page=True)
            page.get_by_test_id('guide-next').click()
            expect(page.get_by_test_id('game-intro')).not_to_be_visible()
            baseline = {path: get(path) for path in ['/api/history/mine', '/api/history/stats', '/api/leaderboards/uno']}
            with page.expect_response(lambda response: '/api/practice/uno' in response.url) as created:
                page.get_by_test_id('start-practice').click()
            practice_code = created.value.json()['code']
            expect(page.get_by_text('私人练习局 · 不计入历史战绩、统计和排行榜', exact=True)).to_be_visible()
            page.get_by_text('开始游戏', exact=True).click()
            page.get_by_test_id('game-shell-uno').wait_for()
            page.get_by_test_id('u-surrender').click()
            page.get_by_text('确认认输', exact=True).click()
            page.get_by_test_id('game-result').wait_for()
            for path, value in baseline.items():
                assert get(path) == value, 'Practice polluted persistent competition data: ' + path
            assert get('/api/rooms/' + practice_code)['practice']
            narrow(page)
            print('PASS guide skip remembered; private bot practice leaves history/stats/ranking unchanged')

            for game, phrase in [('uno', '大家好，一起玩吧'), ('stasis', '大家好，一起守住')]:
                code, peer = running_room(game)
                player, player_messages = page_for(0)
                player.add_init_script('localStorage.setItem(%s, \"seen\")' % json.dumps(f"gamehub:guide:v1:{users[0]['user']['id']}:{game}"))
                player.goto(BASE + '/room/' + code)
                player.get_by_test_id('game-shell-' + game).wait_for()
                player.get_by_test_id('quick-chat-open').click()
                player.get_by_test_id('quick-phrase-hello').click()
                expect(player.get_by_test_id('quick-chat-message').filter(has_text=phrase)).to_be_visible()
                event = receive(peer, lambda message: message['type'] == 'quick_chat')
                assert event['text'] == phrase and event['user_id'] == users[0]['user']['id']
                before = get('/api/rooms/' + code)
                watcher, watch_messages = page_for(2)
                watcher.goto(BASE + '/watch/' + code)
                state = packet(watcher, watch_messages, 'watch_sync')
                assert state['read_only'] is True
                post(f'/api/rooms/{code}/quick-chat', 1, {'id': 'hello'})
                expect(watcher.get_by_test_id('watch-phrase').filter(has_text=phrase)).to_be_visible()
                if game == 'uno':
                    assert not {'hand', 'hands', 'your_seat', 'playable', 'legal_actions'} & state['game'].keys()
                    assert state['game']['hand_counts']
                after = get('/api/rooms/' + code)
                assert [p['id'] for p in before['players']] == [p['id'] for p in after['players']]
                assert users[2]['user']['id'] not in [p['id'] for p in after['players']]
                assert watcher.get_by_test_id('u-play-0').count() == 0
                assert watcher.get_by_test_id('u-draw').count() == 0
                narrow(watcher)
                watcher.screenshot(path=str(OUT / f'social-watch-{game}-360.png'), full_page=True)
                watcher.evaluate("window.__socialSockets.find(socket => socket.url.includes('/ws/watch/')).send(JSON.stringify({type:'surrender'}))")
                denied = packet(watcher, watch_messages, 'error')
                assert '只读' in denied['message']
                watcher.get_by_test_id('watch-leave').click()
                expect(watcher.get_by_test_id('spectator-screen')).not_to_be_visible()
                watcher.wait_for_function("window.__socialSockets.filter(socket => socket.url.includes('/ws/watch/')).every(socket => socket.readyState === WebSocket.CLOSED)")
                player.wait_for_timeout(700)
                final = get('/api/rooms/' + code)
                assert final['status'] == 'playing'
                assert [p['id'] for p in final['players']] == [p['id'] for p in before['players']]
                assert not any(message.get('type') == 'game_over' for message in player_messages)
                print(f'PASS {game} quick phrase broadcast; readonly watcher hidden data/seats/exit/360px')
            assert not browser_errors, browser_errors
        finally:
            for context in contexts:
                context.close()
            for connection in sockets:
                connection.close()
            browser.close()


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as endpoint:
        endpoint.bind(('127.0.0.1', 0)); port = endpoint.getsockname()[1]
    api = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='gamehub-social-') as temporary, (OUT / 'social-e2e-server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, __file__, '--serve', str(port), str(Path(temporary) / 'test.db')],
                                   cwd=ROOT / 'server', stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError('Social backend exited; see social-e2e-server.log')
                try:
                    if requests.get(api + '/docs', timeout=.5).ok:
                        break
                except requests.RequestException:
                    time.sleep(.1)
            else:
                raise AssertionError('Social backend did not start')
            browser_checks(api)
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--serve':
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        run()
