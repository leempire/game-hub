#!/usr/bin/env python3
"""
Web E2E: battleship smoke test (create/join/start/placement/battle/surrender).
Usage: ensure backend (50541) and frontend web (50540) are running, then
python test/e2e_battleship_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'bs_a_{TS}', f'bs_b_{TS}'
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ok {msg}')


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def open_settings(page):
    """打开「玩法设置」抽屉（v1.15：房间级设置从行内芯片收进了底部抽屉）。"""
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


def main():
    print('=== Web E2E: 海战棋冒烟 ===')
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

        # A 创建海战棋房间
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('海战棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建海战棋房间 {code}')

        # ── 棋盘尺寸是**房间级设置**（v13）：房主在大厅里挑 ────────
        # 这一步与深空休眠的选图那一步同构：验的是「芯片接线 + 文案跟着变」，
        # 而尺寸**真的驱动开局**由 `test_ws_battleship_size.py` 与服务端的
        # `gs["size"]` 断言锁住（浏览器这边只看得到界面）。
        # v1.15 起这些芯片在「玩法设置」抽屉里（房间页只留一行摘要）。
        open_settings(pa)
        assert pa.locator('[data-testid="room-settings-board_size-6"]').count() == 1, \
            '房主看不到棋盘尺寸芯片'
        pa.locator('[data-testid="room-settings-board_size-6"]').click()
        pa.wait_for_timeout(600)
        assert '闪电战' in pa.locator('body').inner_text(), '切成 6×6 后抽屉里没跟着换名字'
        ok('房主把棋盘切成「闪电战」（6×6），抽屉里跟着换了名字')
        # 切回标准图（后面的断言都按 10×10 写）
        pa.locator('[data-testid="room-settings-board_size-10"]').click()
        pa.wait_for_timeout(600)
        assert '标准' in pa.locator('body').inner_text()
        close_settings(pa)
        assert '标准 · 10×10' in pa.locator('body').inner_text(), \
            '抽屉关了但房间页摘要没更新'
        ok('再切回标准 10×10（后面的断言按它走）')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        ok('双方进入海战棋布阵页面')

        # 等待布阵页面出现
        pa.get_by_text('完成布阵', exact=True).wait_for(timeout=15000)
        pb.get_by_text('完成布阵', exact=True).wait_for(timeout=15000)
        ok('双方看到布阵面板和完成布阵按钮')

        # 点击完成布阵 (使用默认随机布阵)
        pa.get_by_text('完成布阵', exact=True).click()
        pa.wait_for_timeout(1000)
        pb.get_by_text('完成布阵', exact=True).click()
        pb.wait_for_timeout(1000)
        ok('双方完成布阵')

        # 等待进入战斗阶段
        pa.get_by_text('攻击面板', exact=True).wait_for(timeout=15000)
        pb.get_by_text('攻击面板', exact=True).wait_for(timeout=15000)
        ok('双方进入战斗阶段，看到攻击面板')

        # 验证回合提示
        body_a = pa.locator('body').inner_text()
        body_b = pb.locator('body').inner_text()
        # One should see "轮到你开炮" and other "等待对方"
        has_turn = '轮到你开炮' in body_a or '轮到你开炮' in body_b
        assert has_turn, '双方都未看到轮到你开炮'
        ok('回合提示正确')

        # 蓝方先手 - 找到能开炮的一方并点击
        if '轮到你开炮' in body_a:
            shooter = pa
            other = pb
        else:
            shooter = pb
            other = pa

        # 点击攻击面板的某个格子 (大致位置)
        # 找到"攻击面板"文本下方的棋盘，点击一个格子
        attack_label = shooter.get_by_text('攻击面板', exact=True)
        attack_label.wait_for(timeout=5000)
        # 点击面板中心区域
        box = attack_label.bounding_box()
        if box:
            shooter.mouse.click(box['x'] + 60, box['y'] + 80)
        shooter.wait_for_timeout(2000)
        ok('蓝方开炮一次，双方收到 shot 广播')

        # 回归：攻击面板必须出现命中/未命中标识（曾因 attackBoard 传 null 永远空白）
        marker = shooter.locator(
            '[data-testid^="bs-attack-"][data-testid$="-miss"], '
            '[data-testid^="bs-attack-"][data-testid$="-hit"]'
        )
        marker.first.wait_for(timeout=5000)
        assert marker.count() >= 1, '开炮后攻击面板未出现命中/未命中标识'
        ok('攻击面板出现命中/未命中标识')

        # A 认输
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        body_b = pb.locator('body').inner_text()
        assert '对局结束' in body_b, 'B 未看到终局弹窗'
        ok('A 认输，双方看到对局结束弹窗')

        # 结算弹窗「返回房间」：回到房间页，房间保留（新契约）
        pa.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        # 验证房间仍存在，无错误提示
        body_a = pa.locator('body').inner_text()
        body_b = pb.locator('body').inner_text()
        assert '房间不存在' not in body_a, f'A 返回后房间被删除: {body_a[:150]}'
        assert '房间不存在' not in body_b, f'B 返回后房间被删除: {body_b[:150]}'
        assert code in body_a, f'A 返回后未显示房间号 {code}'
        ok('双方点击结算「返回」，回到房间页，房间保留（未删除）')

        browser.close()
    print(f'=== 海战棋冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
