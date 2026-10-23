#!/usr/bin/env python3
"""
Web 端浏览器级 E2E 测试：双客户端五子棋完整对局 + 断线重连。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_web_game.py
依赖：pip install playwright（浏览器已缓存）
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'e2e_a_{TS}', f'e2e_b_{TS}'
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name, timeout=10000):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)
    ok(f'{username} 注册并自动登录，底部4Tab可见')


def square_cells(page):
    """返回棋盘 225 个交叉点的 bounding box，按 (y,x) 排序。
    Web 上 TouchableOpacity 渲染为 div[tabindex]；棋盘格尺寸一致，用尺寸聚类过滤其他按钮。"""
    btns = page.locator('div[tabindex]')
    squares = []
    for i in range(btns.count()):
        try:
            b = btns.nth(i).bounding_box()
        except Exception:
            continue
        if b and abs(b['width'] - b['height']) < 2 and 8 < b['width'] < 80:
            squares.append((round(b['y'], 1), round(b['x'], 1), round(b['width'])))
    if not squares:
        return []
    from collections import Counter
    size, _ = Counter(s[2] for s in squares).most_common(1)[0]
    cells = sorted(s for s in squares if s[2] == size)
    return cells


def click_cell(page, cells, x, y, confirm=True):
    """点一个交叉点。

    落子是**二次确认**的：第一下只放半透明预览子，再点同一位置才真的落子。
    confirm=False 用于「只想放预览、不落子」的断言（比如检查预览子出现）。
    """
    top, left, w = cells[y * 15 + x]
    page.mouse.click(left + w / 2, top + w / 2)
    page.wait_for_timeout(350)
    if confirm:
        page.mouse.click(left + w / 2, top + w / 2)
        page.wait_for_timeout(700)


def wait_board(page, timeout=20000):
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        cells = square_cells(page)
        if len(cells) >= 225:
            return cells
        page.wait_for_timeout(500)
    raise AssertionError(f'棋盘未出现，仅找到 {len(square_cells(page))} 个方形按钮')


def main():
    print('=== Web E2E: 双客户端五子棋对局 + 断线重连 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
        )
        ctx_a = browser.new_context(viewport={'width': 390, 'height': 844})
        ctx_b = browser.new_context(viewport={'width': 390, 'height': 844})
        pa, pb = ctx_a.new_page(), ctx_b.new_page()
        pa.set_default_timeout(15000)
        pb.set_default_timeout(15000)

        # 1. 双方注册登录
        register(pa, UA)
        register(pb, UB)

        # 2. A 创建房间
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_placeholder('房间名称（可选）').fill('E2E对战房')
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body = pa.locator('body').inner_text()
        m = re.search(r'\b[A-Z0-9]{6}\b', body)
        assert m, f'未找到6位房间号，页面内容: {body[:200]}'
        code = m.group(0)
        ok(f'A 创建房间成功，房间号 {code}')

        # 3. B 通过房间号加入
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        assert UA in pb.locator('body').inner_text(), 'B 的房间页未显示 A'
        ok('B 输入房间号加入成功，双方互见')

        # 4. B 准备（房主 A 无需准备，直接可点开始），房主开始
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        cells_a = wait_board(pa)
        cells_b = wait_board(pb)
        ok('全员准备→房主开始→双方自动进入对局页，棋盘渲染 225 交叉点')

        # 5a. 落子二次确认：第一下只放预览子（不落子），点别处改位置，再点同一处才落子
        click_cell(pa, cells_a, 7, 3, confirm=False)
        pa.locator('[data-testid="gomoku-pending-hint"]').wait_for(timeout=5000)
        assert pa.locator('[data-testid^="stone-"]').count() == 0, \
            '第一下点击不该真的落子'
        click_cell(pa, cells_a, 8, 3, confirm=False)   # 改到别处 → 预览跟着走
        assert pa.locator('[data-testid^="stone-"]').count() == 0, '移动预览时不该落子'
        click_cell(pa, cells_a, 7, 3)                  # 回 (7,3) 两下 → 落子
        assert pa.locator('[data-testid^="stone-"]').count() == 1, '二次确认后应落下一子'
        assert pa.locator('[data-testid="gomoku-last-mark"]').count() == 1, \
            '落子后应有最近一步标记'
        ok('落子二次确认：首点出预览子、点别处改位置、再点同一处才落子')

        # 5b. 对局：黑(A)五连获胜（(7,3) 已落，从第 2 手继续）
        moves = [('b', 9, 3), ('a', 7, 4), ('b', 9, 4),
                 ('a', 7, 5), ('b', 9, 5), ('a', 7, 6), ('b', 9, 6), ('a', 7, 7)]
        for who, x, y in moves:
            page, cells = (pa, cells_a) if who == 'a' else (pb, cells_b)
            click_cell(page, cells, x, y)
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=15000)
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=15000)
        # 最近一步标记必须停在最后一手 (7,7) 上，而不是留在开局那一手
        mark = pa.locator('[data-testid="gomoku-last-mark"]')
        assert mark.count() == 1, '终局后应恰好有一个最近一步标记'
        mb = mark.first.bounding_box()
        cell77 = cells_a[7 * 15 + 7]
        assert abs(mb['x'] + mb['width'] / 2 - (cell77[1] + cell77[2] / 2)) < 4, \
            '最近一步标记没落在最后一手 (7,7) 上'
        assert '黑棋胜' in pa.locator('body').inner_text(), 'A 未看到黑棋胜'
        assert '五连' in pa.locator('body').inner_text(), '未显示胜利原因五连'
        ok('9 手对局完成，双方均收到 game_over：黑棋胜（五连）')

        # 6. 结算只有「返回房间」：双方回房间大厅，房主续开新一局
        pa.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        cells_a = wait_board(pa)
        cells_b = wait_board(pb)
        ok('双方「返回房间」，房主续开新局，棋盘重置')

        # 7. 断线重连：关闭 B 页面(真实断开WS) → A 显示横幅 → B 重开页面按房间号重进 → 横幅消失、棋盘同步
        click_cell(pa, cells_a, 7, 7)  # 先落一子便于验证同步
        pb.close()
        pa.get_by_text('对方已断线', exact=False).wait_for(timeout=30000)
        ok('B 断开(关页面)后 A 出现「对方已断线」横幅，座位保留中')
        # B 重新打开页面：同 context 的 localStorage 保留 token，自动登录
        pb = ctx_b.new_page()
        pb.set_default_timeout(15000)
        pb.goto(BASE)
        pb.get_by_text('社交', exact=True).last.wait_for(timeout=30000)
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        # 重连进入对局中房间 → RoomScreen 收到 sync 自动跳转对局页
        cells_b = wait_board(pb, timeout=30000)
        deadline = time.time() + 30
        while time.time() < deadline:
            if '对方已断线' not in pa.locator('body').inner_text():
                break
            pa.wait_for_timeout(1000)
        assert '对方已断线' not in pa.locator('body').inner_text(), 'B 重连后 A 横幅未消失'
        ok('B 重开APP按房间号重连成功，自动回到对局，A 横幅消失')

        # B 重连后棋盘应包含 A 断线前落的子（sync 全量恢复）
        wait_board(pb)
        # 棋子带 testID stone-x-y（旧写法靠「方形按钮里有子元素」猜，会把帮助按钮
        # 也数进去，恒 ≥1，等于没断言）
        pb.locator('[data-testid^="stone-"]').first.wait_for(timeout=10000)
        stones = pb.locator('[data-testid^="stone-"]').count()
        assert stones >= 1, f'B 重连后棋盘未同步到棋子（stones={stones}）'
        ok(f'B 重连后收到 sync，棋盘同步（含 {stones} 枚棋子）')

        # 8. 对局可继续：B 认输（二次确认）→ 双方收到 game_over
        pb.get_by_text('认输', exact=True).click()
        pb.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=15000)
        assert '黑棋胜' in pa.locator('body').inner_text()
        ok('重连后对局正常继续，B 认输 → 黑棋胜，双方收到结算弹窗')

        # 9. 结算弹窗「返回房间」：关闭弹窗 + 回到房间页（房间保留，不删除）
        pa.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        # 验证房间仍存在，无「房间不存在」错误
        body_a = pa.locator('body').inner_text()
        body_b = pb.locator('body').inner_text()
        assert '房间不存在' not in body_a, f'A 返回后房间被删除: {body_a[:150]}'
        assert '房间不存在' not in body_b, f'B 返回后房间被删除: {body_b[:150]}'
        assert code in body_a, f'A 返回后未显示房间号 {code}'
        ok('双方点击结算「返回」，回到房间页，房间保留（未删除）')

        # 10. 终局回到 waiting，非房主重新准备后再续开。
        pb.get_by_text('准 备', exact=True).click(timeout=10000)
        # 等待并点击开始游戏
        start_btn = pa.get_by_text('开始游戏', exact=True)
        start_btn.wait_for(timeout=10000)
        # 检查按钮是否可点击（非 disabled）
        if start_btn.is_disabled():
            body_a = pa.locator('body').inner_text()
            raise AssertionError(f'开始游戏按钮不可点击，页面内容: {body_a[:300]}')
        start_btn.click()
        pa.wait_for_timeout(3000)
        # 调试：截图并检查页面状态
        pa.screenshot(path='/tmp/e2e_debug_after_start.png')
        body_a = pa.locator('body').inner_text()
        print(f'[DEBUG] A 点击开始游戏后页面内容 (前500字): {body_a[:500]}')
        if '对局结束' in body_a or '房间号' in body_a:
            # 可能还在房间页，检查是否有错误
            if '房间不存在' in body_a:
                raise AssertionError(f'开始游戏失败，房间不存在: {body_a[:300]}')
            if '错误' in body_a or '失败' in body_a:
                raise AssertionError(f'开始游戏失败: {body_a[:300]}')
        cells_a = wait_board(pa, timeout=20000)
        cells_b = wait_board(pb, timeout=20000)
        ok('房主再次点击「开始游戏」，双方重新进入对局页，房间可复用')

        pa.screenshot(path='/tmp/e2e_final_a.png')
        pb.screenshot(path='/tmp/e2e_final_b.png')
        browser.close()

    print(f'\n=== Web E2E 全部通过 ({STEP}/10 大步) ✓✓✓ ===')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f'\n!!! E2E 失败: {e}')
        sys.exit(1)
