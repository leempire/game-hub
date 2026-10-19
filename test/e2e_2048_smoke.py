#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：2048 竞速冒烟（建房/加入/开始/滑动落块/认输终局/返回房间）。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_2048_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'g2_a_{TS}', f'g2_b_{TS}'
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name):
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


def board_text(page):
    return page.locator('[data-testid="g2048-board"]').inner_text()


# 名次行是 g2048-lb-<user_id>；收起胶囊 g2048-lb-toggle 共用前缀，必须排除
LB_ROWS = '[data-testid^="g2048-lb-"]:not([data-testid="g2048-lb-toggle"])'


def open_leaderboard(page):
    """排行榜是右上角可收起浮层，默认收起；展开后才能读名次行。"""
    if page.locator('[data-testid="g2048-leaderboard"]').count() == 0:
        page.locator('[data-testid="g2048-lb-toggle"]').click()
        page.wait_for_timeout(300)


def swipe(page, dx, dy):
    """在棋盘上滑动（拖动 >阈值），验证手势路径而不是方向按钮。"""
    box = page.locator('[data-testid="g2048-board"]').bounding_box()
    cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + dx, cy + dy, steps=8)
    page.mouse.up()
    page.wait_for_timeout(700)


def main():
    print('=== Web E2E: 2048 竞速冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)
        pb.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        # A 创建 2048 房间
        goto_tab(pa, '游戏')
        pa.get_by_text('2048 竞速', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建 2048 房间 {code}')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        # 等双方对局状态到达：收起胶囊只在 game 就绪后渲染，等它比等棋盘可靠
        # （棋盘 View 在 game 为 null 时也已挂载，等它会提前放行）
        pa.locator('[data-testid="g2048-lb-toggle"]').wait_for(timeout=20000)
        pb.locator('[data-testid="g2048-lb-toggle"]').wait_for(timeout=20000)
        ok('双方进入 2048 对局页，棋盘渲染')

        # 实时排行榜：两人都在榜上（浮层默认收起，先展开）
        open_leaderboard(pa)
        rows = pa.locator(LB_ROWS)
        assert rows.count() == 2, f'排行榜行数应为 2，实际 {rows.count()}'
        ok('实时排行榜显示全部玩家（右上角浮层可展开）')

        # 同种子公平性：双方初始棋盘应一致
        assert board_text(pa) == board_text(pb), '初始棋盘不一致（同种子失效）'
        ok('同种子出块：双方初始棋盘一致')

        # A 用「滑动」走子：走的是手势路径，不是方向按钮
        # （曾经 PanResponder 闭包过期导致滑动完全无反应，只有按钮能用）
        initial = board_text(pa)
        for dx, dy in ((0, -110), (-110, 0), (0, 110), (110, 0)):
            swipe(pa, dx, dy)
            if board_text(pa) != initial:
                break
        assert board_text(pa) != initial, 'A 在棋盘上滑动后棋盘无变化（滑动手势未生效）'
        ok('A 滑动棋盘走子生效（手势路径）')

        # 方向按钮仍是可用备选路径
        btn_before = board_text(pa)
        for d in ('dir-up', 'dir-left', 'dir-up'):
            pa.locator(f'[data-testid="{d}"]').click()
            pa.wait_for_timeout(700)
        assert board_text(pa) != btn_before, 'A 走 3 步后棋盘无变化（move 未生效）'
        ok('A 方向按钮走子生效，棋盘更新')

        # 网页键盘（v1.14.x）：方向键 / WASD 也能走（四个方向依次试，理由同下）
        kb_before = board_text(pa)
        for key in ('ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft'):
            pa.keyboard.press(key)
            pa.wait_for_timeout(700)
            if board_text(pa) != kb_before:
                break
        assert board_text(pa) != kb_before, 'A 按方向键后棋盘无变化（键盘未生效）'
        ok('网页键盘：方向键走子生效')

        # B 也能独立走子（各自棋盘互不干扰）。
        # 不能只点一个方向：起始盘是随机种子出的，某个方向「走不动」是正常情况
        # （服务端静默忽略），单方向断言会 flaky。依次试四个方向直到盘面变化。
        b_initial = board_text(pb)
        for d in ('dir-down', 'dir-right', 'dir-up', 'dir-left'):
            pb.locator(f'[data-testid="{d}"]').click()
            pb.wait_for_timeout(700)
            if board_text(pb) != b_initial:
                break
        assert board_text(pb) != b_initial, 'B 四个方向都走不动（move 未生效）'
        ok('B 独立走子生效（双人各自棋盘）')

        # A 认输 → 只结束 A 自己，B 仍在玩（局不结束）
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pa.wait_for_timeout(1500)
        assert '对局结束' not in pb.locator('body').inner_text(), \
            'A 认输不应直接终结整局（各玩各的盘）'
        open_leaderboard(pb)
        assert '已结束' in pb.locator('body').inner_text(), 'B 未看到 A 已结束'
        ok('A 认输只结束自己，B 的盘继续')

        # B 也认输 → 全员结束 → 结算排名
        pb.get_by_text('认输', exact=True).click()
        pb.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pb.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        # 2048 是跑分竞速，不是胜负对抗：没人合出 2048 就只该看到「游戏结束」+
        # 名次，不能出现「你赢了」这类胜负措辞（排名第一 ≠ 胜利）。
        body_settle = pb.locator('body').inner_text()
        assert '游戏结束' in body_settle, f'结算标题不对: {body_settle[:200]}'
        assert '你赢了' not in body_settle and '胜' not in body_settle.split('对局结束')[-1], \
            f'2048 结算不该用胜负措辞: {body_settle[:200]}'
        assert re.search(r'第 \d+ / \d+ 名', body_settle), \
            f'2048 结算应给出名次: {body_settle[:200]}'
        ok('全员结束 → 双方看到结算（只给名次，无胜负措辞）')

        # 结算「返回房间」：房间保留可续开
        pb.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_b = pb.locator('body').inner_text()
        assert code in body_b and '房间不存在' not in body_b, f'返回后房间异常: {body_b[:150]}'
        ok('B 点「返回房间」回到大厅，房间保留（可续开）')

        # 单人局：1 人即可开局（无需等对手），排行榜只有自己
        pc = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pc.set_default_timeout(15000)
        register(pc, f'g2_c_{TS}')
        goto_tab(pc, '游戏')
        pc.get_by_text('2048 竞速', exact=True).first.click()
        pc.get_by_text('创建房间', exact=True).click()
        pc.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pc.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pc.get_by_text('开始游戏', exact=True).click()
        pc.locator('[data-testid="g2048-lb-toggle"]').wait_for(timeout=20000)
        open_leaderboard(pc)
        solo_rows = pc.locator(LB_ROWS)
        assert solo_rows.count() == 1, f'单人局排行榜应只有 1 行，实际 {solo_rows.count()}'
        pc.locator('[data-testid="dir-left"]').click()
        pc.wait_for_timeout(800)
        ok('单人 2048 可直接开局，排行榜只有自己')

        browser.close()
    print(f'=== 2048 竞速冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
