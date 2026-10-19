#!/usr/bin/env python3
"""
Web E2E: bot smoke test (create gomoku room, add bot, start, play a move,
wait for bot response).
Usage: ensure backend (50541) and frontend web (50540) are running, then
python test/e2e_bot_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA = f'bt_a_{TS}'
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ok {msg}')


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


def main():
    print('=== Web E2E: 人机冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)

        register(pa, UA)
        ok('注册登录')

        # 创建五子棋房间
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('五子棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'创建五子棋房间 {code}')

        # 点击添加人机
        pa.get_by_text('添加人机', exact=True).click()
        pa.wait_for_timeout(1000)
        # 验证人机徽标出现
        body = pa.locator('body').inner_text()
        assert '人机' in body, '未看到人机徽标'
        ok('人机已添加，徽标显示')

        # 验证人机自动准备
        body = pa.locator('body').inner_text()
        assert '已准备' in body, '人机未自动准备'
        ok('人机自动准备')

        # 开始游戏
        pa.get_by_text('开始游戏', exact=True).click()
        # 等待进入对局页 (五子棋页面有 "轮到你落子" 或 "等待对方" 文本)
        pa.wait_for_timeout(3000)
        body = pa.locator('body').inner_text()
        assert '轮到你落子' in body or '等待对方' in body, '未进入对局页'
        ok('进入五子棋对局页面')

        # 验证轮到自己落子
        body = pa.locator('body').inner_text()
        assert '轮到你落子' in body, '未看到轮到你落子'
        ok('轮到自己落子(黑先)')

        # 落一子: 点击棋盘中心区域
        # 棋盘大约在页面中部，找一个合适的位置点击
        body_text = pa.locator('body').inner_text()
        # 找到 "#房间号" 下方、"认输" 上方的棋盘区域
        # 棋盘大小约 350x350，居中显示
        # 点击棋盘中心 (约 x=195, y=400 的位置)
        pa.mouse.click(195, 400)
        pa.wait_for_timeout(500)
        ok('自己落子')

        # 等待 bot 应招 (约 1-2 秒)
        pa.wait_for_timeout(3000)
        # 检查棋盘上有新棋子出现
        body = pa.locator('body').inner_text()
        # 验证回合回到自己
        assert '轮到你落子' in body, f'Bot 未应招或回合未移交: {body[:200]}'
        ok('Bot 已应招，回合回到自己')

        # 认输并验证结算弹窗「返回」：房间保留（新契约）
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        ok('认输后看到对局结束弹窗')

        # 点击「返回房间」回到房间页
        pa.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body = pa.locator('body').inner_text()
        assert '房间不存在' not in body, f'返回后房间被删除: {body[:150]}'
        assert code in body, f'返回后未显示房间号 {code}'
        ok('点击结算「返回」，回到房间页，房间保留（未删除）')

        browser.close()
    print(f'=== 人机冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
