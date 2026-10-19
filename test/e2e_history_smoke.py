#!/usr/bin/env python3
"""
Web E2E: 历史战绩冒烟。
流程：注册 → 建五子棋房 → 加人机 → 开始 → 认输(判负落库) → 我的 → 历史战绩 → 验证记录出现。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_history_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA = f'hx_a_{TS}'
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(700)


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)


def main():
    print('=== Web E2E: 历史战绩冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)

        register(pa, UA)
        ok('注册登录')

        # 创建五子棋房间 + 加人机
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('五子棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        code = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text()).group(0)
        pa.get_by_text('添加人机', exact=True).click()
        pa.wait_for_timeout(1000)
        assert '人机' in pa.locator('body').inner_text(), '未添加人机'
        ok(f'创建五子棋房 {code} 并添加人机')

        # 开始对局
        pa.get_by_text('开始游戏', exact=True).click()
        pa.get_by_text('认输', exact=True).wait_for(timeout=15000)
        ok('进入对局页')

        # 认输 → 判负 → 触发战绩落库
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=15000)
        ok('认输终局（战绩落库）')
        pa.wait_for_timeout(800)

        # 返回房间 → 退出到主 Tab
        pa.get_by_text('返回房间', exact=True).last.click()
        pa.wait_for_timeout(1000)
        leave = pa.get_by_text('退出房间', exact=True)
        if leave.count():
            leave.first.click()
            pa.wait_for_timeout(1200)
        ok('离开对局/房间')

        # 我的 → 历史战绩
        goto_tab(pa, '我的')
        pa.get_by_text('历史战绩', exact=True).first.click()
        pa.wait_for_timeout(1500)
        body = pa.locator('body').inner_text()
        assert '五子棋' in body, f'历史列表未见五子棋记录: {body[:200]}'
        ok('进入历史战绩，看到五子棋记录')

        # 认输判负 → 应显示"负"结果徽标
        assert '负' in body, f'未见判负结果徽标: {body[:200]}'
        ok('结果徽标显示「负」（认输判负）')

        browser.close()
    print(f'=== 历史战绩冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
