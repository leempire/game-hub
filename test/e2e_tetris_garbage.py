#!/usr/bin/env python3
"""Web 端 E2E：俄罗斯方块**被攻击行顶到之后不能卡住**（用户报的那个 bug）。

现象：「被对手顶之后就会卡住，不再出现新的方块」。两个根因（详见
`docs/游戏设计/俄罗斯方块/设计.md`）：

1. **引擎**：方块出生在第 0 行、本引擎没有可视区上方的缓冲区，于是
   `cur["y"] - count < 0` 的判死逻辑把「刚出生的方块」直接顶死；而且判死分支
   `continue` 掉了 `add_garbage`——受害者的盘面纹丝不动、方块凭空消失，
   之后重力与按键全部空转。
2. **界面**：出局提示挂在页面最底下，20 行的棋盘把整屏撑满，手机视口里整条
   被裁掉，玩家连「你已经出局了」都看不到。

本脚本用**真浏览器当受害者**，另开一个原生 WebSocket 的「攻击机器人」不断
消行送攻击行，然后断言受害者一侧的三个不变量：

- **被顶真的发生了**（HUD 的「被顶」计数 ≥ 1）——否则这个用例等于没测到；
- **没有静默卡死**：没出局就要还能继续下落（方块在动）、还能走子锁定；
- **出局必须看得见**：若已出局，出局浮层（`t-out-overlay`）必须真的在视口内。

用法：先启动后端(50541)和前端 web(50540)，然后 python test/e2e_tetris_garbage.py
"""
import json
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright
from websocket import create_connection

BASE = 'http://localhost:50540'
API = 'http://127.0.0.1:50541'
WS = 'ws://127.0.0.1:50541'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'tk_a_{TS}', f'tk_b_{TS}'
COLS, ROWS = 10, 20
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def register_page(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)


def read_hud(page, label):
    """读面板 HUD 上某个读数（`被顶` / `分数`…）——它是「标签 + 数值」两行文本。"""
    return page.evaluate('''(label) => {
        const els = [...document.querySelectorAll('div')];
        for (const el of els) {
            const t = (el.innerText || '').trim();
            const m = t.match(new RegExp('^' + label + '\\\\s*\\\\n?\\\\s*(\\\\d+)$'));
            if (m) return parseInt(m[1], 10);
        }
        return null;
    }''', label)


def falling_cells(page, rows=20):
    """盘面上所有有色格子的坐标（塌掉的行也算）。读全盘是为了看方块有没有在动。"""
    return page.evaluate('''(args) => {
        const [rows, cols] = args;
        const out = [];
        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                const el = document.querySelector(`[data-testid="t-c-${r}-${c}"]`);
                const label = el && el.getAttribute('aria-label');
                if (label && label !== 'empty' && label !== 'garbage') out.push([r, c]);
            }
        }
        return out;
    }''', [rows, COLS])


# ── 攻击机器人：靠服务端回包做完全信息贪心摆放 ──────────────────


def _valid_at(board, cells, x, y):
    for dy, dx in cells:
        r, c = y + dy, x + dx
        if c < 0 or c >= COLS or r >= ROWS:
            return False
        if r >= 0 and board[r][c]:
            return False
    return True


def _score(board, cleared):
    heights = []
    for c in range(COLS):
        h = 0
        for r in range(ROWS):
            if board[r][c]:
                h = ROWS - r
                break
        heights.append(h)
    holes = 0
    for c in range(COLS):
        seen = False
        for r in range(ROWS):
            if board[r][c]:
                seen = True
            elif seen:
                holes += 1
    bump = sum(abs(heights[i] - heights[i + 1]) for i in range(COLS - 1))
    # 经典权重：堆矮、洞少、表面平。
    s = -0.51 * sum(heights) - 3.0 * holes - 0.18 * bump
    # 用例要的是「一次消 ≥2 行」——消 1 行不送攻击行（GARBAGE_TABLE）。
    # 所以要**劝它别消单行**（攒着等两行一起消），光给多行加分不够：单行消
    # 每次都实打实地降 5 分高度，而多行的那 100 分要等到正好凑上才拿得到。
    if cleared == 1:
        s -= 30
    elif cleared >= 2:
        s += 40 + 25 * cleared
    return s


def best_actions(game):
    """给当前方块挑一套动作：转到位 → 横移 → 硬降。返回动作列表。"""
    board, piece = game['board'], game['current']['piece']
    shapes = game['shapes']
    spawn_x = game['current']['x']
    best = None
    for rot in range(4):
        cells = [(dy, dx) for dy, dx in shapes[piece][rot]]
        for x in range(-3, COLS + 1):
            if not _valid_at(board, cells, x, 0):
                continue
            y = 0
            while _valid_at(board, cells, x, y + 1):
                y += 1
            b2 = [row[:] for row in board]
            for dy, dx in cells:
                b2[y + dy][x + dx] = 1
            full = [r for r in range(ROWS) if all(b2[r])]
            for r in sorted(full):
                b2.pop(r)
                b2.insert(0, [0] * COLS)
            s = _score(b2, len(full))
            if best is None or s > best[0]:
                best = (s, rot, x)
    if best is None:
        return ['hard_drop']
    _s, rot, x = best
    dx = x - spawn_x
    return (['rotate_cw'] * rot
            + (['right'] * dx if dx > 0 else ['left'] * (-dx))
            + ['hard_drop'])


def recv_until(ws, types, skip=30):
    for _ in range(skip):
        msg = json.loads(ws.recv())
        if msg.get('type') in types:
            return msg
    raise AssertionError(f'没等到 {types}')


