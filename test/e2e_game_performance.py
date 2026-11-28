"""Isolated late-game delta/render stress and twelve-species fish art acceptance.

Run: python3 test/e2e_game_performance.py (existing web server remains running).
"""
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))

from playwright.sync_api import sync_playwright

from app.game import fish
from bench_abyss_performance import crowded_game
from e2e_abyss_complete import isolated_backend, use_backend
from e2e_abyss_smoke import BASE, goto_tab, register


def main():
    with isolated_backend() as (origin, in_match), sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1100, 'height': 850})
        use_backend(page, origin)
        page.add_init_script('''
          localStorage.setItem('abyss.audio.music', 'off');
          localStorage.setItem('abyss.audio.effects', 'off');
          const AppSocket = window.WebSocket;
          window.streamCounts = {full: 0, delta: 0, bytes: 0};
          window.WebSocket = class extends AppSocket {
            constructor(...args) {
              super(...args);
              this.addEventListener('message', event => {
                const msg = JSON.parse(event.data);
                if (msg.type === 'abyss_state') window.streamCounts.full++;
                if (msg.type === 'abyss_delta') window.streamCounts.delta++;
                window.streamCounts.bytes += event.data.length;
              });
            }
          };
        ''')
        with page.expect_response(lambda r: r.url == origin + '/api/auth/register') as registered:
            register(page, f'perf_{int(time.time())}')
        account = registered.value.json()
        uid = account['user']['id']
        headers = {'Authorization': 'Bearer ' + account['token']}
        goto_tab(page, '游戏')
        page.get_by_text('深渊回声', exact=True).first.click()
        with page.expect_response(lambda r: r.url == origin + '/api/rooms') as created:
            page.get_by_text('创建房间', exact=True).click()
        code = created.value.json()['code']
        page.get_by_test_id('guide-skip').click()
        page.get_by_text('开始游戏', exact=True).click()
        page.get_by_test_id('abyss-world').wait_for()

        def stress(gs):
            loaded = crowded_game(4)
            player = loaded['players'].pop(0)
            player.update(id=uid, name='你')
            # Distinct synthetic peers produce the four-build rendering stress.
            peers = {10000 + key: {**value, 'id': 10000 + key}
                     for key, value in loaded['players'].items()}
            loaded['players'] = {uid: player, **peers}
            gs.clear()
            gs.update(loaded)
        in_match(code, stress)
        page.wait_for_function('window.streamCounts.delta >= 8', timeout=10000)
        page.wait_for_timeout(2000)
        page.keyboard.down('d')
        measure = '''world => new Promise(resolve => {
          const changes = []; let first, last, x;
          let batchPeak = 0, entityPeak = 0;
          function sample(now) {
            first ??= now;
            const next = world.transform.baseVal.getItem(0).matrix.e;
            if (next !== x) { if (last) changes.push(now-last); last=now; x=next; }
            batchPeak = Math.max(batchPeak, world.querySelectorAll('[data-testid^="abyss-projectile-batch-"]').length);
            entityPeak = Math.max(entityPeak, world.querySelectorAll('*').length);
            if (now-first < 1500) requestAnimationFrame(sample);
            else { changes.sort((a,b)=>a-b); resolve({updates: changes.length,
              p95: changes[Math.ceil(changes.length*.95)-1], batchPeak, entityPeak, ...window.streamCounts}); }
          }
          requestAnimationFrame(sample);
        })'''
        metrics = page.get_by_test_id('abyss-world').evaluate(measure)
        page.keyboard.up('d')
        assert metrics['delta'] >= 8 and metrics['updates'] > 20, metrics
        assert metrics['batchPeak'] > 0, metrics
        page.screenshot(path='tmp/log/abyss-performance.png')
        print('Late game:', json.dumps(metrics), flush=True)

        cdp = page.context.new_cdp_session(page)
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': 4})
        page.keyboard.down('a')
        slowed = page.get_by_test_id('abyss-world').evaluate(measure)
        page.keyboard.up('a')
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': 1})
        assert slowed['updates'] > 20, slowed
        print('Fourfold CPU throttle:', json.dumps(slowed), flush=True)

        # A new socket must negotiate a new full baseline before accepting deltas.
        page.reload()
        page.get_by_test_id('abyss-world').wait_for()
        page.wait_for_function('window.streamCounts.delta >= 3', timeout=10000)
        print('Delta stream reload/reconnect: passed', flush=True)

        response = page.request.post(origin + '/api/rooms', headers=headers, data={'game': 'fish'})
        assert response.ok, response.text()
        fish_code = response.json()['code']
        page.goto(BASE + '/room/' + fish_code)
        page.get_by_test_id('guide-skip').click()
        page.get_by_text('开始游戏', exact=True).click()
        page.get_by_test_id('fish-player-self').wait_for(state='attached')
        page.wait_for_timeout(300)
        if page.get_by_test_id('guide-skip').count():
            page.get_by_test_id('guide-skip').click()

        def fish_gallery(gs):
            # Static art gallery in a real running fish room, immune player kept clear of the fish.
            gs['players'][uid].update(x=600, y=425, immune=1000000, shield=True)
            entries = [(7, 0), (16, 0), (16, 1), (23, 0), (23, 1), (31, 0),
                       (31, 1), (41, 0), (41, 1), (56, 1), (73, 0), (92, 1)]
            gs['fish'] = []
            for index, (radius, parity) in enumerate(entries):
                entity = fish._wild(gs, size=radius)
                entity.update(id=200 + index*2 + parity, x=200 + index%4*265,
                              y=160 + index//4*250, vx=0, vy=0)
                gs['fish'].append(entity)
            gs['population'] = len(gs['fish'])
        in_match(fish_code, fish_gallery)
        species = ['tetra', 'goldfish', 'clownfish', 'tang', 'angelfish', 'flounder',
                   'puffer', 'lionfish', 'tuna', 'swordfish', 'hammerhead', 'shark']
        for name in species:
            page.get_by_test_id('fish-species-' + name).first.wait_for(timeout=5000)
        page.wait_for_function('''() => {
          const images = [...document.querySelectorAll('[data-testid^="fish-species-"] img')];
          return images.length >= 12 && images.every(img => img.complete && img.naturalWidth > 0);
        }''')
        page.screenshot(path='tmp/log/fish-species.png')
        print('Twelve species loaded in actual fish room: passed', flush=True)
        browser.close()


if __name__ == '__main__':
    main()
