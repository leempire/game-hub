#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：对局中退出「别人还在继续」的局，必须真的回到房间页。

背景（用户实测报的 bug）：扫雷 / 飞行棋 / 2048 允许「本人已出局、其他人继续」，
这类玩家离开对局页后房间仍是 `playing`。房间页原来只看「status=playing 且有 game」
就自动跳对局页，于是玩家一点「退出」就被立刻弹回去——**表现为点了没反应、
页面闪一下还在原处**。

服务端用 sync 里的 `in_game` 告知「这个人还算不算在局玩家」，房间页据此决定
要不要自动跳转。本脚本是这条的服务端 + 客户端联合回归。

用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_leave_midgame.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
STEP = 0

# 有「退出」按钮、且退出后别人还能继续玩的游戏。
# marker 取「只在对局页出现」的元素选择器（用 testID 而不是文案：扫雷的
# 「放弃本局」在首次翻格前不显示——雷盘是懒生成的）。
GAMES = [
    ('2048', '2048', '[data-testid="g2048-board"]'),
    ('minesweeper', '扫雷', '[data-testid="mine-players-toggle"]'),
]

ROOM_MARKER = '点击复制'


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)


def run_case(browser, game, label, marker):
    """一局：A 建房 → B 加入 → 开局 → A 中途退出 → A 必须停在房间页。"""
    tag = 'g' if game == '2048' else 'm'
    pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
    pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
    pa.set_default_timeout(20000)
    pb.set_default_timeout(20000)
    try:
        register(pa, f'lm{tag}{TS}a')
        register(pb, f'lm{tag}{TS}b')

        pa.get_by_text('房间', exact=True).last.click()
        pa.wait_for_timeout(600)
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text(label, exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        code = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text()).group(0)

        pb.get_by_text('房间', exact=True).last.click()
        pb.wait_for_timeout(600)
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        # 等确实进了对局页
        pa.locator(marker).first.wait_for(timeout=20000)
        ok(f'{label}：开局，A 在对局页')

        # A 中途「退出」——此时 B 还在玩，房间仍是 playing
        pa.get_by_text('退出', exact=True).first.click()
        pa.get_by_text('确认退出', exact=True).wait_for(timeout=8000)
        pa.get_by_text('确认退出', exact=True).click()

        # 关键断言：A 必须停在房间页，不能被抓回对局页
        pa.get_by_text(ROOM_MARKER, exact=True).first.wait_for(timeout=15000)
        pa.wait_for_timeout(1500)          # 给可能发生的「弹回」留出时间
        body = pa.locator('body').inner_text()
        assert code in body, f'{label}：退出后未停在房间页（房间号不见）: {body[:200]}'
        assert ROOM_MARKER in body, f'{label}：退出后被弹回了对局页: {body[:200]}'
        assert pa.locator(marker).count() == 0, f'{label}：仍显示对局页元素 {marker}'
        ok(f'{label}：A 中途退出后停在房间页，未被弹回对局页')

        # B 仍在局中，不受影响
        assert pb.locator(marker).count() > 0, f'{label}：B 被误判出局'
        ok(f'{label}：B 仍在局中继续玩')
    finally:
        pa.close()
        pb.close()


def main():
    print('=== Web E2E: 对局中退出不被弹回 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        for game, label, marker in GAMES:
            run_case(browser, game, label, marker)
        browser.close()
    print(f'=== 通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
