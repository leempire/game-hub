"""Isolated browser check: admin credits, one human + three paid bots.

Run: python3 test/e2e_undercover_ai.py (existing Web server required).
Provider replies are deterministic; no paid network calls or live user writes.
"""

import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx
import requests
from playwright.sync_api import expect, sync_playwright
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'server'))
from app.cfg import config

OUT = ROOT / 'tmp/checks'
BASE = os.environ.get('GAMEHUB_WEB_URL', 'http://localhost:50540')


async def fake_post(self, url, **kwargs):
    view = json.loads(kwargs['json']['messages'][1]['content'])
    action = ({'text': '经常能在生活中见到这种东西'} if view['can_clue']
              else {'target_id': view['candidate_ids'][0]})
    return httpx.Response(200, request=httpx.Request('POST', url), json={
        'usage': {'prompt_tokens': 1000, 'completion_tokens': 100,
                  'prompt_tokens_details': {'cached_tokens': 100}},
        'choices': [{'message': {'content': json.dumps(action, ensure_ascii=False)}}],
    })


def serve(port, db_path):
    os.environ['ARK_AGENT_API_KEY'] = 'isolated-mock-key'
    httpx.AsyncClient.post = fake_post
    config.update(db_path=db_path, admin={'username': 'ai_test_admin', 'password': 'isolated-test-password'},
                  jwt_secret='isolated-ai-browser-secret-at-least-32-bytes')
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, log_level='warning')


def browser_checks(api, db_path):
    login = requests.post(api + '/api/auth/login', json={
        'username': 'ai_test_admin', 'password': 'isolated-test-password'}).json()
    token, uid = login['token'], login['user']['id']
    headers = {'Authorization': 'Bearer ' + token}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.set_default_timeout(15000)
        page.route(re.compile(r'/api/'), lambda route: route.continue_(
            url=api + urlsplit(route.request.url).path +
            ('?' + urlsplit(route.request.url).query if urlsplit(route.request.url).query else '')))
        page.add_init_script('''localStorage.setItem('gamehub_token', %s);
            localStorage.setItem('gamehub_user', %s);
            const Original = window.WebSocket;
            window.WebSocket = class extends Original { constructor(url, protocols) {
              const parsed = new URL(url);
              super(parsed.pathname.startsWith('/ws/room/') ? %s + parsed.pathname + parsed.search : url, protocols);
            }};''' % (json.dumps(token), json.dumps(json.dumps(login['user'])), json.dumps(api.replace('http:', 'ws:'))))
        messages, errors = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('websocket', lambda ws: ws.on('framereceived', lambda raw: messages.append(json.loads(raw))))
        page.goto(BASE, timeout=90000)
        page.get_by_text('我的', exact=True).click()
        expect(page.get_by_text('AI 积分：0（由管理员设置）', exact=True)).to_be_visible()
        page.get_by_text('用户管理', exact=True).click()
        page.get_by_text('设置 AI 积分', exact=True).click()
        page.locator('input').last.fill('100')
        page.get_by_text('保存积分', exact=True).click()
        expect(page.get_by_text('保存积分', exact=True)).not_to_be_visible()
        expect(page.get_by_text('AI 积分：100', exact=True)).to_be_visible()
        assert requests.get(api + '/api/auth/me', headers=headers).json()['ai_credits'] == 100
        page.screenshot(path=str(OUT / 'ai-admin-credits.png'))
        # Navigate to the catalog with browser linking after saving the modal.
        page.goto(BASE)
        page.get_by_role('button', name='多人桌游', exact=True).click()
        page.get_by_test_id('game-card-undercover').click()
        page.get_by_text('创建房间', exact=True).click()
        page.get_by_text('房间号', exact=True).wait_for()
        for count in range(1, 4):
            page.get_by_text('添加人机', exact=True).click()
            expect(page.get_by_label(re.compile('移除人机'))).to_have_count(count)
        assert 'token' not in page.locator('body').inner_text().lower()
        page.screenshot(path=str(OUT / 'ai-lobby.png'))
        page.get_by_text('开始游戏', exact=True).click()
        expect(page.get_by_test_id('undercover-word-toggle')).to_be_visible()
        page.get_by_test_id('undercover-clue-input').fill('日常生活里比较常见')
        page.get_by_test_id('undercover-clue-submit').click()
        page.get_by_test_id('undercover-vote-confirm').wait_for()
        for _ in range(30):
            if any((m.get('game') or {}).get('votes_cast') == 3 for m in messages):
                break
            page.wait_for_timeout(100)
        assert any((m.get('game') or {}).get('votes_cast') == 3 for m in messages)
        balance = requests.get(api + '/api/auth/me', headers=headers).json()['ai_credits']
        assert 0 < balance < 100
        with sqlite3.connect(db_path) as conn:
            count, cost = conn.execute("SELECT count(*),sum(cost_units) FROM ai_usage WHERE status='settled'").fetchone()
            assert count == 6
            assert cost == 6 * (100 + 900*50 + 100*200)
        assert not errors, errors
        page.screenshot(path=str(OUT / 'ai-undercover-vote.png'))
        vote = next(m['game'] for m in reversed(messages)
                    if m['type'] == 'undercover_state' and m['game']['votes_cast'] == 3)
        page.get_by_test_id(f"undercover-vote-{vote['candidate_ids'][0]}").click()
        page.get_by_test_id('undercover-vote-confirm').click()
        # Bots vote out the human first; an eliminated observer must keep the AI match running.
        expect(page.get_by_test_id('undercover-result')).to_be_visible(timeout=20000)
        with sqlite3.connect(db_path) as conn:
            record = conn.execute('SELECT has_bots,players_json,snapshot_json FROM game_records').fetchone()
            assert record and record[0] == 1
            players = json.loads(record[1])
            assert len({player['username'] for player in players}) == 4
            assert sum(player['is_bot'] for player in players) == 3
            assert 'ai_owner_id' not in record[2] and '_ai_attempted' not in record[2]
        page.get_by_test_id('undercover-back-to-room').click()
        page.get_by_text('开始游戏', exact=True).click()
        expect(page.get_by_text('有 AI 人机的对局需要房主 AI 积分至少为 100', exact=True)).to_be_visible()
        assert not errors, errors
        print('PASS: 积分设置/整数展示、一真人三AI发言投票与扣费、出局观战自然终局、人机战绩、余额不足拦截续局')
        browser.close()


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    api = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory(prefix='gamehub-ai-e2e-') as folder, (OUT / 'ai-e2e-server.log').open('w') as log:
        db_path = str(Path(folder) / 'ai.db')
        process = subprocess.Popen([sys.executable, __file__, '--serve', str(port), db_path], cwd=ROOT, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    if requests.get(api + '/api/app/version', timeout=.3).ok:
                        break
                except requests.RequestException:
                    time.sleep(.1)
            browser_checks(api, db_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    serve(int(sys.argv[2]), sys.argv[3]) if '--serve' in sys.argv else run()
