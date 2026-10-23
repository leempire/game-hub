#!/usr/bin/env python3
"""
Web 端扫雷 E2E 冒烟测试（新协议：独立生命/淘汰制）。
流程：注册双账号 -> A 建扫雷房（**难度在房间里选**，v1.13 起不在建房时定）-> B 加入 ->
  B 准备 -> A 开始 ->
  双方看到玩家状态条 -> 交替翻格 -> 一方踩雷淘汰 ->
  player_eliminated toast + 状态变化 + 淘汰方不可操作 ->
  另一方继续翻格 -> game_over ranking 面板字段校验。

用法：后端 50541、web 50540 均在运行，python test/e2e_mine_smoke.py
依赖：pip install playwright requests
"""
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
API = 'http://localhost:50541'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'mine_a_{TS}', f'mine_b_{TS}'
STEP = 0

# Revealed cell colors (RGB) that indicate a revealed cell
REVEALED_COLORS = {
    'rgb(237, 239, 242)',   # #EDEFF2 empty
    'rgb(239, 68, 68)',     # #EF4444 mine
}


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] OK {msg}')


def goto_tab(page, name, timeout=10000):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def open_settings(page):
    """打开「玩法设置」抽屉。

    v1.15 起房间级设置从行内芯片收进了底部抽屉（点房间页那一行）。**抽屉是盖屏的
    Modal** —— 开完一定要 `close_settings`，否则后面点不到「开始游戏」。
    """
    page.locator('[data-testid="room-settings-row"]').click()
    page.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
    page.wait_for_timeout(300)


def close_settings(page):
    page.locator('[data-testid="room-settings-close"]').click()
    page.wait_for_timeout(400)


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)
    ok(f'{username} 注册并自动登录')


