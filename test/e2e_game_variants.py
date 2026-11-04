"""Real-browser checks for variant payloads; isolated backend/database, existing web bundle.

The test backend seeds small deterministic positions; production exposes no fixture endpoint.
Run from the repository root: python test/e2e_game_variants.py
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


def serve(port, db_path):
    sys.path.insert(0, str(ROOT / 'server'))
    from app.cfg import config
    from app import room_manager as rm, room_timing
    import uvicorn
    config.update(db_path=db_path, admin={}, jwt_secret='isolated-variants-browser-test-secret-long-enough')
    original, uno_round = rm.start_game, 0

    def start_game(code, uid, **kwargs):
        nonlocal uno_round
        state = original(code, uid, **kwargs)
        if state is None:
            return state
        room = rm.get_room(code)
        if room['game'] == 'connect4':
            state['board'][5][0], state['board'][4][0] = 1, 2
            state['moves'] = [{'x': 0, 'y': 5, 'color': 'black', 'action': 'drop'},
                              {'x': 0, 'y': 4, 'color': 'white', 'action': 'drop'}]
        elif room['game'] == 'luzhanqi':
            state['setup_remaining_ms'] = 10000
            room_timing.schedule(room)
        elif room['game'] == 'ludo':
            state.update(phase='move', dice_value=6, movable_pieces=[0])
        elif room['game'] == 'uno':
            uno_round += 1
            state.update(discard_pile=['R5'], current_color='R', turn=1 if uno_round == 1 else 0,
                         pending_draw=0, pending_kind=None, drawn_this_turn=None)
            state['hands'] = {0: ['B2', 'G3'], 1: ['W+4', 'R7']} if uno_round == 1 else {0: ['R1'], 1: ['R8', 'R9']}
        return state

    rm.start_game = start_game
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def receive(ws, predicate):
    for _ in range(80):
        message = json.loads(ws.recv())
        if message.get('type') == 'error':
            raise AssertionError(message.get('message'))
        if predicate(message):
            return message
    raise AssertionError('Expected websocket message was not received')


def browser_checks(api):
    users = []
    for seat in range(4):
        response = requests.post(api + '/api/auth/register', json={
            'username': f'variant_{seat}', 'password': 'test123456',
        })
        response.raise_for_status()
        users.append(response.json())
    headers = lambda seat: {'Authorization': 'Bearer ' + users[seat]['token']}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        contexts, peers = [], []
        errors = []

        def open_game(game, settings=None, *, count=2, bot=False):
            room = requests.post(api + '/api/rooms', headers=headers(0), json={'game': game}).json()
            code = room['code']
            host = websocket.create_connection(api.replace('http://', 'ws://') + f'/ws/room/{code}?token=' + users[0]['token'], timeout=10)
            receive(host, lambda msg: msg['type'] == 'sync')
            for key, value in (settings or {}).items():
                host.send(json.dumps({'type': 'room_setting', 'key': key, 'value': value}))
                receive(host, lambda msg: msg['type'] == 'lobby' and any(
                    item['key'] == key and item['value'] == value for item in msg['room'].get('settings', [])))
            sockets = []
            if bot:
                host.send(json.dumps({'type': 'add_bot', 'difficulty': 'easy'}))
                receive(host, lambda msg: msg['type'] == 'lobby' and len(msg['room']['players']) == 2)
            else:
                for seat in range(1, count):
                    requests.post(api + f'/api/rooms/{code}/join', headers=headers(seat), json={}).raise_for_status()
                    peer = websocket.create_connection(api.replace('http://', 'ws://') + f'/ws/room/{code}?token=' + users[seat]['token'], timeout=10)
                    receive(peer, lambda msg: msg['type'] == 'sync')
                    peer.send(json.dumps({'type': 'ready'}))
                    receive(host, lambda msg: msg['type'] == 'lobby' and any(
                        player['id'] == users[seat]['user']['id'] and player['ready'] for player in msg['room']['players']))
                    sockets.append(peer)
            host.send(json.dumps({'type': 'start'}))
            receive(host, lambda msg: msg['type'] == 'game_start')
            host.close()
            context = browser.new_context(viewport={'width': 390, 'height': 844})
            contexts.append(context); peers.extend(sockets)
            context.route('**/api/**', lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
                ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
            context.add_init_script('''const Original = window.WebSocket;
                window.WebSocket = class extends Original { constructor(url, protocols) {
                  const parsed = new URL(url);
                  super(parsed.pathname.startsWith('/ws/room/') ? %s + parsed.pathname + parsed.search : url, protocols);
                }};
                localStorage.setItem('gamehub_token', %s); localStorage.setItem('gamehub_user', %s);''' % (
                    json.dumps(api.replace('http://', 'ws://')), json.dumps(users[0]['token']), json.dumps(json.dumps(users[0]['user']))))
            page = context.new_page(); page.set_default_timeout(15000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            received = []
            page.on('websocket', lambda ws: ws.on('framereceived', lambda value: received.append(json.loads(value))))
            page.goto(BASE + f'/room/{code}')
            page.get_by_test_id('game-shell-' + game).wait_for()
            page.get_by_text('已连接', exact=True).wait_for()
            return page, sockets, received, code

        def packet(page, messages, kind, predicate=lambda value: True):
            for _ in range(150):
                found = [msg for msg in messages if msg.get('type') == kind and predicate(msg)]
                if found:
                    return found[-1]
                page.wait_for_timeout(100)
            raise AssertionError(f'No {kind} packet matched; recent types: {[msg.get("type") for msg in messages[-10:]]}')

        page, _, messages, _ = open_game('gomoku', {'board_size': 9, 'forbidden': True, 'clock_seconds': 300})
        game = packet(page, messages, 'sync')['game']
        assert game['board_size'] == 9 and game['forbidden']
        assert page.locator('[data-testid^="gomoku-cell-"]').count() == 81
        page.get_by_test_id('game-clock').wait_for()
        box = page.get_by_test_id('gomoku-cell-4-4').bounding_box()
        for _ in range(2):
            page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.wait_for_timeout(80)
        packet(page, messages, 'move', lambda msg: msg.get('x') == 4 and msg.get('y') == 4)
        print('PASS 1: 9路禁手五子棋、权威合法落子和棋钟', flush=True)

        page, _, messages, _ = open_game('reversi', {'board_size': 10})
        game = packet(page, messages, 'sync')['game']
        assert page.locator('[data-testid^="cell-"]').count() == 100
        x, y = game['legal'][0]
        page.get_by_test_id(f'cell-{x}-{y}').click()
        packet(page, messages, 'move', lambda msg: msg.get('x') == x and msg.get('y') == y)
        page, _, _, _ = open_game('dots', {'dots': 4})
        assert page.locator('[data-testid^="h-"]').count() == 12
        assert page.locator('[data-testid^="v-"]').count() == 12
        print('PASS 2: 10路翻转棋和4×4点阵实际渲染、合法落子', flush=True)

        page, _, messages, _ = open_game('connect4', {'popout': True})
        page.get_by_test_id('c4-pop-0').click()
        popped = packet(page, messages, 'move', lambda msg: msg.get('action') == 'pop')
        assert popped['board'][5][0] == 2 and popped['board'][4][0] == 0
        assert page.get_by_test_id('c4-disc-5-0').count() == 1 and page.get_by_test_id('c4-disc-4-0').count() == 0
        print('PASS 3: PopOut真实动作与服务端权威重排', flush=True)

        page, _, messages, _ = open_game('xiangqi', {'clock_seconds': 300, 'byoyomi_seconds': 10, 'allow_undo': True}, bot=True)
        game = packet(page, messages, 'sync')['game']; fr, fc, tr, tc = game['legal'][0]
        page.get_by_test_id(f'xq-{fr}-{fc}').click(); page.get_by_test_id(f'xq-{tr}-{tc}').click()
        packet(page, messages, 'move', lambda msg: msg.get('can_undo') is True)
        page.get_by_test_id('xiangqi-notation').wait_for()
        page.get_by_test_id('xiangqi-undo').click()
        undone = packet(page, messages, 'xiangqi_state')['game']
        assert not undone['moves'] and undone['clock'] and not undone['can_undo']
        for width, height in [(390, 844), (360, 640)]:
            page.set_viewport_size({'width': width, 'height': height}); page.wait_for_timeout(350)
            page.screenshot(path=str(OUT / f'shell-xiangqi-{width}.png'))
        print('PASS 4: 象棋中文记谱、人机应招、整对悔棋与权威棋钟', flush=True)

        page, sockets, messages, _ = open_game('luzhanqi', {'mode': 'hidden', 'setup_timeout': 30})
        page.get_by_test_id('luzhanqi-setup-countdown').wait_for()
        assert not page.get_by_test_id('lz-truth-board').count()
        packet(page, messages, 'luzhanqi_begin')
        sockets[0].send(json.dumps({'type': 'surrender'}))
        page.get_by_test_id('lz-truth-board').wait_for()
        assert packet(page, messages, 'luzhanqi_state')['game']['truth_available']
        print('PASS 5: 军棋限时自动布阵与终局真相（进行中不亮牌）', flush=True)

        page, _, messages, _ = open_game('ludo', {'teams': '2v2', 'animation_speed': 'fast'}, count=4)
        game = packet(page, messages, 'sync')['game']
        assert game['seat_teams'] == [0, 1, 0, 1] and game['animation_scale'] == .6
        assert '第 1 队' in page.locator('body').inner_text() and '第 2 队' in page.locator('body').inner_text()
        page.get_by_test_id('ludo-piece-0-0').click()
        packet(page, messages, 'piece_moved')
        print('PASS 6: 飞行棋2v2归属、快动画档和权威走子', flush=True)

        official = {'wild4_challenge': True, 'uno_penalty': True, 'score_target': 500}
        page, sockets, messages, _ = open_game('uno', official)
        sockets[0].send(json.dumps({'type': 'uno_play', 'card': 'W+4', 'color': 'B'}))
        page.get_by_test_id('uno-catch-1').click()
        packet(page, messages, 'uno_event', lambda msg: msg.get('kind') == 'catch_uno')
        page.get_by_test_id('uno-challenge-wild4').click()
        challenge = packet(page, messages, 'uno_event', lambda msg: msg.get('kind') == 'challenge_wild4')
        assert challenge['success'] and challenge['penalty'] == 4
        page, _, messages, _ = open_game('uno', official)
        page.get_by_test_id('u-play-0').click()
        final = packet(page, messages, 'game_over')
        assert final['series']['scores'][str(users[0]['user']['id'])] == 17
        expect(page.get_by_test_id('uno-series-score')).to_contain_text('17 分')
        expect(page.get_by_test_id('game-result')).to_contain_text('本局结束，系列赛继续')
        print('PASS 7: UNO抓漏喊、+4质疑罚牌与500分系列赛单局累计', flush=True)
        assert not errors, errors
        for peer in peers:
            peer.close()
        for context in contexts:
            context.close()
        browser.close()


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    api = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='gamehub-variants-') as temp, (OUT / 'variants-e2e-server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, __file__, '--serve', str(port), str(Path(temp) / 'test.db')],
                                   cwd=ROOT / 'server', stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError('Variant test backend exited; see variants-e2e-server.log')
                try:
                    if requests.get(api + '/docs', timeout=.5).ok:
                        break
                except requests.RequestException:
                    time.sleep(.1)
            else:
                raise AssertionError('Variant test backend did not start')
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
