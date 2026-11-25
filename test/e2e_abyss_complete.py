"""Browser-to-WebSocket progression, boss, result and history smoke test.

Run from the repository root with the web bundler running on localhost:50540.
The test uses a loopback backend and disposable database. Server checkpoints
skip waiting for the late waves while combat and persistence take normal paths.
"""
import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import os
import re
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

from e2e_abyss_smoke import BASE, UA, UB, goto_tab, ok, register


@contextmanager
def isolated_backend():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
    os.environ['JWT_SECRET'] = secrets.token_urlsafe(48)
    from app.main import app
    from app import room_manager
    from app.cfg import config

    with tempfile.TemporaryDirectory(prefix='gamehub-abyss-') as directory:
        config.update(db_path=str(Path(directory) / 'test.db'), admin={})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
            loop = asyncio.new_event_loop()
            async def serve():
                try:
                    await server.serve(sockets=[listener])
                finally:
                    pending = asyncio.all_tasks() - {asyncio.current_task()}
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

            worker = threading.Thread(target=lambda: loop.run_until_complete(serve()), daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    if not worker.is_alive() or time.monotonic() >= deadline:
                        raise RuntimeError('Isolated backend did not start')
                    time.sleep(.02)

                def in_match(code, change):
                    async def apply():
                        return change(room_manager.get_room(code)['game_state'])
                    return asyncio.run_coroutine_threadsafe(apply(), loop).result(timeout=5)

                yield f'http://127.0.0.1:{port}', in_match
            finally:
                server.should_exit = True
                worker.join(10)
                if worker.is_alive():
                    raise RuntimeError('Isolated backend did not shut down')
                loop.close()


def use_backend(page, origin):
    page.add_init_script(f'''
      const testOrigin = {json.dumps(origin)};
      function redirectGameHubUrl(url) {{
        const current = new URL(url, window.location.href);
        if (current.pathname.startsWith('/api/')) return testOrigin + current.pathname + current.search;
        if (current.pathname.startsWith('/ws/')) return testOrigin.replace(/^http/, 'ws') + current.pathname + current.search;
        return url;
      }}
      const appFetch = window.fetch;
      window.fetch = (resource, options) => appFetch(
        typeof resource === 'string' ? redirectGameHubUrl(resource) : resource, options);
      const AppSocket = window.WebSocket;
      const roomConnections = new Set();
      let mutedConnection = null;
      let jitter = false;
      let packet = 0;
      let lastBattlePacket;
      window.repeatAbyssBattlePacket = () => {{
        if (mutedConnection?.readyState === AppSocket.OPEN && lastBattlePacket) {{
          mutedConnection.dispatchEvent(new MessageEvent('message', {{data: lastBattlePacket}}));
        }}
      }};
      window.setAbyssTestJitter = enabled => {{ jitter = enabled; }};
      window.abyssRoomConnectionCount = 0;
      window.setAbyssTestMute = enabled => {{
        mutedConnection = enabled
          ? [...roomConnections].filter(connection => connection.readyState === AppSocket.OPEN).at(-1)
          : null;
      }};
      window.WebSocket = class extends AppSocket {{
        constructor(url, ...args) {{
          super(redirectGameHubUrl(url), ...args);
          if (new URL(url).pathname.startsWith('/ws/room/')) {{
            roomConnections.add(this);
            window.abyssRoomConnectionCount++;
            this.addEventListener('message', event => {{
              if (window.abyssTrackChoiceFrames && this !== mutedConnection) {{
                const packet = JSON.parse(event.data);
                if (packet.type === 'abyss_state') window.abyssTrackChoiceFrames(packet.game);
              }}
              if ((!jitter && this !== mutedConnection) || JSON.parse(event.data).type !== 'abyss_state') return;
              if (this === mutedConnection && event.isTrusted) {{
                event.stopImmediatePropagation();
              }} else if (event.isTrusted && ++packet % 2 === 0) {{
                event.stopImmediatePropagation();
                setTimeout(() => {{
                  if (this.readyState === AppSocket.OPEN) this.dispatchEvent(new MessageEvent('message', {{data: event.data}}));
                }}, 70);
              }}
            }});
            this.addEventListener('message', event => {{
              if (JSON.parse(event.data).type === 'abyss_state') lastBattlePacket = event.data;
            }});
            this.addEventListener('close', () => roomConnections.delete(this), {{once: true}});
          }}
        }}
      }};
      window.disconnectAbyssTestRoom = () => {{
        for (const connection of roomConnections) connection.close(4000, 'E2E link interruption');
      }};
    ''')


def main(minutes):
    from urllib.parse import urlencode

    with isolated_backend() as (origin, in_match), sync_playwright() as p:
        from app.game import abyss

        browser = p.chromium.launch(headless=True)
        host, guest = [browser.new_page(viewport={'width': 360, 'height': 720}) for _ in range(2)]
        use_backend(host, origin)
        use_backend(guest, origin)
        # 默认开启的音频会在前面的移动/重连测试里持续解码和播放。这里先关掉，
        # 到声音专项再显式打开，避免音频负载把网络时序断言拖过截止时间。
        for page in (host, guest):
            page.add_init_script("""
              try {
                localStorage.setItem('abyss.audio.music', 'off');
                localStorage.setItem('abyss.audio.effects', 'off');
              } catch (error) {}
            """)
        host.add_init_script("""
          window.setAbyssTestVisibility = state => {
            Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
          };
          window.abyssDecodedClips = 0;
          window.abyssSfxStarts = 0;
          window.abyssSfxDurations = [];
          window.abyssSecondPickupInterrupted = false;
          window.abyssPickSends = 0;
          const socketSend = WebSocket.prototype.send;
          WebSocket.prototype.send = function (data) {
            if (typeof data === 'string' && JSON.parse(data).type === 'abyss_pick') {
              window.abyssPickSends++;
              if (window.abyssDropNextPick) {
                window.abyssDropNextPick = false;
                return;
              }
            }
            return socketSend.call(this, data);
          };
          const mediaPlay = HTMLMediaElement.prototype.play;
          HTMLMediaElement.prototype.play = function (...args) {
            if (this.src.includes('recovery-station')) {
              window.abyssMusicElement = this;
              if (window.abyssDenyNextMusic) {
                window.abyssDenyNextMusic = false;
                window.abyssDeniedMusic = this;
                return Promise.reject(new DOMException('Playback denied for test', 'NotAllowedError'));
              }
            }
            return mediaPlay.apply(this, args);
          };
          const resumeAudio = AudioContext.prototype.resume;
          AudioContext.prototype.resume = function (...args) {
            window.abyssSoundContext = this;
            return resumeAudio.apply(this, args);
          };
          const decode = AudioContext.prototype.decodeAudioData;
          AudioContext.prototype.decodeAudioData = function (...args) {
            return decode.apply(this, args).then(buffer => {
              window.abyssDecodedClips++;
              return buffer;
            });
          };
          const stop = AudioBufferSourceNode.prototype.stop;
          AudioBufferSourceNode.prototype.stop = function (...args) {
            if (this === window.abyssSecondPickup) window.abyssSecondPickupInterrupted = true;
            if (this === window.abyssFirstWeaponHit) window.abyssWeaponHitInterrupted = true;
            return stop.apply(this, args);
          };
          const play = AudioBufferSourceNode.prototype.start;
          AudioBufferSourceNode.prototype.start = function (...args) {
            window.abyssSfxStarts++;
            window.abyssSfxDurations.push(this.buffer.duration);
            if (window.abyssSfxStarts === 2) {
              window.abyssSecondPickup = this;
              this.playbackRate.value = .25;
            }
            if (window.abyssSfxStarts === 4) {
              window.abyssFirstWeaponHit = this;
              this.playbackRate.value = .1;
            }
            if (window.abyssSfxStarts === 5) {
              this.playbackRate.value = .08;
              this.addEventListener('ended', () => { window.abyssLongAlertEnded = true; }, {once: true});
            }
            return play.apply(this, args);
          };
        """)
        with host.expect_response(lambda response: response.url == origin + '/api/auth/register'
                                  and response.request.method == 'POST') as registered:
            register(host, UA)
        account = registered.value.json()
        token, host_id = account['token'], account['user']['id']
        register(guest, UB)
        goto_tab(host, '游戏')
        host.get_by_text('深渊回声', exact=True).first.click()
        with host.expect_response(lambda response: response.url == origin + '/api/rooms'
                                  and response.request.method == 'POST') as created:
            host.get_by_text('创建房间', exact=True).click()
        code = created.value.json()['code']
        host.get_by_test_id('guide-skip').click()
        host.get_by_test_id('help-btn-abyss').last.click()
        field_guide = host.get_by_test_id('abyss-field-guide')
        field_guide.get_by_text('针鳍鱼', exact=True).wait_for()
        assert field_guide.locator('svg').count() == 7
        assert field_guide.get_by_text('精英锈甲蟹', exact=True).count() == 1
        assert field_guide.locator('img[src*="cable-warden.webp"]').count() == 1
        for kind, (hp, speed, _) in abyss.ENEMIES.items():
            damage = abyss.CONTACT_DAMAGE.get(kind, 2)
            stats = field_guide.get_by_test_id(f'abyss-creature-stats-{kind}')
            assert stats.inner_text() == f'耐久 {hp} · 移速 {speed} · 接触伤害 {damage}'
        host.get_by_test_id('help-close-btn').click()
        ok('房间图鉴的八种怪物数值与服务器一致')
        if minutes != 12:
            host.get_by_test_id('room-settings-row').click()
            host.get_by_test_id(f'room-settings-duration-{minutes * 60}').click()
            host.get_by_test_id('room-settings-close').click()
        goto_tab(guest, '房间')
        guest.get_by_text('加入', exact=True).first.click()
        guest.get_by_placeholder('输入6位房间号').fill(code)
        guest.get_by_text('加入', exact=True).last.click()
        guest.get_by_test_id('guide-skip').click()
        guest.get_by_text('准 备', exact=True).click()
        host.get_by_text('开始游戏', exact=True).click()
        for page in (host, guest):
            page.get_by_test_id('abyss-arena').wait_for()
        ok('双人房间通过隔离后端开局')
        # 下面的移动 / 重连 / 低速断言默认玩家站着挨打；怪潮版本里自然刷怪会
        # 让两人在断言前掉血甚至倒地。这里先压住自然刷怪，后续用例按需手动
        # 生成敌人或恢复状态。
        in_match(code, lambda gs: (gs.update(spawn_budget=-10000), gs['enemies'].clear(), gs['caches'].clear()))

        def host_input():
            return in_match(code, lambda gs: (gs['players'][host_id]['dx'], gs['players'][host_id]['input_until']))

        touch = host.context.new_cdp_session(host)
        arena = host.get_by_test_id('abyss-arena').bounding_box()
        x, y = int(arena['x'] + arena['width'] / 2), int(arena['y'] + arena['height'] / 2)

        def gesture(event, points):
            touch.send('Input.dispatchTouchEvent', {
                'type': event, 'touchPoints': [{'x': px, 'y': py} for px, py in points],
            })

        def host_direction():
            return in_match(code, lambda gs: tuple(gs['players'][host_id][axis] for axis in ('dx', 'dy')))

        gesture('touchStart', [(x, y)])
        gesture('touchMove', [(x + 12, y + 16)])
        deadline = time.monotonic() + .5
        while any(abs(value - target) >= 1e-6 for value, target in zip(host_direction(), (.6, .8))):
            assert time.monotonic() < deadline, 'A short diagonal drag did not reach full movement speed'
            host.wait_for_timeout(10)
        gesture('touchMove', [(x + 2, y + 1)])
        deadline = time.monotonic() + .5
        while host_direction() != (0, 0):
            assert time.monotonic() < deadline, 'Returning to the joystick dead zone did not stop movement'
            host.wait_for_timeout(10)
        gesture('touchEnd', [])
        ok('轻推斜向摇杆以完整速度移动，回到死区就停下')

        gesture('touchStart', [(x, y)])
        gesture('touchMove', [(x + 55, y)])
        gesture('touchMove', [(x - 55, y)])
        deadline = time.monotonic() + .5
        while host_input()[0] != -1:
            assert time.monotonic() < deadline, 'Final touch direction did not reach the server'
            host.wait_for_timeout(10)
        gesture('touchMove', [(x + 55, y)])
        gesture('touchEnd', [])
        deadline = time.monotonic() + .5
        while host_input()[0] != 0:
            assert time.monotonic() < deadline, 'Touch release did not stop movement'
            host.wait_for_timeout(10)
        host.wait_for_timeout(130)
        assert host_input()[0] == 0, 'A queued touch direction resumed movement after release'
        def host_x():
            return in_match(code, lambda gs: gs['players'][host_id]['x'])

        before_flick = host_x()
        gesture('touchStart', [(x, y)])
        gesture('touchMove', [(x + 20, y)])
        gesture('touchEnd', [])
        deadline = time.monotonic() + .5
        while host_x() == before_flick:
            assert time.monotonic() < deadline, 'A short touch flick disappeared between simulation ticks'
            host.wait_for_timeout(8)
        host.wait_for_timeout(120)
        assert abs(host_x() - before_flick - abyss.data.PLAYER_SPEED * abyss.STEP) < 1e-6
        assert host_input() == (0, 0)
        ok('快速拨动触屏摇杆只走一个模拟步进，随后停步')

        gesture('touchStart', [(x, y)])
        gesture('touchMove', [(x + 55, y)])
        deadline = time.monotonic() + .5
        while host_direction() != (1, 0):
            assert time.monotonic() < deadline, 'Touch movement did not start before cancellation'
            host.wait_for_timeout(10)
        gesture('touchMove', [(x - 55, y)])
        gesture('touchCancel', [])
        deadline = time.monotonic() + .25
        while host_input() != (0, 0):
            assert time.monotonic() < deadline, 'System touch cancellation did not clear movement immediately'
            host.wait_for_timeout(10)
        stopped_at = host_x()
        host.wait_for_timeout(140)
        assert host_x() == stopped_at and host_input() == (0, 0)
        host.wait_for_function("""() => {
          const stick = document.querySelector('[data-testid="abyss-stick"]');
          return stick && getComputedStyle(stick).opacity === '0';
        }""")
        ok('系统取消触摸后立即停步并隐藏摇杆，排队方向不会重放')
        touch.detach()
        ok('触屏摇杆快速反向后保留最后方向，松手后不重放移动')

        heading = """([id, direction]) => {
          const player = document.querySelector('[data-testid="abyss-diver-' + id + '"]');
          const transforms = player?.querySelector(':scope > g')?.transform.baseVal;
          if (!transforms) return false;
          const horizontal = transforms.numberOfItems ? transforms.getItem(0).matrix.a : 1;
          return horizontal * direction > .95;
        }"""
        host.keyboard.down('a')
        host.wait_for_function(heading, arg=[host_id, -1])
        deadline = time.monotonic() + 1
        while in_match(code, lambda gs: abs(gs['players'][host_id]['angle'])) < 3:
            assert time.monotonic() < deadline, 'Server did not confirm the old heading'
            host.wait_for_timeout(10)
        host.keyboard.up('a')
        host.wait_for_timeout(160)
        host.evaluate('window.setAbyssTestMute(true)')
        try:
            host.keyboard.down('d')
            host.wait_for_function(heading, arg=[host_id, 1])
            host.keyboard.up('d')
            host.wait_for_timeout(65)
            assert host.evaluate(heading, [host_id, 1]), 'Releasing input snapped back to the old heading'
        finally:
            host.keyboard.up('d')
            host.evaluate('window.setAbyssTestMute(false)')
        ok('松开方向键后保留新朝向，等待服务器确认期间不回头')

        for panel, close in (('abyss-inventory', 'abyss-inventory-close'), ('abyss-audio', 'abyss-audio-close')):
            host.keyboard.down('d')
            deadline = time.monotonic() + 2
            while host_input()[0] != 1:
                assert time.monotonic() < deadline, f'Input did not start before opening {panel}'
                host.wait_for_timeout(10)
            host.get_by_test_id(panel).click()
            deadline = time.monotonic() + .4
            while host_input()[0] != 0:
                assert time.monotonic() < deadline, f'Input did not stop when opening {panel}'
                host.wait_for_timeout(10)
            host.get_by_test_id(close).click()
            host.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key: 'd', repeat: true}))")
            host.wait_for_timeout(130)
            assert host_input()[0] == 0, f'Old input resumed after closing {panel}'
            host.keyboard.up('d')
        ok('打开装备和声音面板时立即停步，关闭后旧输入不会恢复')

        before_tap = host_x()
        host.evaluate('''() => {
          window.dispatchEvent(new KeyboardEvent('keydown', {key: 'd'}));
          window.dispatchEvent(new KeyboardEvent('keyup', {key: 'd'}));
        }''')
        deadline = time.monotonic() + .6
        while host_x() == before_tap:
            assert time.monotonic() < deadline, 'A short key tap disappeared between simulation ticks'
            host.wait_for_timeout(10)
        host.wait_for_timeout(120)
        assert abs(host_x() - before_tap - abyss.data.PLAYER_SPEED * abyss.STEP) < 1e-6
        assert host_input() == (0, 0)
        ok('快速点按方向键只推进一个模拟步进，随后自动停步')

        host.keyboard.down('d')
        deadline = time.monotonic() + 2
        while host_input()[0] != 1:
            assert time.monotonic() < deadline
            host.wait_for_timeout(10)
        host.evaluate("window.setAbyssTestVisibility('hidden')")
        deadline = time.monotonic() + .4
        while host_input()[0] != 0:
            assert time.monotonic() < deadline, 'Backgrounding did not cancel movement'
            host.wait_for_timeout(10)
        host.evaluate("window.setAbyssTestVisibility('visible')")
        host.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key: 'd', repeat: true}))")
        host.wait_for_timeout(130)
        assert host_input()[0] == 0
        host.keyboard.up('d')
        ok('切到后台时停止移动，回到前台后旧按键不会恢复')

        host.keyboard.down('d')
        deadline = time.monotonic() + 3
        while host_input()[0] != 1:
            assert time.monotonic() < deadline, 'Host movement did not reach the server'
            host.wait_for_timeout(20)

        camera = host.get_by_test_id('abyss-world').evaluate('''world => new Promise(resolve => {
          const svg = world.ownerSVGElement;
          const viewBoxes = new Set();
          const positions = [];
          const start = performance.now();
          function sample(now) {
            viewBoxes.add(svg.getAttribute('viewBox'));
            positions.push(world.getCTM().e);
            if (now - start < 550) requestAnimationFrame(sample);
            else resolve({ viewBoxes: viewBoxes.size, positions });
          }
          requestAnimationFrame(sample);
        })''')
        positions = camera['positions']
        assert camera['viewBoxes'] == 1, 'Camera movement changed the SVG viewport'
        assert positions[0] - positions[-1] > 25, 'Camera did not follow horizontal movement'
        assert len(set(positions)) > 6, 'Camera did not move between server snapshots'
        ok('横向移动时镜头在快照之间更新，SVG 视口保持固定')

        guest.evaluate('window.setAbyssTestJitter(true)')
        guest.wait_for_timeout(950)
        remote = guest.get_by_test_id(f'abyss-diver-{host_id}').evaluate('''diver => new Promise(resolve => {
          let last = diver.transform.baseVal.getItem(0).matrix.e;
          let changedAt = performance.now();
          const start = changedAt;
          let longest = 0;
          let updates = 0;
          function sample(now) {
            const x = diver.transform.baseVal.getItem(0).matrix.e;
            if (x !== last) {
              updates++;
              longest = Math.max(longest, now - changedAt);
              last = x;
              changedAt = now;
            }
            if (now - start < 700) requestAnimationFrame(sample);
            else resolve({longest: Math.max(longest, now - changedAt), updates});
          }
          requestAnimationFrame(sample);
        })''')
        guest.evaluate('window.setAbyssTestJitter(false)')
        # Four 60 Hz frames take about 66.7 ms; require motion well between the usual 100 ms snapshots.
        assert remote['longest'] < 75 and remote['updates'] >= 25, remote
        ok('隔包增加 70 ms 延迟时，队友的持续移动仍能在快照间推进')

        host.evaluate('window.disconnectAbyssTestRoom()')
        host.get_by_text('正在重连…', exact=True).wait_for()
        peer = guest.get_by_test_id(f'abyss-team-status-{host_id}')
        peer.get_by_text('离线', exact=True).wait_for()
        assert host_input() == (0, 0)
        frame = in_match(code, lambda gs: gs['frame'])
        guest.wait_for_timeout(180)
        assert in_match(code, lambda gs: gs['frame']) > frame
        host.get_by_text('正在重连…', exact=True).wait_for(state='hidden')
        peer.get_by_text('离线', exact=True).wait_for(state='hidden')
        host.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', {key: 'd', repeat: true}))")
        host.wait_for_timeout(180)
        assert host_input() == (0, 0), 'Old movement resumed after reconnect'
        host.keyboard.up('d')
        ok('断开一端后队友继续作战，双方在线状态与旧输入正确恢复')

        connections = host.evaluate('window.abyssRoomConnectionCount')
        host.keyboard.down('d')
        deadline = time.monotonic() + 3
        while host_input()[0] != 1:
            assert time.monotonic() < deadline, 'Host could not move after reconnect'
            host.wait_for_timeout(20)
        prediction = host.get_by_test_id(f'abyss-diver-{host_id}').evaluate("""diver => new Promise(resolve => {
          window.setAbyssTestMute(true);
          const repeats = setInterval(window.repeatAbyssBattlePacket, 50);
          const read = () => diver.transform.baseVal.getItem(0).matrix.e;
          const origin = read();
          const start = performance.now();
          let peak = origin;
          let latePosition;
          let lateTravel = 0;
          function sample(now) {
            const x = read();
            peak = Math.max(peak, x);
            if (now - start >= 290) {
              latePosition ??= x;
              lateTravel = Math.max(lateTravel, x - latePosition);
            }
            if (now - start < 350) requestAnimationFrame(sample);
            else {
              clearInterval(repeats);
              resolve({travel: peak - origin, lateTravel});
            }
          }
          requestAnimationFrame(sample);
        })""")
        assert 3 < prediction['travel'] < 45 and prediction['lateTravel'] < .1, prediction
        ok('重复收到同一战斗帧时，本地只预测一小段移动，随后停住')
        status = host.get_by_test_id('abyss-network-status')
        status.get_by_text('网络延迟，等待战场更新…', exact=True).wait_for()
        deadline = time.monotonic() + 1
        while host_input()[0] != 1:
            assert time.monotonic() < deadline, 'Movement input was cleared during a short network delay'
            host.wait_for_timeout(20)
        ok('短时网络延迟先显示同步提示，暂不清除移动输入')
        status.get_by_text('战场同步中断，正在恢复…', exact=True).wait_for()
        assert host_input()[0] == 0
        host.evaluate("""() => {
          window.abyssSawStaleHit = false;
          window.abyssStaleHitObserver = new MutationObserver(() => {
            if (document.querySelector('[data-testid="abyss-local-hit"]')) window.abyssSawStaleHit = true;
          });
          window.abyssStaleHitObserver.observe(document.body, {childList: true, subtree: true});
        }""")
        def damage_during_interruption(gs):
            diver = gs['players'][host_id]
            before = diver['hp']
            abyss._hurt_diver(gs, diver, 5)
            return before - diver['hp']

        assert in_match(code, damage_during_interruption) > 0
        host.evaluate('window.setAbyssTestMute(false)')
        status.wait_for(state='hidden')
        host.wait_for_timeout(120)
        assert not host.evaluate("""() => {
          window.abyssStaleHitObserver.disconnect();
          return window.abyssSawStaleHit;
        }"""), 'A hit during interrupted sync flashed after the board resumed'
        assert host.evaluate('window.abyssRoomConnectionCount') == connections
        assert host_input()[0] == 0
        host.keyboard.up('d')

        touch = host.context.new_cdp_session(host)
        try:
            gesture('touchStart', [(x, y)])
            gesture('touchMove', [(x + 55, y)])
            deadline = time.monotonic() + 2
            while host_direction() != (1, 0):
                assert time.monotonic() < deadline, 'Touch movement did not start before interrupted sync'
                host.wait_for_timeout(10)
            host.evaluate('window.setAbyssTestMute(true)')
            status.get_by_text('战场同步中断，正在恢复…', exact=True).wait_for()
            assert host_direction() == (0, 0)
            repeats = host.evaluate('setInterval(window.repeatAbyssBattlePacket, 50)')
            try:
                host.wait_for_function('count => window.abyssRoomConnectionCount > count', arg=connections, timeout=6000)
            finally:
                host.evaluate('timer => clearInterval(timer)', repeats)
            status.wait_for(state='hidden')
            gesture('touchMove', [(x - 55, y)])
            host.wait_for_timeout(160)
            assert host_direction() == (0, 0), 'A pre-reconnect touch resumed movement'
            gesture('touchEnd', [])
            gesture('touchStart', [(x, y)])
            gesture('touchMove', [(x - 55, y)])
            deadline = time.monotonic() + 2
            while host_direction() != (-1, 0):
                assert time.monotonic() < deadline, 'A fresh touch did not respond after reconnecting'
                host.wait_for_timeout(10)
            gesture('touchEnd', [])
        finally:
            touch.detach()
        ok('断线时释放旧摇杆方向，重连后重新触碰即可移动')
        ok('短时同步中断后不补播旧受击反馈，持续停更时重复包无法阻止重连')

        in_match(code, lambda gs: [
            p.update(invincible=gs['elapsed'] + 100)
            for pid, p in gs['players'].items() if pid != host_id
        ])
        in_match(code, lambda gs: abyss._hurt_diver(gs, gs['players'][host_id], 24))
        hit_ring = host.get_by_test_id('abyss-local-hit')
        hit_ring.wait_for()
        assert guest.get_by_test_id('abyss-local-hit').count() == 0
        hit_ring.wait_for(state='hidden')
        ok('服务端确认受伤后，本地潜水员外圈短暂高亮')

        def enter_low_pressure(gs):
            diver = gs['players'][host_id]
            previous_hp = diver['hp']
            diver['hp'] = diver['max_hp'] / 4
            return previous_hp

        prior_hp = in_match(code, enter_low_pressure)
        max_hp = in_match(code, lambda gs: gs['players'][host_id]['max_hp'])
        expected_low = math.ceil(round(max_hp / 4, 1))
        warning_ring = host.get_by_test_id('abyss-low-pressure-ring')
        warning_ring.wait_for()
        host.get_by_text('低压警告', exact=True).wait_for()
        hit_ring.wait_for(state='hidden')
        assert warning_ring.count() == 1 and guest.get_by_test_id('abyss-low-pressure-ring').count() == 0
        peer_pressure = guest.get_by_test_id(f'abyss-team-status-{host_id}')
        peer_pressure.get_by_text(f'低压 · {expected_low} HP', exact=True).wait_for()
        assert peer_pressure.get_attribute('aria-label') == f'低压 · {expected_low} HP'
        in_match(code, lambda gs: gs['players'][host_id].update(hp=prior_hp))
        warning_ring.wait_for(state='detached')
        peer_pressure.get_by_text(f'{math.ceil(round(prior_hp, 1))} HP', exact=True).wait_for()
        ok('本人角色和队友面板都会提示低压，恢复耐久后自动撤下')

        def place_effects(gs):
            x, y = (gs['players'][host_id][axis] for axis in ('x', 'y'))
            abyss._effect(gs, 'charge', life=60, x=-400, y=y, r=24)
            abyss._effect(gs, 'charge', life=60, x=x, y=y, r=25)
            abyss._effect(gs, 'arc', life=60, points=[[-400, y], [1840, y]])
            return [effect['id'] for effect in gs['effects'][-3:]]

        offscreen, near, crossing = in_match(code, place_effects)
        host.get_by_test_id(f'abyss-effect-{near}').wait_for()
        host.get_by_test_id(f'abyss-effect-{crossing}').wait_for(state='attached')
        assert host.get_by_test_id(f'abyss-effect-{offscreen}').count() == 0
        def place_remote_diver(gs, together):
            lead = gs['players'][host_id]
            mate = next(player for player in gs['players'].values() if player['id'] != host_id)
            mate.update(x=lead['x'] + 60 if together else 32, y=lead['y'] if together else 32)
            return mate['id']

        mate_id = in_match(code, lambda gs: place_remote_diver(gs, True))
        remote_diver = host.get_by_test_id(f'abyss-diver-{mate_id}')
        remote_diver.wait_for()
        def separate_in_endless(gs):
            gs['map_size'] = 'endless'
            place_remote_diver(gs, False)

        in_match(code, separate_in_endless)
        remote_diver.wait_for(state='detached')
        host.get_by_test_id(f'abyss-team-status-{mate_id}').wait_for()
        regroup = host.get_by_test_id('abyss-objective')
        regroup.get_by_text('队友', exact=True).wait_for()
        assert (regroup.get_attribute('aria-label') or '').startswith(f'队友 {UB}，距离 ')

        def cast_past_the_viewport(gs):
            gs['players'][mate_id].update(x=-3000, y=32)
            abyss._effect(gs, 'pentagram', 'nova', life=14, x=-3000, y=32, r=1400)
            return gs['effects'][-1]['id']

        remote_nova_id = in_match(code, cast_past_the_viewport)
        remote_nova = host.get_by_test_id('abyss-distant-nova')
        remote_nova.wait_for(timeout=3000)
        assert remote_nova.get_attribute('fill') == 'none'
        assert host.get_by_test_id(f'abyss-effect-{remote_nova_id}').count() == 0
        remote_nova.wait_for(state='detached', timeout=3000)
        ok('无尽地图上的远处清屏以淡出边框提示，不绘制屏外技能圈')
        regroup_distance = host.get_by_test_id('abyss-regroup-distance')
        far_distance = int((regroup_distance.text_content() or '').split()[0])
        in_match(code, lambda gs: gs['players'][mate_id].update(x=-2400))
        host.wait_for_function("""far => {
          const label = document.querySelector('[data-testid="abyss-regroup-distance"]');
          return label && Number.parseInt(label.textContent, 10) < far - 500;
        }""", arg=far_distance)
        ok('无尽地图的队友箭头显示实时距离')

        def fill_beacon_pool(gs):
            lead = gs['players'][host_id]
            mate = gs['players'][mate_id]
            previous = gs['loot'], gs['xp'], mate['xp_collected']
            gs['loot'] = []
            for offset in range(abyss.MAX_LOOT):
                abyss._drop_loot(gs, {'x': lead['x'] + 125, 'y': lead['y'] + offset / 10}, 1)
            contact = {'x': mate['x'] - 100, 'y': mate['y']}
            abyss._drop_loot(gs, contact, 1)
            return previous, contact

        previous, contact = in_match(code, fill_beacon_pool)
        guest.get_by_test_id('abyss-loot').first.wait_for()
        host.wait_for_function("""count => document.querySelectorAll('[data-testid="abyss-loot"]').length === count""",
                               arg=abyss.MAX_LOOT - 1)
        assert guest.get_by_test_id('abyss-loot').count() == 1
        in_match(code, lambda gs: gs['players'][mate_id].update(contact))
        guest.get_by_test_id('abyss-loot').wait_for(state='detached')

        def restore_beacons(gs):
            mate = gs['players'][mate_id]
            assert mate['xp_collected'] == previous[2] + 1
            gs['loot'], gs['xp'], mate['xp_collected'] = previous

        in_match(code, restore_beacons)
        ok('信标满额时远处掉落只出现在当地队友视口，并可在原地拾取')

        in_match(code, lambda gs: place_remote_diver(gs, True))
        remote_diver.wait_for()
        regroup.wait_for(state='hidden')
        in_match(code, lambda gs: gs.update(map_size='medium'))
        ok('无尽地图队友在屏外时可跟随汇合箭头，进入视野后提示消失')
        ok('远处队友离开画面时跳过角色绘制，小队状态持续可见')
        ok('屏外爆炸跳过绘制，横穿视口的电弧仍可见')

        def launch_harpoon(gs):
            diver = gs['players'][host_id]
            x, y = round(diver['x']), round(diver['y'] + 100)
            stats = abyss.data.weapon_stats('harpoon', 1, diver['stats'])
            # 慢速长寿命弹道：给负载较高的测试机留出首份快照的渲染窗口。
            abyss._shot(gs, diver, 'harpoon', stats, x, y, 400, 0,
                        kind='harpoon', life=60, radius=7)
            return gs['shots'][-1]['id'], x

        host.get_by_test_id('abyss-world').evaluate('''world => {
          window.abyssFirstHarpoon = null;
          const observer = new MutationObserver(() => {
            const node = world.querySelector('[data-testid^="abyss-shot-"]');
            if (!node) return;
            window.abyssFirstHarpoon = {
              id: node.dataset.testid,
              x: node.transform.baseVal.getItem(0).matrix.e,
              sprite: node.getAttribute('href') ?? node.getAttribute('xlink:href'),
              drawnWidth: node.getBBox().width,
            };
            observer.disconnect();
          });
          observer.observe(world, { childList: true, subtree: true });
        }''')
        shot_id, origin_x = in_match(code, launch_harpoon)
        shot = host.wait_for_function('window.abyssFirstHarpoon').json_value()
        assert shot['id'] == f'abyss-shot-{shot_id}'
        assert shot['sprite'].endswith('-shot-harpoon') and shot['drawnWidth'] > 0
        assert origin_x <= shot['x'] < origin_x + 65, (origin_x, shot['x'])
        ok('鱼叉在首份快照渲染期间从发射位置向前推进')

        def hit_at_muzzle(gs):
            diver = gs['players'][host_id]
            x, y = round(diver['x'] - 100), round(diver['y'] + 160)
            abyss._spawn(gs, 'drifter', diver)
            gs['enemies'][-1].update(x=x + 15, y=y, speed=0, hp=1000, radius=18)
            stats = abyss.data.weapon_stats('harpoon', 1, diver['stats'])
            stats['crit'] = 0
            abyss._shot(gs, diver, 'harpoon', stats, x, y, 1400, 0,
                        kind='harpoon', life=12, pierce=1, radius=7)
            return x, y, gs['shots'][-1]['id']

        hit_x, hit_y, consumed_shot = in_match(code, hit_at_muzzle)
        host.wait_for_function('''([x, y]) => [...document.querySelectorAll('[data-testid^="abyss-projectile-impact-"]')]
          .some(node => {
            const point = node.transform.baseVal.getItem(0).matrix;
            const sprite = node.getAttribute('href') ?? node.getAttribute('xlink:href');
            return point.e === x && point.f === y && node.localName === 'use'
              && sprite?.endsWith('-impact-harpoon-0') && node.getBBox().width > 0;
          })''', arg=[hit_x, hit_y], timeout=4000)
        assert host.get_by_test_id(f'abyss-shot-{consumed_shot}').count() == 0
        ok('贴身鱼叉在下一份快照前被消耗，仍在实际接触点显示火花')

        def launch_observed_shot(gs):
            diver = gs['players'][host_id]
            x, y = round(diver['x'] - 120), round(diver['y'] - 185)
            stats = abyss.data.weapon_stats('harpoon', 1, diver['stats'])
            abyss._shot(gs, diver, 'harpoon', stats, x, y, 160, 0,
                        kind='harpoon', life=60, pierce=1, radius=7)
            return gs['shots'][-1]['id'], y

        observed_id, observed_y = in_match(code, launch_observed_shot)
        host.get_by_test_id(f'abyss-shot-{observed_id}').wait_for()
        host.evaluate("""([id, y]) => {
          window.abyssTerminalImpactAligned = new Promise(resolve => {
            const observer = new MutationObserver(() => {
              if (document.querySelector('[data-testid="abyss-shot-' + id + '"]')) return;
              observer.disconnect();
              resolve([...document.querySelectorAll('[data-testid^="abyss-projectile-impact-"]')].some(node =>
                node.transform.baseVal.getItem(0).matrix.f === y));
            });
            observer.observe(document.querySelector('[data-testid="abyss-world"]'), {childList: true, subtree: true});
          });
        }""", [observed_id, observed_y])

        def intercept_observed_shot(gs):
            shot = next(shot for shot in gs['shots'] if shot['id'] == observed_id)
            abyss._spawn(gs, 'drifter', gs['players'][host_id])
            gs['enemies'][-1].update(x=shot['x'] + 20, y=shot['y'], hp=1000, radius=18, speed=0)

        in_match(code, intercept_observed_shot)
        host.get_by_test_id(f'abyss-shot-{observed_id}').wait_for(state='detached', timeout=3000)
        assert host.evaluate('window.abyssTerminalImpactAligned')
        ok('已经可见的鱼叉在命中火花出现的同一帧消失')

        center = [abyss.WIDTH / 2, abyss.HEIGHT / 2]
        in_match(code, lambda gs: gs['players'][host_id].update(x=center[0], y=center[1]))
        host.wait_for_function("""([id, x, y]) => {
          const diver = document.querySelector('[data-testid="abyss-diver-' + id + '"]');
          const position = diver?.transform.baseVal.getItem(0).matrix;
          return position && Math.hypot(position.e - x, position.f - y) < 1;
        }""", arg=[host_id, *center])

        def seed_impact_cloud(gs):
            diver = gs['players'][host_id]
            xs = [diver['x'] + dx for dx in range(-126, 127, 36)]
            ys = [diver['y'] + dy for dy in range(-126, 127, 36)]
            ids = []
            for x in xs:
                for y in ys:
                    abyss._effect(gs, 'axe', 'impact', life=5, x=x, y=y, r=16, angle=math.pi / 2)
                    ids.append(gs['effects'][-1]['id'])
            offscreen = []
            for x in xs:
                abyss._effect(gs, 'axe', 'impact', life=5, x=x, y=0, r=16, angle=math.pi / 2)
                offscreen.append(gs['effects'][-1]['id'])
            return ids, offscreen

        impact_ids, offscreen_ids = in_match(code, seed_impact_cloud)
        host.wait_for_function("""([ids, offscreen]) => {
          const sparks = ids.map(id => document.querySelector('[data-testid="abyss-projectile-impact-' + id + '"]'));
          const batched = sparks.filter(spark => spark?.closest('[data-testid="abyss-impact-group"]'));
          return batched.length >= 12 && offscreen.every(id =>
            !document.querySelector('[data-testid="abyss-projectile-impact-' + id + '"]')) && sparks.every(spark => {
            const sprite = spark?.getAttribute('href') ?? spark?.getAttribute('xlink:href');
            return spark?.localName === 'use' && spark.childElementCount === 0 && sprite?.endsWith('-impact-axe-1')
              && spark.getBBox().width > 0 && Math.abs(spark.transform.baseVal.getItem(1).angle - 90) < 1;
          });
        }""", arg=[impact_ids, offscreen_ids], timeout=4000)
        ok('64 个重击火花保留分组淡出与方向，同组屏外火花不挂载')

        def spawn_defeat_target(gs):
            diver = gs['players'][host_id]
            abyss._spawn(gs, 'drifter', diver)
            enemy = gs['enemies'][-1]
            enemy.update(x=round(diver['x'] + 110), y=round(diver['y'] + 65), hp=1000, speed=0)
            return enemy['id'], enemy['x'], enemy['y']

        target_id, target_x, target_y = in_match(code, spawn_defeat_target)
        host.get_by_test_id(f'abyss-enemy-{target_id}').wait_for()
        host.evaluate("""([id, x, y]) => {
          window.abyssDefeatAligned = new Promise(resolve => {
            const observer = new MutationObserver(() => {
              if (document.querySelector('[data-testid="abyss-enemy-' + id + '"]')) return;
              const aligned = [...document.querySelectorAll('[data-testid^="abyss-defeat-"]')].some(node => {
                const transform = node.transform.baseVal.getItem(0).matrix;
                return transform.e === x && transform.f === y;
              });
              observer.disconnect();
              resolve(aligned);
            });
            observer.observe(document.querySelector('[data-testid="abyss-world"]'), {childList: true, subtree: true});
          });
        }""", [target_id, target_x, target_y])

        def defeat_foe(gs):
            diver = gs['players'][host_id]
            enemy = next(enemy for enemy in gs['enemies'] if enemy['id'] == target_id)
            abyss._damage(gs, diver, enemy, enemy['hp'])
            return gs['effects'][-1]['id']

        defeat_id = in_match(code, defeat_foe)
        defeat_burst = host.get_by_test_id(f'abyss-defeat-{defeat_id}')
        defeat_burst.wait_for(timeout=3000)
        assert host.evaluate('window.abyssDefeatAligned')
        assert defeat_burst.evaluate('node => node.getBBox().width') > 0
        defeat_burst.wait_for(state='detached', timeout=3000)
        ok('怪物消失与最后一击的击破光环在同一渲染帧')

        def bounce_rune(gs):
            diver = gs['players'][host_id]
            width, _ = abyss._bounds(gs)
            diver.update(x=width - 120, y=500)
            stats = abyss.data.weapon_stats('rune', 1, diver['stats'])
            abyss._shot(gs, diver, 'rune', stats, width - 22, 600, 800, 0,
                        kind='rune', life=18, radius=12, bounce=True)
            rune_id = gs['shots'][-1]['id']
            stats = abyss.data.weapon_stats('charge', 1, diver['stats'])
            abyss._shot(gs, diver, 'charge', stats, diver['x'], 540, -200, 0,
                        kind='fire', life=18, radius=24)
            return rune_id

        rune_id = in_match(code, bounce_rune)
        rune = host.get_by_test_id(f'abyss-rune-{rune_id}')
        rune.wait_for()
        assert rune.evaluate("""node => {
          const href = node.getAttribute('href') ?? node.getAttribute('xlink:href');
          return node.localName === 'use' && node.ownerSVGElement.querySelector(href)?.querySelector('line')
            && node.getBBox().width > 0;
        }""")
        assert abs(abs(rune.evaluate('node => node.transform.baseVal.getItem(1).angle')) - 180) < 1
        fireball = host.locator(r'use[href$="-shot-charge"], use[xlink\:href$="-shot-charge"]').first
        fireball.wait_for()
        assert fireball.evaluate('node => node.transform.baseVal.getItem(2).matrix.a') == 2
        host.wait_for_function("""async () => {
          const board = document.querySelector('[data-testid="abyss-world"]')?.ownerSVGElement;
          if (!board) return false;
          const names = ['shot-harpoon', 'shot-charge', 'shot-rune', 'area-bible', 'area-la_borra'];
          return (await Promise.all(names.map(async name => {
            const image = board.querySelector(`g[id$="-${name}"] image`);
            const src = image?.href.baseVal;
            if (!src || !src.includes('/items/')) return false;
            const asset = new Image();
            asset.src = src;
            try { await asset.decode(); } catch { return false; }
            return asset.naturalWidth > 0;
          }))).every(Boolean);
        }""")
        ok('火球和声呐弹复用 SVG，火球按半径缩放，声呐亮纹随反弹转向')

        def place_dense_wave(gs):
            gs['spawn_budget'] = -1000
            gs['enemies'].clear()
            diver = gs['players'][host_id]
            diver.update(x=abyss.WIDTH / 2, y=abyss.HEIGHT / 2)
            kinds = ('drifter', 'eel', 'crab', 'needlefish', 'jelly', 'angler')
            for index in range(108):
                abyss._spawn(gs, kinds[index % len(kinds)], diver)
                gs['enemies'][-1].update(
                    x=diver['x'] + (index % 12 - 5.5) * 26,
                    y=diver['y'] - 165 + index // 12 * 14,
                    speed=0, hp=1000, max_hp=1000,
                )
            abyss._spawn(gs, 'elite', diver)
            gs['enemies'][-1].update(x=diver['x'] + 197, y=diver['y'] - 185, speed=0)
            gs['loot'].clear()
            for index in range(120):
                abyss._drop_loot(gs, {
                    'x': diver['x'] + (index % 12 - 5.5) * 26,
                    'y': diver['y'] + 135 + index // 12 * 12,
                }, 1)

        in_match(code, place_dense_wave)
        host.wait_for_function('''() => {
          const href = node => node.getAttribute('href') ?? node.getAttribute('xlink:href');
          const instances = [...document.querySelectorAll('[data-testid="abyss-world"] use')]
            .filter(node => href(node)?.includes('-creature-'));
          const elite = instances.find(node => href(node)?.endsWith('-elite'));
          const crab = instances.find(node => href(node)?.endsWith('-crab'));
          if (instances.length !== 109 || !elite || !crab) return false;
          return new Set(instances.map(href)).size === 7
            && instances.every(node => node.getBBox().width > 0)
            && elite.getBBox().height > crab.getBBox().height * 1.4;
        }''')
        ok('同屏 108 只普通怪物和 1 只精英复用 7 套 SVG 定义，精英剪影可区分')

        def overfill_wave(gs):
            gs['spawn_budget'] = -1000
            gs['enemies'].clear()
            diver = gs['players'][host_id]
            diver.update(x=abyss.WIDTH / 2, y=abyss.HEIGHT / 2)
            for index in range(220):
                abyss._spawn(gs, 'drifter', diver)
                gs['enemies'][-1].update(
                    x=diver['x'] + (index % 20 - 9.5) * 22,
                    y=diver['y'] + (index // 20 - 5.5) * 22,
                    speed=0, hp=1000, max_hp=1000,
                )

        in_match(code, overfill_wave)
        host.wait_for_function('''() => document.querySelectorAll('[data-testid^="abyss-enemy-"]').length === 160''')
        ok('同屏超过 160 只普通怪时只挂载最近 160 个 SVG 实例')

        in_match(code, place_dense_wave)
        host.wait_for_function('''() => {
          const href = node => node.getAttribute('href') ?? node.getAttribute('xlink:href');
          return [...document.querySelectorAll('[data-testid="abyss-world"] use')]
            .filter(node => href(node)?.includes('-creature-')).length === 109;
        }''')

        def slow_one_enemy(gs):
            enemy = gs['enemies'][0]
            abyss._damage(gs, gs['players'][host_id], enemy, 1, slow=.5, slow_ticks=200)
            return enemy['id']

        slowed_id = in_match(code, slow_one_enemy)
        slow_mark = host.get_by_test_id(f'abyss-slow-{slowed_id}')
        slow_mark.wait_for()
        assert slow_mark.evaluate('node => (node.getAttribute("href") ?? node.getAttribute("xlink:href")).endsWith("-slow-mark")')
        ok('减速中的怪物显示虚线冰环 + 冰棱标记')

        beacons = host.get_by_test_id('abyss-loot')
        host.wait_for_function('''() => {
          const nodes = [...document.querySelectorAll('[data-testid="abyss-loot"]')];
          return nodes.length === 120 && nodes.every(node => node.localName === 'use'
            && (node.getAttribute('href') ?? node.getAttribute('xlink:href')).endsWith('-beacon')
            && node.getBBox().width > 0);
        }''')
        assert beacons.count() == 120
        ok('120 个同屏经验信标各只挂载一个 SVG 实例')
        hit_enemy = in_match(code, lambda gs: gs['enemies'][0]['id'])
        host.get_by_test_id(f'abyss-enemy-{hit_enemy}').evaluate('''enemy => {
          window.abyssEnemyRecoiled = false;
          const observer = new MutationObserver(() => {
            if (enemy.getAttribute('transform').includes('scale(')) {
              window.abyssEnemyRecoiled = true;
              observer.disconnect();
            }
          });
          observer.observe(enemy, { attributes: true, attributeFilter: ['transform'] });
        }''')
        in_match(code, lambda gs: abyss._damage(gs, gs['players'][host_id], gs['enemies'][0], 25))
        host.wait_for_function('''id => {
          const enemy = document.querySelector('[data-testid="abyss-enemy-' + id + '"]');
          return window.abyssEnemyRecoiled && enemy && !enemy.getAttribute('transform').includes('scale(');
        }''', arg=hit_enemy, timeout=4000)
        ok('服务器确认命中后普通怪物短暂变形，随后恢复原剪影')

        host.emulate_media(reduced_motion='reduce')
        host.wait_for_timeout(100)
        enemy = host.get_by_test_id(f'abyss-enemy-{hit_enemy}')
        enemy.evaluate('''node => {
          window.abyssReducedRecoil = false;
          const observer = new MutationObserver(() => {
            window.abyssReducedRecoil ||= node.getAttribute('transform').includes('scale(');
          });
          observer.observe(node, { attributes: true, attributeFilter: ['transform'] });
          setTimeout(() => observer.disconnect(), 500);
        }''')
        in_match(code, lambda gs: abyss._damage(gs, gs['players'][host_id], gs['enemies'][0], 25))
        host.wait_for_timeout(500)
        assert not host.evaluate('window.abyssReducedRecoil')
        ok('开启系统减少动态效果后，怪物受击保持原剪影')

        def seed_reduced_bursts(gs):
            lead = gs['players'][host_id]
            abyss._effect(gs, 'charge', 'burst', life=14, x=lead['x'], y=lead['y'], r=62)
            abyss._effect(gs, 'pentagram', 'nova', life=14, x=lead['x'], y=lead['y'], r=170)
            return [effect['id'] for effect in gs['effects'][-2:]]

        burst, nova = in_match(code, seed_reduced_bursts)
        assert host.get_by_test_id(f'abyss-effect-{burst}').get_attribute('r') == '62'
        nova_effect = host.get_by_test_id(f'abyss-effect-{nova}')
        assert nova_effect.locator('circle').first.get_attribute('r') == '170'
        host.wait_for_function("""id => {
          const effect = document.querySelector('[data-testid="abyss-effect-' + id + '"]');
          const opacity = effect ? Number(effect.getAttribute('opacity')) : 0;
          return opacity > .25 && opacity < .75;
        }""", arg=nova, timeout=3000)
        nova_effect.wait_for(state='detached')
        host.emulate_media(reduced_motion='no-preference')
        ok('减少动态效果时爆炸和清屏保持技能范围，清屏光圈按实际时长淡出')


        def start_dense_pursuit(gs):
            for enemy in gs['enemies']:
                enemy['speed'] = abyss.ENEMIES[enemy['kind']][1]
            for player in gs['players'].values():
                player['invincible'] = gs['elapsed'] + 60
            return {enemy['id']: (enemy['x'], enemy['y']) for enemy in gs['enemies']}

        initial_positions = in_match(code, start_dense_pursuit)
        assert len(initial_positions) == 109

        host.keyboard.down('d')
        try:
            frame_stats = host.get_by_test_id('abyss-world').evaluate('''world => new Promise(resolve => {
              let first;
              let last;
              let x;
              const changes = [];
              const pursuing = world.querySelector('[data-testid^="abyss-enemy-"]');
              const enemyPositions = new Set();
              function sample(now) {
                first ??= now;
                const next = world.transform.baseVal.getItem(0).matrix.e;
                const enemy = pursuing.transform.baseVal.getItem(0).matrix;
                enemyPositions.add(enemy.e + ',' + enemy.f);
                if (next !== x) {
                  if (last !== undefined) changes.push(now - last);
                  last = now;
                  x = next;
                }
                if (now - first < 900) requestAnimationFrame(sample);
                else {
                  changes.sort((a, b) => a - b);
                  const percentile = q => changes[Math.ceil(changes.length * q) - 1];
                  resolve({ updates: changes.length, enemyUpdates: enemyPositions.size, median: percentile(.5), p95: percentile(.95) });
                }
              }
              requestAnimationFrame(sample);
            })''')
        finally:
            host.keyboard.up('d')
        moved = in_match(code, lambda gs: sum(
            initial_positions[enemy['id']] != (enemy['x'], enemy['y']) for enemy in gs['enemies']
        ))
        assert moved == len(initial_positions), moved
        assert min(frame_stats['updates'], frame_stats['enemyUpdates']) > 20, frame_stats
        ok(f'109 只怪物同屏移动，900 ms 内镜头更新 {frame_stats["updates"]} 次，'
           f'间隔中位数 {frame_stats["median"]:.1f} ms，P95 {frame_stats["p95"]:.1f} ms')

        def prepare_pickup_audio(gs):
            # 音频用例只关心提示音优先级：把等级抬到 10 级，避免几次 1 XP
            # 拾取触发升级提示音；同时清掉可能残留的选卡暂停。
            gs.update(xp=0, level=10, pick_until=0, spawn_budget=-1000)
            gs['enemies'].clear()
            gs['loot'].clear()
            for player in gs['players'].values():
                player['invincible'] = 0
                player['choices'] = []

        in_match(code, prepare_pickup_audio)
        host.get_by_test_id('abyss-audio').click()
        host.get_by_test_id('abyss-music').click()
        host.wait_for_function('''window.abyssMusicElement?.loop
            && !abyssMusicElement.paused && abyssMusicElement.volume === .55 && abyssMusicElement.duration > 70''')
        host.get_by_test_id('abyss-music-level-quiet').click()
        host.wait_for_function('abyssMusicElement.volume === .25')
        host.get_by_test_id('abyss-music-level-standard').click()
        host.wait_for_function('abyssMusicElement.volume === .55')
        ok('配乐音量可独立调整，切换档位即时生效')
        host.evaluate("window.setAbyssTestVisibility('hidden')")
        host.wait_for_function('abyssMusicElement.paused')
        host.evaluate("window.setAbyssTestVisibility('visible')")
        host.wait_for_function('!abyssMusicElement.paused')
        ok('切到后台暂停配乐，返回后从当前位置恢复')
        host.evaluate('abyssMusicElement.currentTime = abyssMusicElement.duration - .14')
        host.wait_for_function('!abyssMusicElement.paused && abyssMusicElement.currentTime < 2')
        ok('72 秒配乐实际播放到结尾并循环')
        host.get_by_test_id('abyss-sfx').click()
        host.wait_for_function('window.abyssDecodedClips === 11')
        host.get_by_test_id('abyss-audio-close').click()
        # 从拾取音测试开始重新计数，避免开关音效前的提示音干扰。
        host.evaluate('window.abyssSfxStarts = 0; window.abyssSfxDurations = []')

        def pickup(gs, mine):
            diver = next(player for player in gs['players'].values()
                         if (player['id'] == host_id) == mine)
            abyss._drop_loot(gs, diver, 1)

        in_match(code, lambda gs: pickup(gs, False))
        host.wait_for_timeout(220)
        assert host.evaluate('window.abyssSfxStarts') == 0
        in_match(code, lambda gs: pickup(gs, True))
        host.wait_for_function('window.abyssSfxStarts === 1')
        in_match(code, lambda gs: pickup(gs, True))
        host.wait_for_timeout(220)
        assert host.evaluate('window.abyssSfxStarts') == 1
        host.wait_for_timeout(350)
        in_match(code, lambda gs: pickup(gs, True))
        host.wait_for_function('window.abyssSfxStarts === 2')

        def hit_and_pickup(gs):
            abyss._hurt_diver(gs, gs['players'][host_id], 5)
            pickup(gs, True)

        in_match(code, hit_and_pickup)
        host.wait_for_function('window.abyssSfxStarts === 3')
        first_pickup, second_pickup, hit = host.evaluate('window.abyssSfxDurations')
        assert first_pickup == second_pickup and hit != first_pickup
        assert host.evaluate('window.abyssSecondPickupInterrupted'), 'Damage cue did not interrupt active pickup audio'
        in_match(code, lambda gs: pickup(gs, True))
        host.wait_for_timeout(230)
        assert host.evaluate('window.abyssSfxStarts') == 3, 'Pickup interrupted the damage cue'
        ok('受伤提示打断正在播放的拾取声，并短时跳过后续拾取音')
        host.wait_for_timeout(350)

        def weapon_contact(gs, mine):
            diver = next(player for player in gs['players'].values()
                         if (player['id'] == host_id) == mine)
            abyss._spawn(gs, 'drifter', diver)
            abyss._damage(gs, diver, gs['enemies'][-1], 1)
            gs['enemies'].pop()

        in_match(code, lambda gs: weapon_contact(gs, False))
        host.wait_for_timeout(150)
        assert host.evaluate('window.abyssSfxStarts') == 3
        in_match(code, lambda gs: weapon_contact(gs, True))
        host.wait_for_function('window.abyssSfxStarts === 4')
        in_match(code, lambda gs: weapon_contact(gs, True))
        host.wait_for_timeout(160)
        assert host.evaluate('window.abyssSfxStarts') == 4
        ok('武器命中声仅提示本人，连续命中限频')

        def interrupt_weapon_contact(gs):
            diver = gs['players'][host_id]
            diver['invincible'] = 0
            abyss._hurt_diver(gs, diver, 5)

        in_match(code, interrupt_weapon_contact)
        host.wait_for_function('window.abyssSfxStarts === 5 && window.abyssWeaponHitInterrupted === true')
        ok('受伤提示会中断尚在播放的武器命中音')
        host.wait_for_timeout(350)
        in_match(code, lambda gs: weapon_contact(gs, True))
        host.wait_for_timeout(150)
        audio_state = host.evaluate('[window.abyssSfxStarts, window.abyssLongAlertEnded]')
        assert audio_state[0] == 5 and not audio_state[1], audio_state
        host.wait_for_function('window.abyssLongAlertEnded')
        ok('提示音持续播放时，常规命中即使过了限频间隔也不会抢占声音')


        def finish_enemy(gs):
            diver = gs['players'][host_id]
            abyss._spawn(gs, 'drifter', diver)
            enemy = gs['enemies'].pop()
            abyss._damage(gs, diver, enemy, enemy['hp'], drop_chance=0)

        in_match(code, finish_enemy)
        host.wait_for_function('''window.abyssSfxStarts === 6
            && Math.abs(window.abyssSfxDurations[5] - .24) < .005''')
        in_match(code, finish_enemy)
        host.wait_for_timeout(160)
        assert host.evaluate('window.abyssSfxStarts') == 6
        ok('本人击破使用独立的低频音效，连续击破限频')
        host.get_by_test_id('abyss-audio').click()
        host.get_by_test_id('abyss-sfx').click()
        host.get_by_test_id('abyss-audio-close').click()
        ok('拾取提示音仅为本人播放，快速连续拾取时限频')



        host.get_by_test_id('abyss-local-hit').wait_for(state='detached')
        in_match(code, lambda gs: pickup(gs, True))
        pickup_flash = host.get_by_test_id('abyss-local-pickup')
        pickup_flash.wait_for()
        assert guest.get_by_test_id('abyss-local-pickup').count() == 0
        pickup_flash.wait_for(state='detached')
        assert host.evaluate('window.abyssSfxStarts') == 6
        ok('关闭音效后本地经验拾取仍有短暂视觉反馈，队友画面不受影响')

        def seed_moving_objects(gs):
            lead = gs['players'][host_id]
            gs['spawn_budget'] = -1000
            gs['enemies'].clear()
            gs['loot'].clear()
            lead['weapons'].update(bible=1, la_borra=1)
            lead['passives']['attractorb'] = 5
            abyss._refresh_stats(lead)

        in_match(code, seed_moving_objects)
        for name in ('abyss-orbit', 'abyss-field'):
            host.get_by_test_id(name).first.wait_for()

        def drop_beacon(gs):
            lead = gs['players'][host_id]
            mate = next(player for player in gs['players'].values() if player['id'] != host_id)
            side = 1 if lead['x'] >= mate['x'] else -1
            abyss._drop_loot(gs, {'x': lead['x'] + side * 215, 'y': lead['y']}, 1)

        # Exaggerate angular travel to expose a straight-line shortcut across the orbit.
        in_match(code, lambda gs: gs['players'][host_id]['orbits']['bible'].update(speed=14))
        host.get_by_test_id('abyss-arena').evaluate("""(arena, playerId) => {
          const ids = ['abyss-loot', 'abyss-orbit', 'abyss-field'];
          const samples = ids.map(() => new Set());
          const radii = new Set();
          const orbitDistances = [];
          const player = arena.querySelector(`[data-testid="abyss-diver-${playerId}"]`);
          const deadline = performance.now() + 1700;
          let startedAt;
          window.abyssMotionSamples = null;
          function frame(now) {
            const targets = ids.map(id => arena.querySelector(`[data-testid="${id}"]`));
            if (targets[0]) startedAt ??= now;
            if (startedAt !== undefined) targets.forEach((target, i) => {
              if (!target) return;
              const value = i === 2 ? [target.getAttribute('cx'), target.getAttribute('cy')].join(',')
                : target.getAttribute('transform');
              samples[i].add(value);
              if (i === 1) {
                const orbit = target.transform.baseVal.getItem(0).matrix;
                const center = player.transform.baseVal.getItem(0).matrix;
                orbitDistances.push(Math.hypot(orbit.e - center.e, orbit.f - center.f));
              }
              if (i === 2) radii.add(target.getAttribute('r'));
            });
            if (now >= deadline || startedAt !== undefined && now - startedAt >= 260) {
              window.abyssMotionSamples = [...samples.map(sample => sample.size), radii.size];
              window.abyssOrbitSpread = Math.max(...orbitDistances) - Math.min(...orbitDistances);
            } else {
              requestAnimationFrame(frame);
            }
          }
          requestAnimationFrame(frame);
        }""", host_id)
        in_match(code, drop_beacon)
        host.wait_for_function('window.abyssMotionSamples !== null')
        moving_steps = host.evaluate('window.abyssMotionSamples')
        assert min(moving_steps) >= 6, moving_steps
        orbit_spread = host.evaluate('window.abyssOrbitSpread')
        assert orbit_spread <= 3, f'Orbit radius varied by {orbit_spread:.2f} units between snapshots'
        ok('飞向玩家的信标、环绕浮标和水洼均平滑移动，快速浮标维持轨道半径，水洼半径逐帧变化')

        def start_rescue(gs):
            lead, mate = gs['players'].values()
            lead.update(x=700, y=500)
            mate.update(x=770, y=500, invincible=0)
            abyss._hurt_diver(gs, mate, mate['max_hp'])

        in_match(code, start_rescue)
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('等待队友靠近', exact=False).wait_for()
        host.get_by_test_id('abyss-rescue-notice').get_by_text(f'靠近 {UB} · 进入救援圈', exact=True).wait_for()
        in_match(code, lambda gs: gs['players'][host_id].update(x=729))
        host.get_by_test_id('abyss-rescue-notice').get_by_text(re.compile(rf'救援 {re.escape(UB)} · \d+% · 剩 \d+s')).wait_for()
        guest.get_by_test_id('abyss-rescue-notice').get_by_text(re.compile(rf'{re.escape(UA)}正在救援 · \d+% · 剩 \d+s')).wait_for()
        rescue_arc = host.get_by_test_id('abyss-rescue-progress')
        values = rescue_arc.evaluate("""arc => new Promise(resolve => {
          const offsets = new Set();
          const start = performance.now();
          function sample(now) {
            offsets.add(Number(arc.getAttribute('stroke-dashoffset')));
            if (now - start < 240) requestAnimationFrame(sample);
            else resolve([...offsets]);
          }
          requestAnimationFrame(sample);
        })""")
        assert len(values) >= 7 and all(a > b for a, b in zip(values, values[1:])), values
        in_match(code, lambda gs: gs['players'][host_id].update(x=700))
        rescue_arc.wait_for(state='detached')
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('等待队友靠近', exact=False).wait_for()
        in_match(code, lambda gs: gs['players'][host_id].update(x=729))
        ok('救援进度在快照之间连续推进，离开救援圈清空进度')
        guest.get_by_test_id('abyss-hp').get_by_text('35 / 100', exact=True).wait_for()
        for page in (host, guest):
            page.get_by_test_id('abyss-rescue-pulse').wait_for()
        ok('队友在真实战斗步进中完成救援，双方都能看到救援成功的扩散光环')
        protection = guest.get_by_test_id(f'abyss-protection-{mate_id}')
        protection.wait_for()

        def remaining_protection(gs):
            mate = next(player for player in gs['players'].values() if player['id'] != host_id)
            return mate['invincible'] - gs['elapsed']

        deadline = time.monotonic() + 2
        while in_match(code, remaining_protection) > 16:
            assert time.monotonic() < deadline
            guest.wait_for_timeout(10)
        guest.evaluate('window.setAbyssTestMute(true)')
        try:
            protection.wait_for(state='hidden', timeout=1100)
        finally:
            guest.evaluate('window.setAbyssTestMute(false)')
        ok('持续丢包超过移动预测时限后，获救保护圈仍按剩余时间撤下')


        def set_upgrade(gs):
            gs.update(level=19, spawn_budget=-1000)
            gs['xp'] = abyss.threshold(gs)
            gs['enemies'].clear()
            gs['fields'].clear()
            lead = next(iter(gs['players'].values()))
            lead['orbits'].clear()
            lead.update(weapons={'rivet': 7}, passives={'empty_tome': 5})
            abyss._refresh_stats(lead)

        in_match(code, set_upgrade)
        host.get_by_text('选择强化 · 10s', exact=True).wait_for(timeout=3000)
        card = host.get_by_test_id('abyss-choice-rivet')
        card.get_by_text('可用宝箱进化为圣铆炮', exact=True).wait_for()
        choice_frames = host.evaluate("""() => new Promise(resolve => {
          const frames = [];
          let last = 0;
          window.abyssTrackChoiceFrames = game => {
            if (game.choice_seconds && last !== game.frame) {
              frames.push(game.frame);
              last = game.frame;
            }
          };
          setTimeout(() => {
            window.abyssTrackChoiceFrames = undefined;
            resolve(frames);
          }, 650);
        })""")
        assert 2 <= len(choice_frames) <= 3, choice_frames
        assert all(b - a == 5 for a, b in zip(choice_frames, choice_frames[1:])), choice_frames
        ok('选卡倒计时从 10 秒开始，暂停期间每 250 毫秒同步一次')
        host.evaluate('window.setAbyssTestMute(true); window.abyssDropNextPick = true')
        card.click()
        host.get_by_test_id('abyss-pick-pending').wait_for()
        card.dispatch_event('click')
        assert card.get_attribute('aria-disabled') == 'true'
        assert host.evaluate('window.abyssPickSends') == 1
        host.evaluate('window.setAbyssTestMute(false)')
        host.get_by_test_id('abyss-pick-pending').wait_for(state='hidden')
        assert card.count() == 1
        host.get_by_text('按数字键选择强化', exact=True).wait_for()
        upgrade_key = in_match(code, lambda gs: gs['players'][host_id]['choices'].index('rivet') + 1)
        card.get_by_text(str(upgrade_key), exact=True).wait_for()
        host.keyboard.press(str(upgrade_key))
        card.wait_for(state='hidden')
        assert host.evaluate('window.abyssPickSends') == 2
        ok('升级选择等待确认期间防连点，首次未送达可用卡片上的数字键重试')
        waiting_choice = host.get_by_test_id('abyss-choice-waiting')
        waiting_choice.get_by_text(f'待选择：{UB}', exact=True).wait_for()
        host.get_by_text('等待 1 名队友 ·', exact=False).wait_for()
        host.get_by_text('你已完成选择。全员选完或倒计时结束后继续战斗。', exact=True).wait_for()
        assert guest.get_by_test_id('abyss-choice-waiting').count() == 0
        def offer_new_weapon(gs):
            mate = next(player for player in gs['players'].values() if player['id'] != host_id)
            mate.update(hp=94, choices=['harpoon', 'repair'])

        in_match(code, offer_new_weapon)
        new_weapon = guest.get_by_test_id('abyss-choice-harpoon')
        new_weapon.get_by_text('伤害 6.5 · 冷却 1秒', exact=True).wait_for()
        new_weapon.get_by_text('朝移动方向连发，穿透多个敌人', exact=True).wait_for()
        guest.get_by_test_id('abyss-choice-repair').get_by_text('耐久 94 → 100', exact=True).wait_for()
        new_weapon.click()
        waiting_choice.wait_for(state='detached')
        ok('提交后显示仍在选卡的队友，队友选完自动回到战斗')
        host.get_by_test_id('abyss-level').get_by_text('×2', exact=False).wait_for()
        card.wait_for(state='hidden')
        ok('20 级升级显示成长加成、进化条件、新武器一级数值和实际修补量')
        evolution_ready = host.get_by_test_id('abyss-evolution-ready')
        evolution_ready.wait_for()
        assert host.get_by_test_id('abyss-inventory').get_attribute('aria-label') == '装备，可用宝箱进化为圣铆炮'


        host.get_by_test_id('abyss-audio').click()
        host.get_by_test_id('abyss-sfx').click()
        host.get_by_test_id('abyss-audio-close').click()

        def start_boss(gs):
            gs['spawned'].update(('elite0', 'elite1'))
            gs['elapsed'] = round(gs['duration'] * 11 / 12 / abyss.STEP) - 2
            lead = gs['players'][host_id]
            lead['x'] = round(lead['x']) + .375
            abyss._effect(gs, 'charge', life=80, x=lead['x'], y=lead['y'], r=115)
            return gs['effects'][-1]['id']

        clash_effect = in_match(code, start_boss)
        host.get_by_test_id('abyss-warden-warning').get_by_text('断缆落点', exact=False).wait_for()
        host.wait_for_function('abyssMusicElement.volume === .28')
        max_hp = {12: 2160, 20: 2916, 30: 4320}[minutes]
        host.get_by_test_id('abyss-warden-health').get_by_text(
            re.compile(rf'^断缆守卫(?: · 狂暴)? · [0-9.]+ / {max_hp}$')).wait_for()
        arena = host.get_by_test_id('abyss-arena')
        arena.locator('img[src*="recovery-floor.webp"]').wait_for(state='attached')
        arena.locator('image[href*="cable-warden.webp"]').wait_for()
        dimensions = arena.evaluate('''async arena => {
          const floor = arena.querySelector('img[src*="recovery-floor.webp"]');
          const sprite = new Image();
          sprite.src = arena.querySelector('image[href*="cable-warden.webp"]').getAttribute('href');
          await Promise.all([floor.decode(), sprite.decode()]);
          return [[floor.naturalWidth, floor.naturalHeight], [sprite.naturalWidth, sprite.naturalHeight]];
        }''')
        assert dimensions == [[1440, 1000], [192, 192]], dimensions

        host.get_by_test_id(f'abyss-effect-{clash_effect}').wait_for()
        assert arena.evaluate('''(arena, ids) => {
          const position = id => arena.querySelector('[data-testid="' + id + '"]');
          const before = (a, b) => Boolean(position(a).compareDocumentPosition(position(b))
            & Node.DOCUMENT_POSITION_FOLLOWING);
          return before('abyss-warden-ground', ids.diver)
            && before(ids.effect, 'abyss-warden-outline');
        }''', {'diver': f'abyss-diver-{host_id}', 'effect': f'abyss-effect-{clash_effect}'})
        ground = host.get_by_test_id('abyss-warden-ground')
        assert ground.evaluate('''node => [...node.children].some(
            zone => Math.abs(zone.transform.baseVal.getItem(0).matrix.e % 1 - .375) < 1e-4)''')
        def add_remote_zone(gs):
            warden = next(enemy for enemy in gs['enemies'] if enemy['kind'] == 'warden')
            warden['attack']['zones'].append([30, 30, 38])
            return gs['frame']

        zone_frame = in_match(code, add_remote_zone)
        deadline = time.monotonic() + .5
        while in_match(code, lambda gs: gs['frame']) < zone_frame + 2:
            assert time.monotonic() < deadline
            host.wait_for_timeout(5)
        assert not ground.evaluate("""node => [...node.children].some(
          zone => zone.transform.baseVal.getItem(0).matrix.e === 30)""")
        ok('首领预警保留服务器的落点精度，底色与轮廓在爆炸重叠时分层绘制')

        charge = host.get_by_test_id('abyss-warden-charge').first
        samples = charge.evaluate('''target => new Promise(resolve => {
          const radii = new Set();
          const lateRadii = new Set();
          const start = performance.now();
          window.setAbyssTestMute(true);
          function frame(now) {
            const radius = target.getAttribute('r');
            radii.add(radius);
            if (now - start > 120) lateRadii.add(radius);
            if (now - start < 245) requestAnimationFrame(frame);
            else {
              window.setAbyssTestMute(false);
              resolve({ all: radii.size, late: lateRadii.size });
            }
          }
          requestAnimationFrame(frame);
        })''')
        assert samples['all'] >= 6 and samples['late'] >= 4, samples
        ok('短时快照中断后首领蓄力仍连续推进，命中阶段等待服务器确认')

        assert host.evaluate('abyssMusicElement.volume') == .28
        host.evaluate("window.setAbyssTestVisibility('hidden')")
        host.wait_for_function("abyssMusicElement.paused && abyssSoundContext.state === 'suspended'")
        host.evaluate("window.setAbyssTestVisibility('visible')")
        host.wait_for_function("""!abyssMusicElement.paused && abyssMusicElement.volume === .55
            && abyssSoundContext.state === 'running'""")
        ok('首领预警期间切后台会暂停配乐与音效处理，返回时恢复正常音量')
        host.emulate_media(reduced_motion='reduce')
        impact = host.get_by_test_id('abyss-warden-impact').first
        impact.wait_for()
        assert host.get_by_test_id('abyss-warden-charge').count() == 0
        radii = impact.evaluate("""ring => new Promise(resolve => {
          const sizes = new Set();
          const start = performance.now();
          function sample(now) {
            sizes.add(ring.getAttribute('r'));
            if (now - start < 100) requestAnimationFrame(sample);
            else resolve([...sizes]);
          }
          requestAnimationFrame(sample);
        })""")
        assert len(radii) == 1, radii
        host.emulate_media(reduced_motion='no-preference')
        ok('首领命中经服务器确认后显示冲击圈，减少动态效果时固定半径')
        def schedule_next_attack(gs):
            warden = next(enemy for enemy in gs['enemies'] if enemy['kind'] == 'warden')
            warden['hp'] = warden['max_hp'] * .4
            warden['next_attack'] = gs['elapsed'] + 1

        in_match(code, schedule_next_attack)
        host.get_by_test_id('abyss-warden-health').get_by_text('狂暴', exact=False).wait_for()
        host.get_by_test_id('abyss-warden-warning').get_by_text('压强脉冲', exact=False).wait_for()
        host.wait_for_function('abyssMusicElement.volume === .28')
        host.get_by_test_id('abyss-audio').click()
        volumes = host.evaluate("""() => new Promise(resolve => {
          const levels = new Set();
          const started = performance.now();
          let switched = null;
          function sample(now) {
            const volume = abyssMusicElement.volume;
            if (!switched && volume > .34 && volume < .49) {
              document.querySelector('[data-testid="abyss-music-level-loud"]').click();
              switched = {before: volume / .55, after: abyssMusicElement.volume / .8};
            }
            const normalized = abyssMusicElement.volume / (switched ? .8 : .55);
            if (normalized > .28 / .55 && normalized < 1) levels.add(normalized);
            if (normalized >= 1 || now - started > 1200) resolve({levels: [...levels], switched});
            else requestAnimationFrame(sample);
          }
          requestAnimationFrame(sample);
        })""")
        assert volumes['switched'] is not None, volumes
        assert abs(volumes['switched']['before'] - volumes['switched']['after']) < .01, volumes
        assert len(volumes['levels']) >= 3 and volumes['levels'] == sorted(volumes['levels']), volumes
        assert host.evaluate('abyssMusicElement.volume') == .8
        ok('守卫狂暴后配乐先压低再恢复，淡入中切换音量不回落')
        host.get_by_test_id('abyss-sfx').click()
        host.get_by_test_id('abyss-audio-close').click()

        def collect_warden_chest(gs):
            warden = next(enemy for enemy in gs['enemies'] if enemy['kind'] == 'warden')
            lead = next(iter(gs['players'].values()))
            warden.update(x=lead['x'], y=lead['y'])
            lead['hp'] = 60
            abyss._damage(gs, lead, warden, warden['hp'])
            abyss._spawn_chest(gs, lead['x'], lead['y'])

        in_match(code, collect_warden_chest)
        host.get_by_test_id('abyss-chest-burst').nth(1).wait_for()
        host.get_by_test_id('abyss-chest').get_by_text('圣铆炮', exact=False).wait_for()
        evolution_ready.wait_for(state='detached')
        host.get_by_test_id('abyss-chest').get_by_text('修复 30 点耐久', exact=False).wait_for()
        guest_rewards = guest.get_by_test_id('abyss-chest').get_by_text(UB, exact=False)
        guest_rewards.nth(1).wait_for()
        assert guest_rewards.count() == 2
        ok('装备进化后就绪标记消失，双方可看到同帧开启的两份宝箱奖励')
        host.get_by_test_id('abyss-inventory').click()
        loadout = host.get_by_test_id('abyss-inventory-modal')
        loadout.get_by_text('铆钉发射器进化：高频连射', exact=True).wait_for()
        loadout.get_by_text('已进化', exact=True).wait_for()
        loadout.get_by_text('武器冷却 -8%/级', exact=True).wait_for()
        host.get_by_test_id('abyss-inventory-close').click()
        ok('装备面板显示进化武器效果和被动升级收益')

        host.get_by_test_id('abyss-audio').click()
        host.get_by_test_id('abyss-sfx').click()
        host.get_by_test_id('abyss-audio-close').click()
        cue_count = host.evaluate('window.abyssSfxStarts')
        in_match(code, lambda gs: gs.update(
            frame=gs['frame'] + 24, elapsed=round(gs['duration'] / abyss.STEP) - 1))
        host.wait_for_function('count => window.abyssSfxStarts === count + 1', arg=cue_count)
        ok('结算快照跨过较多帧时仍播放一次胜利音效')
        host.get_by_test_id('abyss-audio').click()
        host.get_by_test_id('abyss-sfx').click()
        host.get_by_test_id('abyss-audio-close').click()
        summary = host.get_by_test_id('abyss-result-summary')
        summary.get_by_text('断缆守卫 · 已击败', exact=True).wait_for()
        summary.get_by_text('圣铆炮 已进化', exact=True).wait_for()
        summary.get_by_text('救起 1 次', exact=False).wait_for()
        host.wait_for_function('window.abyssMusicElement?.paused === true')
        host.evaluate('window.abyssPreviousMusicElement = window.abyssMusicElement')
        host.get_by_role('button', name='返回房间').click()
        host.get_by_text('房间号', exact=True).wait_for()
        ok('守卫预警、宝箱进化、结算和返回房间均走真实消息')

        headers = {'Authorization': f'Bearer {token}'}
        history_url = f'{origin}/api/history/mine?' + urlencode({'page': 1, 'size': 20})
        history = host.request.get(history_url, headers=headers).json()['items'][0]
        assert history['snapshot']['map_size'] == 'medium'
        assert history['snapshot']['boss_dead'] and history['snapshot']['salvage'] == 50
        lead_record = history['snapshot']['players'][0]
        assert lead_record['weapons'] == {'holy_wand': 1}
        assert lead_record['rescues'] == 1 and lead_record['xp_collected'] >= 3
        response = host.request.post(f"{origin}/api/history/{history['id']}/share", headers=headers)
        assert response.ok
        viewer = browser.new_page()
        use_backend(viewer, origin)
        viewer.goto(BASE + response.json()['path'])
        public_summary = viewer.get_by_test_id('abyss-result-summary')
        public_summary.get_by_text('地图 · 中', exact=True).wait_for()
        public_summary.get_by_text(f'{minutes}:00', exact=True).wait_for()
        public_summary.get_by_text('救起 1 次', exact=False).wait_for()
        ok('战报落盘并通过匿名只读页面显示')
        guest.get_by_role('button', name='返回房间').click()
        def join_rematch(name):
            teammate = browser.new_page(viewport={'width': 360, 'height': 720})
            use_backend(teammate, origin)
            register(teammate, name)
            goto_tab(teammate, '房间')
            teammate.get_by_text('加入', exact=True).first.click()
            teammate.get_by_placeholder('输入6位房间号').fill(code)
            teammate.get_by_text('加入', exact=True).last.click()
            teammate.get_by_test_id('guide-skip').click()
            teammate.get_by_text('准 备', exact=True).click()
            return teammate

        third_name = UB + '_c'
        third = join_rematch(third_name)
        fourth_name = UB + '_long'
        fourth = join_rematch(fourth_name)
        fourth.set_viewport_size({'width': 320, 'height': 680})
        guest.get_by_text('准 备', exact=True).click()
        host.get_by_text('开始游戏', exact=True).click()
        host.get_by_test_id('abyss-arena').wait_for()
        host.evaluate('window.abyssDenyNextMusic = true')
        deadline = time.monotonic() + 5
        while not host.evaluate('window.abyssDeniedMusic?.paused === true'):
            assert time.monotonic() < deadline, 'Restored soundtrack did not attempt playback'
            host.keyboard.press('e')
            host.wait_for_timeout(50)
        host.keyboard.press('e')
        host.wait_for_function('abyssMusicElement === abyssDeniedMusic && !abyssMusicElement.paused && abyssMusicElement.volume === .8')
        ok('下一局保留从游戏内切换的高音量，浏览器拒绝首播后可按键恢复')
        host.get_by_test_id('abyss-audio').click()
        assert host.get_by_test_id('abyss-music').get_attribute('aria-checked') == 'true'
        assert host.get_by_test_id('abyss-sfx').get_attribute('aria-checked') == 'false'
        host.wait_for_function('''window.abyssMusicElement !== window.abyssPreviousMusicElement
            && !window.abyssMusicElement.paused''')
        assert host.evaluate('window.abyssDecodedClips') == 11
        host.get_by_test_id('abyss-sfx').click()
        host.wait_for_function('window.abyssDecodedClips === 22')
        ok('下一局恢复已保存的配乐开关与音效关闭状态，重新开启音效后才加载')
        fourth.get_by_test_id('abyss-arena').wait_for()
        team = fourth.get_by_test_id('abyss-team')
        assert team.evaluate('''team => {
          const cards = Array.from(team.children, node => node.getBoundingClientRect());
          return cards.length === 4 && cards.every(card => card.width >= 55
            && card.left >= 0 && card.right <= 320 && Math.abs(card.width - cards[0].width) < 1);
        }''')
        assert fourth.get_by_test_id('abyss-arena').bounding_box()['height'] > 400
        ok('320 像素宽的四人房保持小队面板等宽，战场保留可操作高度')

        def separate_divers(gs):
            lead, mate, backup, other = gs['players'].values()
            gs['spawn_budget'] = -1000
            gs['enemies'].clear()
            lead.update(x=1080, y=500)
            mate.update(x=400, y=500)
            backup.update(x=700, y=500)
            other.update(hp=0, out=True)

        in_match(code, separate_divers)
        guest.wait_for_function('''() => {
          const world = document.querySelector('[data-testid="abyss-world"]');
          return world && Math.abs(world.transform.baseVal.getItem(0).matrix.e + 140) < 1;
        }''')
        guest.get_by_test_id('abyss-world').evaluate('''world => {
          window.abyssSpectatorTravel = new Promise(resolve => {
            const start = performance.now();
            const positions = new Set();
            function frame(now) {
              positions.add(world.transform.baseVal.getItem(0).matrix.e);
              if (now - start < 600) requestAnimationFrame(frame);
              else resolve([...positions]);
            }
            requestAnimationFrame(frame);
          });
        }''')
        def expire_diver(gs):
            mate = next(player for player in gs['players'].values() if player['id'] != host_id)
            mate.update(hp=0, down=gs['elapsed'] + 1)

        in_match(code, expire_diver)
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + UA).wait_for()
        travel = guest.evaluate('window.abyssSpectatorTravel')
        assert len(travel) > 8 and travel[0] - travel[-1] > 500, travel
        ok('队友出局后镜头分帧移向存活队友，观战提示显示其名字')
        spectator_next = guest.get_by_test_id('abyss-spectator-next')
        spectator_next.click()
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + third_name).wait_for()
        guest.wait_for_function('''() => {
          const world = document.querySelector('[data-testid="abyss-world"]');
          return Math.abs(world.transform.baseVal.getItem(0).matrix.e + 440) < 2;
        }''')
        guest.get_by_text('← / → 切换队友', exact=True).wait_for()
        guest.keyboard.press('ArrowLeft')
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + UA).wait_for()
        ok('出局后可用按钮或方向键在存活队友间切换镜头')
        host.evaluate('window.disconnectAbyssTestRoom()')
        host_status = guest.get_by_test_id(f'abyss-team-status-{host_id}')
        host_status.get_by_text('离线', exact=True).wait_for()
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + third_name).wait_for()
        host_status.get_by_text('离线', exact=True).wait_for(state='hidden')
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + third_name).wait_for()
        guest.keyboard.press('ArrowLeft')
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + UA).wait_for()
        ok('观战队友掉线时自动跟随在线队友，重连后保留当前镜头')
        in_match(code, lambda gs: abyss._hurt_diver(gs, gs['players'][host_id], 200))
        guest.get_by_test_id('abyss-rescue-notice').get_by_text('观战中 · ' + third_name).wait_for()
        guest.wait_for_function('''() => {
          const world = document.querySelector('[data-testid="abyss-world"]');
          return Math.abs(world.transform.baseVal.getItem(0).matrix.e + 440) < 2;
        }''')
        spectator_next.wait_for(state='hidden')
        ok('多人房中被观战的队友倒地后，镜头与名字一起切换到另一位存活队友')
        host.get_by_test_id('abyss-audio-close').click()
        in_match(code, lambda gs: gs.update(xp=abyss.threshold(gs)))
        guest.get_by_test_id('abyss-choice-waiting').wait_for()
        guest.get_by_test_id('abyss-world').evaluate("""world => {
          window.abyssSpectatorResume = new Promise(resolve => {
            const start = performance.now();
            let furthest = 0;
            function sample(now) {
              furthest = Math.max(furthest, Math.abs(world.transform.baseVal.getItem(0).matrix.e + 440));
              if (now - start < 600) requestAnimationFrame(sample);
              else resolve(furthest);
            }
            requestAnimationFrame(sample);
          });
        }""")
        third.locator('[data-testid^="abyss-choice-"]').first.click()
        guest.get_by_test_id('abyss-choice-waiting').wait_for(state='hidden')
        assert guest.evaluate('window.abyssSpectatorResume') < 3
        ok('选卡结束后观战镜头保持在被观战队友身上')
        third.close()
        host.get_by_text('离线', exact=True).wait_for()

        def set_up_offline_rescue(gs):
            gs['players'][host_id].update(x=710, rescue=abyss.RESCUE_TICKS - 1)
            return gs['frame']

        frame = in_match(code, set_up_offline_rescue)
        deadline = time.monotonic() + 2
        while in_match(code, lambda gs: gs['frame']) < frame + 2:
            assert time.monotonic() < deadline, 'Battle stopped after one teammate disconnected'
            host.wait_for_timeout(20)
        assert in_match(code, lambda gs: gs['players'][host_id]['hp']) == 0
        host.get_by_test_id('abyss-rescue-notice').get_by_text('等待队友靠近', exact=False).wait_for()
        ok('离线角色即使站在救援圈里，也不会自动救起队友')
        in_match(code, lambda gs: abyss._spawn_chest(gs, 700, 500))
        host.get_by_test_id('abyss-chest').wait_for(state='hidden')
        host.wait_for_timeout(230)
        assert in_match(code, lambda gs: len(gs['chests'])) == 1
        in_match(code, lambda gs: gs['players'][host_id].update(x=730, hp=35, down=0))
        host.get_by_test_id('abyss-chest').wait_for()
        assert in_match(code, lambda gs: not gs['chests'] and gs['salvage'] == 25)
        ok('离线玩家脚边的宝箱保留，在线队友靠近可打开')
        def down_offline_teammate(gs):
            mate = next(player for player in gs['players'].values() if player['name'] == third_name)
            abyss._hurt_diver(gs, mate, 200)
            return mate['id']

        offline_id = in_match(code, down_offline_teammate)
        status = fourth.get_by_test_id(f'abyss-team-status-{offline_id}')
        status.get_by_text('离线待救', exact=True).wait_for()
        assert re.match(r'离线 · 待救 [0-9]+s', status.get_attribute('aria-label'))
        ok('窄屏状态能显示离线待救，辅助技术仍可读到倒计时')
        in_match(code, lambda gs: gs['players'][host_id].update(x=42, y=500))
        wall = host.get_by_test_id('abyss-wall')
        assert float(wall.get_attribute('x')) == abyss.WALL_INSET
        diver = host.get_by_test_id(f'abyss-diver-{host_id}')
        host.keyboard.down('a')
        try:
            host.wait_for_function('''([id, edge]) => {
              const diver = document.querySelector('[data-testid="abyss-diver-' + id + '"]');
              return diver.transform.baseVal.getItem(0).matrix.e === edge;
            }''', arg=[host_id, abyss.WALL_INSET])
            host.keyboard.down('w')
            deadline = time.monotonic() + 1
            while in_match(code, lambda gs: gs['players'][host_id]['y']) >= 470:
                assert time.monotonic() < deadline, 'Diagonal input did not slide along the wall'
                host.wait_for_timeout(20)
            assert in_match(code, lambda gs: gs['players'][host_id]['x']) == abyss.WALL_INSET
            assert diver.evaluate('node => node.transform.baseVal.getItem(0).matrix.e') == abyss.WALL_INSET
        finally:
            host.keyboard.up('w')
            host.keyboard.up('a')
        ok('战场警戒线与移动边界一致，斜向输入可沿边滑动')
        for width, height in ((1800, 480), (320, 1400)):
            host.set_viewport_size({'width': width, 'height': height})
            host.wait_for_function('''([pageWidth, worldWidth, worldHeight]) => {
              const arena = document.querySelector('[data-testid="abyss-arena"]');
              const world = arena?.querySelector('[data-testid="abyss-world"]');
              if (!world) return false;
              const svg = world.ownerSVGElement;
              const box = svg.viewBox.baseVal;
              const camera = world.transform.baseVal.getItem(0).matrix;
              return Math.abs(svg.getBoundingClientRect().width - pageWidth) < 2
                && box.width <= worldWidth + 1e-4 && box.height <= worldHeight + 1e-4
                && camera.e <= 1e-4 && camera.f <= 1e-4
                && box.width - camera.e <= worldWidth + 1e-4
                && box.height - camera.f <= worldHeight + 1e-4;
            }''', arg=[width, abyss.WIDTH, abyss.HEIGHT])
        ok('超宽和超高窗口的镜头视口均落在战场范围内')
        host.set_viewport_size({'width': 360, 'height': 720})

        def seed_objectives(gs):
            near = gs['players'][offline_id]
            far = next(player for player in gs['players'].values() if player['name'] == fourth_name)
            near.update(x=210, y=500, down=gs['elapsed'] + 160)
            far.update(x=1100, y=500, hp=0, out=False, down=gs['elapsed'] + 280)
            abyss._spawn_chest(gs, 230, 500)
            abyss._spawn_chest(gs, 1070, 500)
            return far['id']

        distant_diver = in_match(code, seed_objectives)
        objective = host.get_by_test_id('abyss-objective')
        objective.get_by_text(re.compile(r'待救 (13|14)s')).wait_for()
        in_match(code, lambda gs: gs['players'][distant_diver].update(x=240))
        objective.get_by_text('宝箱', exact=True).wait_for()
        in_match(code, lambda gs: gs['chests'][-1].update(x=245))
        objective.wait_for(state='hidden')
        ok('屏内目标不会盖过屏外指引，屏外救援仍优先于宝箱')
        def stack_top_banners(gs):
            lead = gs['players'][host_id]
            lead.update(x=720, y=900)
            for player in gs['players'].values():
                if player['id'] != host_id:
                    player.update(hp=0, out=True)
            gs['chests'].clear()
            abyss._spawn_chest(gs, 720, 40)
            abyss._spawn_chest(gs, 720, 900)
            abyss._spawn(gs, 'warden', lead)
            gs['enemies'][-1].update(x=1060, y=700, speed=0, next_attack=gs['elapsed'] + 1)
            gs['boss'] = True

        host.get_by_test_id('abyss-inventory').click()
        inventory = host.get_by_test_id('abyss-inventory-modal')
        inventory.wait_for()
        in_match(code, stack_top_banners)
        host.wait_for_function('''() => {
          const messages = document.querySelector('[data-testid="abyss-arena-notices"]');
          const marker = document.querySelector('[data-testid="abyss-objective"]');
          return messages?.children.length === 2 && marker?.textContent === '宝箱'
            && marker.getBoundingClientRect().top >= messages.getBoundingClientRect().bottom + 4;
        }''')
        inventory.wait_for(state='hidden')
        ok('守卫预警覆盖本地潜水员时，装备面板让出战场')
        ok('宝箱提示和首领预警同时显示时，屏外指引避开顶部提示栏')
        def move_marker_near_wall(gs):
            gs['players'][host_id].update(x=abyss.WALL_INSET, y=900)
            gs['chests'][0].update(x=90)

        in_match(code, move_marker_near_wall)
        host.wait_for_function('''([id, edge]) => {
          const diver = document.querySelector('[data-testid="abyss-diver-' + id + '"]');
          const arrow = document.querySelector('[data-testid="abyss-objective-arrow"]');
          if (!diver || !arrow) return false;
          const position = diver.transform.baseVal.getItem(0).matrix;
          if (position.e !== edge || Math.abs(position.f - 900) > 1) return false;
          const expected = Math.atan2(40 - position.f, 90 - position.e) * 180 / Math.PI;
          return Math.abs(arrow.transform.baseVal.getItem(1).angle - expected) < 1;
        }''', arg=[host_id, abyss.WALL_INSET])
        ok('镜头贴边时，箭头按潜水员的位置指向目标')
        in_match(code, lambda gs: gs['chests'].clear())
        objective.get_by_text('守卫', exact=True).wait_for()

        def bring_warden_into_view(gs):
            warden = next(enemy for enemy in gs['enemies'] if enemy['kind'] == 'warden')
            warden.update(x=135, y=900)

        in_match(code, bring_warden_into_view)
        objective.wait_for(state='hidden')
        ok('屏外守卫可追踪，宝箱与等待救援的队友优先')
        def place_chest_below_view(gs):
            gs['players'][host_id].update(x=720, y=60)
            abyss._spawn_chest(gs, 720, 960)

        in_match(code, place_chest_below_view)
        objective.get_by_text('宝箱', exact=True).wait_for()
        host.evaluate('window.setAbyssTestMute(true)')
        try:
            host.wait_for_function("""() => {
              const notice = document.querySelector('[data-testid="abyss-arena-status"]');
              const arrow = document.querySelector('[data-testid="abyss-objective"]');
              return notice?.textContent.includes('网络延迟') && arrow
                && arrow.getBoundingClientRect().bottom <= notice.getBoundingClientRect().top - 4;
            }""", timeout=1100)
        finally:
            host.evaluate('window.setAbyssTestMute(false)')
        host.get_by_test_id('abyss-network-status').wait_for(state='hidden')
        ok('弱网提示出现时，屏边箭头仍保持在提示框外')
        host.set_viewport_size({'width': 320, 'height': 480})

        def finish_full_loadout(gs):
            diver = gs['players'][host_id]
            diver['weapons'] = dict.fromkeys(('rivet', 'arc', 'ring', 'harpoon', 'axe', 'cross'), 2)
            diver['passives'] = dict.fromkeys(list(abyss.data.PASSIVES)[:6], 1)
            gs['enemies'].clear()
            gs.update(boss=True, boss_dead=True, elapsed=round(gs['duration'] / abyss.STEP) - 1)

        in_match(code, finish_full_loadout)
        report = host.get_by_test_id('abyss-result-summary')
        report.wait_for()
        back = host.get_by_role('button', name='返回房间')
        back.wait_for()
        before = back.bounding_box()
        scroll = host.get_by_test_id('abyss-overlay-scroll')
        assert scroll.evaluate('node => node.scrollHeight > node.clientHeight')
        scroll.evaluate('node => { node.scrollTop = node.scrollHeight; }')
        host.wait_for_function('''() => {
          const scroll = document.querySelector('[data-testid="abyss-overlay-scroll"]');
          const report = document.querySelector('[data-testid="abyss-result-summary"]');
          return scroll.scrollTop > 0
            && report.getBoundingClientRect().bottom <= scroll.getBoundingClientRect().bottom + 1;
        }''')
        assert back.bounding_box() == before and before['y'] + before['height'] <= 480
        back.click()
        host.get_by_text('房间号', exact=True).wait_for()
        ok('窄屏四人战报可滚到装备列表末尾，返回按钮保持可见')
        solo = browser.new_page(viewport={'width': 360, 'height': 720})
        use_backend(solo, origin)
        register(solo, UA + '_solo')
        goto_tab(solo, '游戏')
        solo.get_by_text('深渊回声', exact=True).first.click()
        with solo.expect_response(lambda response: response.url == origin + '/api/rooms'
                                  and response.request.method == 'POST') as created_solo:
            solo.get_by_text('创建房间', exact=True).click()
        solo_code = created_solo.value.json()['code']
        solo.get_by_test_id('guide-skip').click()
        solo.get_by_test_id('room-settings-row').click()
        for size in ('small', 'medium', 'large', 'endless'):
            solo.get_by_test_id(f'room-settings-map_size-{size}').wait_for()
        solo.get_by_text('守卫在战斗进行到 11:00 时出现。短局升级门槛降为 75%，更容易成型；升级选卡暂停战斗计时。', exact=True).wait_for()
        solo.get_by_test_id('room-settings-map_size-endless').click()
        solo.get_by_text('地图没有边界，墙壁反弹不会触发，对局时长仍按房规。', exact=True).wait_for()
        solo.get_by_test_id('room-settings-close').click()
        solo.get_by_test_id('room-settings-row').get_by_text('12 分钟 · 标准 · 无尽', exact=True).wait_for()
        solo.get_by_text('开始游戏', exact=True).click()
        solo.get_by_test_id('abyss-arena').wait_for()
        assert in_match(solo_code, lambda gs: gs['map_size']) == 'endless'
        assert solo.get_by_test_id('abyss-wall').count() == 0
        solo.set_viewport_size({'width': 320, 'height': 680})
        solo.wait_for_function("""() => {
          const footer = document.querySelector('[data-testid="abyss-footer"]');
          const bounds = footer.getBoundingClientRect();
          const singleLine = id => {
            const range = document.createRange();
            range.selectNodeContents(footer.querySelector('[data-testid="abyss-footer-' + id + '"]'));
            return new Set([...range.getClientRects()].map(rect => Math.round(rect.top))).size === 1;
          };
          return bounds.width <= 320 && singleLine('status') && singleLine('hint')
            && ['audio', 'inventory', 'leave'].every(id => {
              const button = footer.querySelector('[data-testid="abyss-' + id + '"]').getBoundingClientRect();
              return button.height >= 44 && button.left >= bounds.left && button.right <= bounds.right;
            });
        }""")
        solo.set_viewport_size({'width': 360, 'height': 720})
        ok('320 px 战斗底栏状态和提示各占一行，三个按钮完整可点')

        def move_beyond_old_boundary(gs):
            diver = next(iter(gs['players'].values()))
            diver.update(x=-5, y=500)
            return diver['id']

        solo_id = in_match(solo_code, move_beyond_old_boundary)
        solo.keyboard.down('a')
        try:
            solo.wait_for_function("""id => {
              const player = document.querySelector('[data-testid="abyss-diver-' + id + '"]');
              const world = document.querySelector('[data-testid="abyss-world"]');
              const floor = document.querySelector('[data-testid="abyss-floor-tiles"]');
              if (!world || !floor) return false;
              const viewport = world.closest('svg').getBoundingClientRect();
              const tiles = floor.getBoundingClientRect();
              return player?.transform.baseVal.getItem(0).matrix.e < -25
                && world.transform.baseVal.getItem(0).matrix.e > 0
                && tiles.left <= viewport.left + 1 && tiles.top <= viewport.top + 1
                && tiles.right >= viewport.right - 1 && tiles.bottom >= viewport.bottom - 1
                && [...floor.children].some(tile => {
                  const edge = tile.getBoundingClientRect().left - viewport.left;
                  return edge > 0 && edge < viewport.width;
                });
            }""", arg=solo_id)
            floor_updates = solo.get_by_test_id('abyss-floor-tiles').evaluate("""floor => new Promise(resolve => {
              let moved = 0;
              let tileUpdates = 0;
              const observer = new MutationObserver(records => {
                for (const record of records) {
                  if (record.target === floor && record.type === 'attributes') moved++;
                  else tileUpdates++;
                }
              });
              observer.observe(floor, {attributes: true, childList: true, subtree: true});
              setTimeout(() => {
                observer.disconnect();
                resolve({moved, tileUpdates});
              }, 220);
            })""")
            assert floor_updates['moved'] >= 2 and floor_updates['tileUpdates'] == 0, floor_updates
        finally:
            solo.keyboard.up('a')
        ok('四种地图可选，无尽地图越界时地砖铺满，平移只更新图层')

        def spawn_distant_warden(gs):
            diver = gs['players'][solo_id]
            abyss.stop(gs, solo_id)
            diver.update(x=-50, y=500)
            abyss._spawn(gs, 'warden', diver)
            gs['enemies'][-1].update(x=2450, y=500, speed=0)
            gs['boss'] = True

        in_match(solo_code, spawn_distant_warden)
        distance = solo.get_by_test_id('abyss-warden-distance')
        distance.get_by_text('距离 2500 px', exact=True).wait_for()
        solo.get_by_test_id('abyss-objective').get_by_text('守卫', exact=True).wait_for()
        in_match(solo_code, lambda gs: gs['players'][solo_id].update(x=450))
        distance.get_by_text('距离 2000 px', exact=True).wait_for()
        ok('无尽地图守卫距离随玩家移动而更新')

        def finish_solo(gs):
            diver = next(iter(gs['players'].values()))
            gs['enemies'].clear()
            gs['loot'].clear()
            # Natural waves may have chipped an enemy before this checkpoint;
            # reset so the assertion only sees the 9 damage applied below.
            diver['damage_dealt'] = 0
            abyss._spawn(gs, 'drifter', diver)
            enemy = gs['enemies'][-1]
            enemy['hp'] = 9
            abyss._damage(gs, diver, enemy, 80, drop_chance=0)
            abyss._drop_loot(gs, diver, 7)
            gs['spawned'].update(('elite0', 'elite1', 'boss'))
            gs.update(boss=True, boss_dead=True, elapsed=round(gs['duration'] / abyss.STEP) - 1)

        in_match(solo_code, finish_solo)
        solo_report = solo.get_by_test_id('abyss-result-summary')
        solo_report.get_by_text('本局贡献', exact=True).wait_for()
        solo_report.get_by_text('地图 · 无尽', exact=True).wait_for()
        solo_report.get_by_text('拾取经验 7', exact=False).wait_for()
        solo_report.get_by_text('输出伤害 9', exact=False).wait_for()
        assert solo_report.get_by_text('全队击退', exact=True).count() == 0
        award = solo.get_by_test_id('abyss-talent-award')
        award.get_by_text('天赋点 +1', exact=True).wait_for()
        award.get_by_text('完成试潜 +1', exact=False).wait_for()
        ok('单人结算显示实际输出伤害和拾取经验')

        def back_to_room():
            solo.get_by_role('button', name='返回房间').click()
            solo.get_by_text('房间号', exact=True).wait_for()

        def start_solo():
            solo.get_by_text('开始游戏', exact=True).click()
            solo.get_by_test_id('abyss-arena').wait_for()

        def restart_solo():
            back_to_room()
            start_solo()

        # 结算后回房间：入口行应显示 1 点，抽屉里可加点、可免费重置。
        back_to_room()
        solo.get_by_test_id('talent-tree-row').click()
        sheet = solo.get_by_test_id('talent-sheet')
        sheet.wait_for()
        sheet.get_by_text('深潜体魄', exact=True).wait_for()
        sheet.get_by_text('过载火力', exact=True).wait_for()
        sheet.get_by_text('打捞直觉', exact=True).wait_for()
        vitality = solo.get_by_test_id('talent-node-vitality')
        assert '1' in solo.get_by_test_id('talent-points').inner_text()
        solo.get_by_test_id('talent-add-vitality').click()
        vitality.get_by_text('Lv.1/5', exact=True).wait_for()
        assert '0' in solo.get_by_test_id('talent-points').inner_text()
        solo.get_by_test_id('talent-reset').click()
        solo.get_by_test_id('talent-reset-confirm').wait_for()
        solo.get_by_test_id('talent-reset-confirm-btn').click()
        vitality.get_by_text('Lv.0/5', exact=True).wait_for()
        assert '1' in solo.get_by_test_id('talent-points').inner_text()
        solo.get_by_test_id('talent-close').click()
        sheet.wait_for(state='hidden')
        ok('房间天赋树：结算得点、可加点、免费重置并全额返还')
        start_solo()
        in_match(solo_code, lambda gs: abyss._hurt_diver(gs, next(iter(gs['players'].values())), 200))
        solo.get_by_text('全队失压', exact=True).wait_for()
        solo_report.get_by_text('潜水员失压，回收任务结束。', exact=True).wait_for()
        ok('引擎判定失压后显示失败原因，仍可返回房间继续开局')

        restart_solo()

        def run_out_of_time(gs):
            gs['spawned'].update(('elite0', 'elite1', 'boss'))
            gs.update(boss=True, boss_dead=False, elapsed=round(gs['duration'] / abyss.STEP) - 1)

        in_match(solo_code, run_out_of_time)
        solo_report.get_by_text('作战时间已到，断缆守卫仍未击败。', exact=True).wait_for()
        solo.get_by_role('button', name='返回房间').click()
        solo.get_by_text('房间号', exact=True).wait_for()
        ok('守卫存活时耗尽作战时间会显示超时原因，可正常返回房间')
        def legacy_history(route):
            response = route.fetch()
            payload = response.json()
            row = next(item for item in payload['items'] if item['id'] == history['id'])
            for player in row['snapshot']['players']:
                player.pop('xp_collected', None)
                player.pop('rescues', None)
                player.pop('damage_dealt', None)
                if player['id'] == host_id:
                    player['weapons']['old_weapon'] = 3
                    player['passives']['old_passive'] = 2
            route.fulfill(response=response, json=payload)

        host.route(origin + '/api/history/mine?*', legacy_history)
        host.goto(BASE)
        goto_tab(host, '我的')
        host.get_by_text('历史战绩', exact=True).last.click()
        host.get_by_test_id(f'history-record-{history["id"]}').click()
        legacy = host.get_by_test_id('abyss-result-summary')
        legacy.get_by_text('圣铆炮 已进化', exact=True).wait_for()
        legacy.get_by_text('旧版武器 Lv.3', exact=True).wait_for()
        legacy.get_by_text('旧版被动 Lv.2', exact=True).wait_for()
        assert legacy.get_by_text('拾取经验', exact=False).count() == 0
        ok('历史战报兼容缺少贡献字段和停用装备的旧记录')
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes', type=int, choices=(12, 20, 30), default=12)
    main(parser.parse_args().minutes)