def attack(ws, target_clears=3, max_pieces=70):
    """不断摆放方块，直到送出 target_clears 次攻击行（或摆满 max_pieces 块）。

    每一步都是「发一串动作 → 等自己那条 `tetris_state` 回包」——回包里才有
    下一块是什么与权威盘面。机器人自己顶死时立刻收手（否则就干等回包了）。
    """
    game, sent, pieces = None, 0, 0
    pending = False
    for _ in range(4000):
        msg = json.loads(ws.recv())
        t = msg.get('type')
        if t == 'game_start':
            game = msg['game']
        elif t == 'tetris_state' and msg.get('user_id') is not None:
            game = msg
            pending = False
            if msg.get('cleared', 0) >= 2:
                sent += 1
                print(f'     机器人第 {pieces} 块一次消 {msg["cleared"]} 行 '
                      f'→ 顶对手 {msg.get("garbage_sent")} 行')
            if msg.get('finished'):
                print(f'     机器人自己顶死了（摆了 {pieces} 块）')
                return sent, pieces
        elif t == 'error':
            print(f'     机器人收到错误：{msg.get("message")}')
            pending = False
        if pending or game is None:
            continue
        if sent >= target_clears or pieces >= max_pieces:
            return sent, pieces
        pieces += 1
        pending = True
        ws.send(json.dumps({'type': 'tetris_actions', 'actions': best_actions(game)}))
    return sent, pieces


def main():
    print('=== Web E2E: 被攻击行顶到之后不能卡住 ===')
    r = requests.post(f'{API}/api/auth/register',
                      json={'username': UB, 'password': PWD})
    assert r.status_code == 200, r.text
    bot_token = r.json()['token']

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        page = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        page.set_default_timeout(15000)
        register_page(page, UA)
        goto_tab(page, '游戏')
        page.get_by_text('俄罗斯方块对战', exact=True).first.click()
        page.get_by_text('创建房间', exact=True).click()
        page.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        code = re.search(r'\b[A-Z0-9]{6}\b', page.locator('body').inner_text()).group(0)
        ok(f'浏览器玩家建房 {code}')

        # 机器人加入并准备
        assert requests.post(f'{API}/api/rooms/{code}/join', json={},
                             headers={'Authorization': f'Bearer {bot_token}'}).status_code == 200
        ws = create_connection(f'{WS}/ws/room/{code}?token={bot_token}', timeout=30)
        recv_until(ws, {'sync'})
        ws.send(json.dumps({'type': 'ready'}))
        ok('攻击机器人（原生 WebSocket）加入并准备')

        page.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        page.get_by_text('开始游戏', exact=True).click()
        page.locator('[data-testid="t-drop"]').wait_for(timeout=20000)
        ok('开局：浏览器玩家进入对局页')

        # 机器人猛打一阵，期间浏览器玩家什么都不点（重力照跑）
        sent, pieces = attack(ws, target_clears=3, max_pieces=70)
        assert sent >= 1, f'机器人一块都没送出攻击行（摆了 {pieces} 块），用例没测到目标场景'
        ok(f'机器人摆了 {pieces} 块、送出 {sent} 次攻击行')

        page.wait_for_timeout(600)
        top_hits = read_hud(page, '被顶')
        assert top_hits is not None, '读不到 HUD 的「被顶」计数'
        if top_hits == 0:
            # 机器人送出的攻击行应该都落到浏览器玩家头上（房间里只有两个人）
            raise AssertionError('被顶计数仍为 0——攻击行没送到受害者')
        ok(f'受害者的「被顶」计数 = {top_hits}（攻击行真的顶到了）')

        # 出局了？那提示必须看得见；没出局？那就必须还能玩（不能静默卡死）
        overlay = page.locator('[data-testid="t-out-overlay"]')
        if overlay.count() > 0 and overlay.is_visible():
            back = page.locator('[data-testid="t-back-room"]').bounding_box()
            vh = page.evaluate('() => window.innerHeight')
            assert back and back['y'] + back['height'] <= vh, \
                f'出局提示被挤出视口（{back} vs 视口高 {vh}）——玩家会以为卡住了'
            assert '你已结束' in overlay.inner_text() or '本局结束' in overlay.inner_text()
            ok('被顶到出局时，提示盖在屏幕上且在视口内')
        else:
            # 没出局：方块必须还在，而且重力还在推它（1.5s 内至少动一行）
            before = falling_cells(page)
            page.wait_for_timeout(1500)
            after = falling_cells(page)
            assert after, '盘面上一个方块都没有（当前方块凭空消失了）'
            assert before != after, f'被顶之后盘面卡死不动：{before} → {after}'
            # 还能操作：硬降一次，必须出下一块
            page.locator('[data-testid="t-drop"]').click()
            page.wait_for_timeout(900)
            after_drop = falling_cells(page)
            assert any(r <= 6 for r, _ in after_drop), \
                f'硬降后没有出下一块（界面卡死）：{after_drop}'
            ok('被顶之后照常继续：方块还在、重力在推、走子能锁定并出下一块')

        # 收尾：浏览器玩家认输 → 出局提示应该在视口内（回归 ②）
        page.get_by_text('认输', exact=True).click()
        page.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        page.get_by_text('确认认输', exact=True).click()
        page.get_by_text('你已结束', exact=False).wait_for(timeout=8000)
        back = page.locator('[data-testid="t-back-room"]').bounding_box()
        vh = page.evaluate('() => window.innerHeight')
        assert back and back['y'] + back['height'] <= vh, \
            f'出局后的「返回房间」在视口外（{back} vs 视口高 {vh}）'
        ok('出局提示始终在视口内（认输路径同样）')

        ws.close()
        browser.close()
    print(f'=== 通过：{STEP} 步 ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())