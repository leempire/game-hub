#!/usr/bin/env python3
"""
Web 端扫雷 E2E：格子尺寸 / 双向滑动 / 生命值。
流程：注册 -> A 建扫雷房（单人可开）-> **在房间里把难度切到中级** -> 开局 ->
  格子实测 44px -> 棋盘宽度 704px（不缩格）-> 真的能横向滑动 ->
  命数显示 2/2 -> 踩雷扣命（toast + 1/2，不淘汰）-> 再踩雷 -> 淘汰结算。

为什么单独一个脚本：`e2e_mine_smoke.py` 走的是初级房的淘汰流程，
这里是「中级格子放大 + 多命」这两条新契约的回归守卫。

用法：后端 50541、web 50540 均在运行，python test/e2e_mine_board.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA = f'mineb_a_{TS}'
STEP = 0

CELL = 44                 # 期望格子边长（HIG 44pt）
MINE_RGB = 'rgb(185, 28, 28)'   # 踩中的雷格底色 #B91C1C


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] OK {msg}')


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


def cell_bg(page, x, y):
    loc = page.locator(f'[data-testid="mine-cell-{x}-{y}"]')
    if loc.count() == 0:
        return None
    return loc.first.evaluate('el => getComputedStyle(el).backgroundColor')


def main():
    print('=== Web E2E: 扫雷格子尺寸 / 双向滑动 / 生命值 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
        )
        page = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        page.set_default_timeout(15000)

        register(page, UA)
        ok('注册登录')

        # A 建扫雷房（1 人即可开）—— 难度**进房之后**由房主选
        goto_tab(page, '房间')
        page.get_by_text('创建', exact=True).first.click()
        page.get_by_text('扫雷', exact=True).last.click()
        page.get_by_text('创建', exact=True).last.click()
        page.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        code = re.search(r'\b[A-Z0-9]{6}\b', page.locator('body').inner_text()).group(0)

        # 难度芯片真的能切：先切初级（说明跟着变），再切回中级
        open_settings(page)
        page.locator('[data-testid="room-settings-difficulty-beginner"]').click()
        page.wait_for_timeout(400)
        assert '9×9 / 10雷' in page.locator('body').inner_text(), \
            '切到初级后难度说明没跟着变'
        page.locator('[data-testid="room-settings-difficulty-intermediate"]').click()
        page.wait_for_timeout(400)
        assert '16×16 / 40雷' in page.locator('body').inner_text(), \
            '切回中级后难度说明没跟着变'
        close_settings(page)
        assert '中级' in page.locator('body').inner_text(), '抽屉关了但房间页摘要没更新'
        ok(f'扫雷房 {code}：难度在房间里可切（初级 → 中级）')

        page.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        page.get_by_text('开始游戏', exact=True).click()
        page.locator('[data-testid="mine-cell-0-0"]').wait_for(timeout=20000)
        page.wait_for_timeout(500)
        ok(f'中级房 {code} 开局')

        # 格子尺寸：实测 44×44，不再按屏宽压到 22px
        box = page.locator('[data-testid="mine-cell-0-0"]').bounding_box()
        assert box, '找不到格子'
        assert abs(box['width'] - CELL) < 1.5, f'格宽 {box["width"]}px，期望 {CELL}px'
        assert abs(box['height'] - CELL) < 1.5, f'格高 {box["height"]}px，期望 {CELL}px'
        ok(f'格子实测 {round(box["width"])}×{round(box["height"])}px（HIG 44pt 达标）')

        # 棋盘不缩格：16 列 × 44 = 704px，比 390 的视口宽 → 必须能滑
        board = page.locator('[data-testid="mine-board"]').bounding_box()
        assert abs(board['width'] - 16 * CELL) < 2, \
            f'棋盘宽 {board["width"]}px，期望 {16 * CELL}px（被缩格了？）'
        ok(f'棋盘 {round(board["width"])}px 未缩格（超出 390 视口）')

        # 真的能横向滑动：在棋盘上横向滚轮，棋盘的 x 应左移
        before_x = board['x']
        page.mouse.move(195, 500)
        page.mouse.wheel(300, 0)
        page.wait_for_timeout(400)
        after = page.locator('[data-testid="mine-board"]').bounding_box()
        assert after['x'] < before_x - 50, \
            f'横向滑动无效：x 从 {before_x} 到 {after["x"]}'
        ok(f'横向滑动生效（x {round(before_x)} → {round(after["x"])}）')

        # 纵向滑动：16 行 × 44 = 704px，比可视高度高
        assert abs(board['height'] - 16 * CELL) < 2, \
            f'棋盘高 {board["height"]}px，期望 {16 * CELL}px'
        before_y = after['y']
        page.mouse.move(195, 500)
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(400)
        after_v = page.locator('[data-testid="mine-board"]').bounding_box()
        assert after_v['y'] < before_y - 50, \
            f'纵向滑动无效：y 从 {before_y} 到 {after_v["y"]}'
        ok(f'纵向滑动生效（y {round(before_y)} → {round(after_v["y"])}）')

        # 命数：中级每人 2 条，状态条上显示 2/2
        lives = page.locator('[data-testid="mine-lives-0"]')
        assert lives.count() == 1, '状态条上没有命数标记'
        assert '2/2' in lives.first.inner_text(), f'命数显示 {lives.first.inner_text()}'
        ok('中级开局命数显示 2/2')

        # 首翻必安全
        page.locator('[data-testid="mine-cell-8-8"]').click()
        page.wait_for_timeout(600)
        assert cell_bg(page, 8, 8) != MINE_RGB, '首翻格不该是雷'
        assert '2/2' in lives.first.inner_text(), '安全格不该扣命'

        # 一直翻到踩雷：踩中后只扣命、不淘汰
        hit = None
        for i in range(120):
            x, y = i % 16, i // 16
            if (x, y) == (8, 8):
                continue
            loc = page.locator(f'[data-testid="mine-cell-{x}-{y}"]')
            if loc.count() == 0:
                continue
            loc.click()
            page.wait_for_timeout(120)
            if cell_bg(page, x, y) == MINE_RGB:
                hit = (x, y)
                break
        assert hit, '翻了 120 格没踩到雷，测试假设失效'
        page.wait_for_timeout(400)
        body = page.locator('body').inner_text()
        assert '还剩 1 条命' in body, f'踩雷未提示剩余命数: {body[:300]}'
        assert '1/2' in lives.first.inner_text(), \
            f'命数未更新: {lives.first.inner_text()}'
        assert '已淘汰' not in body, '中级第一次踩雷不该直接淘汰'
        ok(f'踩雷 {hit} → 扣命提示 + 命数 1/2（未淘汰）')

        # 再踩一颗 → 淘汰
        hit2 = None
        for i in range(256):
            x, y = i % 16, i // 16
            if (x, y) == (8, 8) or (x, y) == hit:
                continue
            loc = page.locator(f'[data-testid="mine-cell-{x}-{y}"]')
            if loc.count() == 0:
                continue
            loc.click()
            page.wait_for_timeout(120)
            if cell_bg(page, x, y) == MINE_RGB:
                hit2 = (x, y)
                break
        assert hit2, '第二条命没踩到雷，测试假设失效'
        page.wait_for_timeout(600)
        body = page.locator('body').inner_text()
        assert '已淘汰' in body or '生命耗尽' in body, \
            f'命扣完应淘汰: {body[:300]}'
        ok(f'再踩雷 {hit2} → 生命耗尽淘汰')

        page.screenshot(path='/tmp/mine_board_lives.png')
        ok('截图已保存至 /tmp/mine_board_lives.png')

        browser.close()
    print(f'\n=== 扫雷格子/滑动/生命值 E2E 全部通过 ({STEP} 步) ===')


if __name__ == '__main__':
    try:
        main()
    except AssertionError as e:
        print(f'\n!!! 失败于第 {STEP + 1} 步: {e}')
        sys.exit(1)
    except Exception as e:
        print(f'\n!!! 意外错误: {e}')
        sys.exit(1)
