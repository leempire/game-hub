"""Daily/leaderboard/public-share UI smoke with an isolated backend and database.

Usage: python test/e2e_competition_smoke.py
GAMEHUB_SERVER_ROOT can point at a frozen implementation layer for verification.
"""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import requests
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('GAMEHUB_WEB_URL', 'http://localhost:50540')
SERVER = Path(os.environ.get('GAMEHUB_SERVER_ROOT', ROOT / 'server')).resolve()


def serve(port, db_path):
    sys.path.insert(0, str(SERVER))
    from app.cfg import config
    import uvicorn
    config.update(db_path=db_path, admin={}, jwt_secret='isolated-competition-browser-secret-long-enough')
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def route_context(browser, api, auth=None):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    context.route('**/api/**', lambda route: route.continue_(url=api + urlsplit(route.request.url).path +
        ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
    context.add_init_script('''const OriginalSocket = window.WebSocket;
        window.WebSocket = class extends OriginalSocket { constructor(url, protocols) {
            const parsed = new URL(url);
            super(parsed.pathname.startsWith('/ws/') ? %s + parsed.pathname + parsed.search : url, protocols);
        }};''' % json.dumps(api.replace('http://', 'ws://')))
    if auth:
        context.add_init_script('localStorage.setItem("gamehub_token", %s); localStorage.setItem("gamehub_user", %s);'
                                % (json.dumps(auth['token']), json.dumps(json.dumps(auth['user']))))
    return context


def checks(api):
    auth = requests.post(api + '/api/auth/register', json={'username': 'competition_browser', 'password': 'test123456'}).json()
    headers = {'Authorization': 'Bearer ' + auth['token']}
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = route_context(browser, api, auth)
        page = context.new_page()
        page.set_default_timeout(20000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE, timeout=90000)
        page.get_by_text('每日挑战', exact=True).click()
        expect(page.get_by_test_id('competition-screen')).to_be_visible()
        for game in ('2048', 'tetris', 'minesweeper'):
            expect(page.get_by_test_id('challenge-start-' + game)).to_be_visible()
        print('[01] daily entrance and three challenge games visible', flush=True)
        page.get_by_test_id('challenge-start-2048').click()
        expect(page.get_by_text(re.compile('每日挑战 · .*固定单人房规'))).to_be_visible()
        page.get_by_text('开始游戏', exact=True).click()
        expect(page.get_by_test_id('game-shell-2048')).to_be_visible()
        expect(page.get_by_test_id('g2048-undo')).to_have_count(0)
        page.get_by_text('认输', exact=True).click()
        page.get_by_text('确认认输', exact=True).click()
        expect(page.get_by_test_id('game-result')).to_be_visible()
        page.get_by_text('查看战绩', exact=True).click()
        record = requests.get(api + '/api/history/mine', headers=headers).json()['items'][0]
        page.get_by_test_id('history-record-' + str(record['id'])).click()
        page.get_by_text('生成回放分享链接', exact=True).click()
        link = page.get_by_text(re.compile(r'/replay/[A-Za-z0-9_-]{32}$'))
        expect(link).to_be_visible()
        share_token = re.search(r'/replay/([A-Za-z0-9_-]{32})', link.inner_text()).group(1)
        print('[02] private daily round, no undo, authoritative surrender, history and explicit share', flush=True)
        status = requests.get(api + '/api/challenges/today', headers=headers).json()
        assert not next(item for item in status['games'] if item['game'] == '2048')['completed']
        public = route_context(browser, api)
        anon = public.new_page()
        anon.on('pageerror', lambda error: errors.append(str(error)))
        sockets = []
        anon.on('websocket', lambda ws: sockets.append(ws.url))
        anon.goto(BASE + '/replay/' + share_token, timeout=90000)
        expect(anon.get_by_test_id('shared-replay')).to_be_visible()
        expect(anon.get_by_text('2048 竞速 · 终局回放', exact=True)).to_be_visible()
        assert not [url for url in sockets if '/ws/room/' in url]
        assert requests.get(api + '/api/history/shared/' + share_token).json()['room_code'] == ''
        print('[03] anonymous deep link renders existing board without a player socket', flush=True)
        page.get_by_text('撤销分享', exact=True).click()
        page.get_by_text('确认撤销', exact=True).click()
        expect(page.get_by_text('分享已撤销，旧链接已失效', exact=True)).to_be_visible()
        anon.reload()
        expect(anon.get_by_text('分享不存在或已撤销', exact=True)).to_be_visible()
        print('[04] revocation invalidates anonymous link', flush=True)
        page.goto(BASE + '/competition')
        expect(page.get_by_test_id('competition-screen')).to_be_visible()
        page.get_by_test_id('challenge-start-minesweeper').click()
        page.get_by_text('开始游戏', exact=True).click()
        expect(page.get_by_test_id('game-shell-minesweeper')).to_be_visible()
        center = page.get_by_test_id('mine-cell-4-4')
        expect(center.get_by_text('首翻', exact=True)).to_be_visible()
        center.click()
        expect(center.get_by_text('首翻', exact=True)).to_have_count(0)
        print('[05] daily mine center is marked and reveals through the real socket', flush=True)
        page.goto(BASE + '/competition')
        page.get_by_text('排行榜', exact=True).click()
        expect(page.get_by_text('你还没有符合本榜条件的成绩', exact=True)).to_be_visible()
        # 游戏 / 时间 / 房规都收进了弹窗选择，不再是一排排常驻芯片。
        page.get_by_test_id('board-filters').click()
        page.get_by_text('近 7 天', exact=True).click()
        page.get_by_text('完成', exact=True).click()
        expect(page.get_by_text('暂无成绩，完成正式对局后再来看看。', exact=True)).to_be_visible()
        for width, height in ((360, 640), (390, 844), (1024, 768)):
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        print('[06] leaderboard filtering, empty state and three viewport widths', flush=True)
        browser.close()


def main():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='gamehub-competition-') as directory:
        log = Path(directory) / 'server.log'
        with log.open('w') as output:
            process = subprocess.Popen([sys.executable, __file__, '--serve', str(port), str(Path(directory) / 'test.db')],
                                       stdout=output, stderr=subprocess.STDOUT)
        api = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    if requests.get(api + '/api/app/version', timeout=.3).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError(log.read_text())
            checks(api)
        finally:
            process.terminate()
            process.wait(timeout=15)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--serve':
        serve(int(sys.argv[2]), sys.argv[3])
    else:
        main()