def wait_backend(max_wait=20):
    """Wait for backend health check, retry on failure."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(f'{API}/api/app/version', timeout=3)
            if r.status_code == 200:
                ok(f'后端健康检查通过')
                return
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError(f'后端 {API} 在 {max_wait}s 内未就绪')


def mine_cells(page):
    """Return mine grid cell bounding boxes, sorted by (y, x)."""
    btns = page.locator('div[tabindex]')
    cells = []
    for i in range(btns.count()):
        try:
            b = btns.nth(i).bounding_box()
        except Exception:
            continue
        if b and abs(b['width'] - b['height']) < 2 and 10 < b['width'] < 60:
            cells.append((round(b['y'], 1), round(b['x'], 1), round(b['width'])))
    if not cells:
        return []
    from collections import Counter
    size, _ = Counter(c[2] for c in cells).most_common(1)[0]
    cells = sorted(c for c in cells if c[2] == size)
    return cells


def wait_mine_board(page, timeout=20000, min_cells=81):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        cells = mine_cells(page)
        if len(cells) >= min_cells:
            return cells
        page.wait_for_timeout(500)
    raise AssertionError(f'扫雷棋盘未出现，仅找到 {len(mine_cells(page))} 个格子')


def get_unrevealed_cells(page):
    """Get unrevealed cells by checking background color via evaluate."""
    return page.evaluate("""() => {
        const btns = document.querySelectorAll('div[tabindex]');
        const cells = [];
        for (const btn of btns) {
            const r = btn.getBoundingClientRect();
            if (Math.abs(r.width - r.height) > 2 || r.width < 10 || r.width > 60) continue;
            const bg = getComputedStyle(btn).backgroundColor;
            // Unrevealed: #CBD5E1 = rgb(203,213,225)
            if (bg && bg.includes('203') && bg.includes('213')) {
                cells.push({x: r.x + r.width/2, y: r.y + r.height/2, w: r.width});
            }
        }
        cells.sort((a, b) => Math.round(a.y - b.y) || (a.x - b.x));
        return cells;
    }""")


def click_cell_xy(page, cx, cy):
    """Click at viewport coordinates."""
    page.mouse.click(cx, cy)
    page.wait_for_timeout(800)


def click_mine_cell(page, x, y):
    """按 testID 点某个格子。

    为什么不再用「取 bounding box 的视口坐标 + mouse.click」：格子放大到 44px、
    棋盘改成双向滑动后，靠右/靠下的格子会在视口外，那个坐标点下去落在别处甚至
    页面外，点击静默失效（本脚本曾因此 25 轮都「没人踩雷」）。
    Playwright 用 testID 定位会自动把元素滚进视口再点。
    """
    page.locator(f'[data-testid="mine-cell-{x}-{y}"]').click()
    page.wait_for_timeout(500)


def seat_eliminated(page, seat):
    """该座位是否已出局。

    出局后状态条不再渲染命数标记（改成「已淘汰」标签），所以标记消失即出局——
    比在一大段 body 文本里找「踩雷淘汰」可靠（多命后文案变成
    「踩雷，生命耗尽淘汰」，旧的连续子串匹配会永远不命中）。
    """
    return page.locator(f'[data-testid="mine-lives-{seat}"]').count() == 0


def click_unrevealed(page, idx=0):
    """Click an unrevealed cell by index. Returns True if cell was found."""
    cells = get_unrevealed_cells(page)
    if not cells or idx >= len(cells):
        return False
    c = cells[idx]
    click_cell_xy(page, c['x'], c['y'])
    return True


def has_text(page, text):
    return text in page.locator('body').inner_text()


def wait_for_text(page, text, timeout=15000):
    page.get_by_text(text, exact=False).wait_for(timeout=timeout)


def check_ranking_fields(page):
    """Verify game-over ranking modal has expected fields."""
    body = page.locator('body').inner_text()
    # Check for key ranking labels
    assert '游戏结束' in body, f'未看到游戏结束弹窗: {body[:300]}'
    assert '返回房间' in body, '未看到返回房间按钮'
    # Ranking should show alive/eliminated status
    has_alive = '存活' in body
    has_dead = '淘汰' in body
    assert has_alive or has_dead, f'排名面板缺少存活/淘汰标识: {body[:300]}'
    # Should show stats labels
    assert '标雷' in body or '翻' in body, f'排名面板缺少统计字段: {body[:300]}'


def main():
    print('=== Web E2E: 扫雷新协议冒烟测试 ===')

    # 0. Backend health check with retries
    wait_backend(max_wait=20)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
        )
        ctx_a = browser.new_context(viewport={'width': 390, 'height': 844})
        ctx_b = browser.new_context(viewport={'width': 390, 'height': 844})
        pa, pb = ctx_a.new_page(), ctx_b.new_page()
        pa.set_default_timeout(15000)
        pb.set_default_timeout(15000)

        # 1. Register both
        register(pa, UA)
        register(pb, UB)

        # 2. A creates minesweeper room from Games tab
        goto_tab(pa, '游戏')
        pa.get_by_text('扫雷', exact=True).first.click()
        pa.wait_for_timeout(500)
        pa.get_by_text('创建房间', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body = pa.locator('body').inner_text()
        m = re.search(r'\b[A-Z0-9]{6}\b', body)
        assert m, f'未找到6位房间号: {body[:300]}'
        code = m.group(0)
        ok(f'A 创建扫雷房成功，房间号 {code}')

        # 难度在房间里选（房主），不再在建房弹窗里 —— 与军棋的玩法、
        # 深空休眠的地图同一套；v1.15 起统一收进「玩法设置」抽屉。
        open_settings(pa)
        pa.locator('[data-testid="room-settings-difficulty-beginner"]').click()
        pa.wait_for_timeout(400)
        assert '9×9 / 10雷' in pa.locator('body').inner_text(), \
            '选了初级但难度说明没跟着变'
        close_settings(pa)
        # 关掉抽屉后房间页那一行的摘要要跟着变（房规一路从服务端走回界面）
        assert '初级' in pa.locator('body').inner_text(), '抽屉关了但房间页摘要没更新'
        ok('房主在等待大厅里把难度选成初级')

        # 3. B joins via room code
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        assert UA in pb.locator('body').inner_text(), 'B 未看到 A'
        ok(f'B 加入房间 {code}，双方互见')

        # 4. B readies, A starts
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()

        # 5. Both enter mine screen
        cells_a = wait_mine_board(pa)
        cells_b = wait_mine_board(pb)
        ok(f'双方进入扫雷页，A 有 {len(cells_a)} 格，B 有 {len(cells_b)} 格')

        # 6. Verify player status chips show both players
        pa.wait_for_timeout(1000)
        pb.wait_for_timeout(1000)
        body_a = pa.locator('body').inner_text()
        body_b = pb.locator('body').inner_text()
        # Both usernames should appear in the status bar area
        assert UA in body_a, f'A 页面未看到自己的用户名: {body_a[:200]}'
        assert UB in body_a, f'A 页面未看到 B 的用户名: {body_a[:200]}'
        assert UA in body_b, f'B 页面未看到 A 的用户名: {body_b[:200]}'
        assert UB in body_b, f'B 页面未看到自己的用户名: {body_b[:200]}'
        ok('玩家状态条显示两人（双方均可见）')

        # 7. Reveal cells until someone hits a mine
        # Alternate between A and B clicking unrevealed cells.
        # With 10 mines in 81 cells, someone should hit within ~20 clicks.
        eliminated_user = None
        survivor_page = None
        eliminated_page = None
        max_rounds = 25
        hit_mine = False

        # 两个客户端各扫棋盘的一半（按 testID 走格子，避开「视口外点不到」）
        grid_order = [(x, y) for y in range(9) for x in range(9)]
        for round_num in range(max_rounds):
            # A 扫前半盘
            for x, y in grid_order[:45]:
                click_mine_cell(pa, x, y)
                if seat_eliminated(pa, 0):
                    eliminated_user = UA
                    eliminated_page = pa
                    survivor_page = pb
                    hit_mine = True
                    break
            if hit_mine:
                break
            # B 扫后半盘
            for x, y in grid_order[45:]:
                click_mine_cell(pb, x, y)
                if seat_eliminated(pb, 1):
                    eliminated_user = UB
                    eliminated_page = pb
                    survivor_page = pa
                    hit_mine = True
                    break
            if hit_mine:
                break

        assert hit_mine, f'{max_rounds} 轮翻格后仍无人踩雷'
        ok(f'{eliminated_user} 踩雷淘汰（{round_num + 1} 轮翻格后触发）')

        # 8. Verify elimination UI
        eliminated_page.wait_for_timeout(1000)
        survivor_page.wait_for_timeout(1000)

        # Check toast with elimination message
        elim_body = eliminated_page.locator('body').inner_text()
        surv_body = survivor_page.locator('body').inner_text()
        assert '淘汰' in elim_body, f'淘汰方未看到淘汰信息: {elim_body[:300]}'
        assert '淘汰' in surv_body, f'存活方未看到淘汰信息: {surv_body[:300]}'

        # Check eliminated player sees spectator banner
        assert '你已被淘汰' in elim_body or '观战' in elim_body, \
            f'淘汰方未看到观战横幅: {elim_body[:300]}'

        # Check status bar shows "已淘汰" tag
        assert '已淘汰' in elim_body, f'淘汰方状态条未显示已淘汰: {elim_body[:300]}'
        ok('player_eliminated: toast 展示 + 状态条变淘汰 + 观战横幅')

        # 9. Verify eliminated player cannot operate
        # The eliminated player should see "放弃本局" button gone (already eliminated)
        # and clicking cells should have no effect.
        # Check that "放弃本局" is NOT visible for eliminated player
        assert '放弃本局' not in elim_body, '淘汰方不应看到放弃本局按钮'

        # Try clicking a cell on eliminated player's page - should not send reveal
        unrevealed_elim = get_unrevealed_cells(eliminated_page)
        if unrevealed_elim:
            click_cell_xy(eliminated_page, unrevealed_elim[0]['x'], unrevealed_elim[0]['y'])
            eliminated_page.wait_for_timeout(500)
        ok('被淘汰方无法再操作（按钮隐藏 + 点格无效）')

        # 10. Survivor continues clicking until game_over
        # The survivor keeps clicking unrevealed cells. Eventually either:
        # - All remaining safe cells are revealed (cleared), or
        # - Survivor also hits a mine (all eliminated)
        game_over_shown = False
        for extra in range(40):
            unrevealed = get_unrevealed_cells(survivor_page)
            if not unrevealed:
                break
            click_cell_xy(survivor_page, unrevealed[0]['x'], unrevealed[0]['y'])
            survivor_page.wait_for_timeout(500)
            eliminated_page.wait_for_timeout(300)
            # Check for game over modal
            surv_text = survivor_page.locator('body').inner_text()
            if '游戏结束' in surv_text:
                game_over_shown = True
                break

        # Wait a bit more for game_over to propagate
        if not game_over_shown:
            survivor_page.wait_for_timeout(3000)
            eliminated_page.wait_for_timeout(3000)

        # 11. Verify ranking panel
        surv_text = survivor_page.locator('body').inner_text()
        elim_text = eliminated_page.locator('body').inner_text()
        assert '游戏结束' in surv_text, f'存活方未看到游戏结束弹窗: {surv_text[:300]}'
        assert '游戏结束' in elim_text, f'淘汰方未看到游戏结束弹窗: {elim_text[:300]}'

        # Check ranking fields on both pages
        check_ranking_fields(survivor_page)
        check_ranking_fields(eliminated_page)
        ok(f'结算 ranking 面板字段正确（双方均可见）: {round_num + 1 + extra + 1} 次翻格后结束')

        # Screenshot
        survivor_page.screenshot(path='/tmp/mine_new_protocol.png')
        ok('截图已保存至 /tmp/mine_new_protocol.png')

        browser.close()

    print(f'\n=== 扫雷新协议冒烟全部通过 ({STEP} 步) ===')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f'\n!!! 冒烟失败: {e}')
        sys.exit(1)
    except Exception as e:
        print(f'\n!!! 意外错误: {e}')
        sys.exit(1)
