#!/usr/bin/env python3
"""Web E2E：UNO 2v2 组队档（v1.16）。

用户要求「UNO 牌加入 2v2 组队模式」。这一档的核心是**一个人出完手牌不算赢** ——
把牌倒干净就跑，等于把队友一个人丢在场上。浏览器这一层能验的是「配对有没有
真的下发、界面有没有说出来」；「出完一个局还继续、两个都出完才算赢」在
`server/test/test_ws_uno.py::TestTeamsWire` 里直接断言消息内容。

覆盖：
  ① 大厅里能选到「组队」档，选完房间页摘要跟着变；
  ② 凑满 4 人开局 → 每个人的**对面那一格**被标成「队友」，其余两格不是；
  ③ 队友标识是**文字**（不是只靠边框颜色，WCAG 1.4.1）；
  ④ 人数不够时点「开始游戏」给一句能看懂的话，不是「点了没反应」。

用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_uno_teams.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
STEP = 0


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


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def open_settings(page):
    page.locator('[data-testid="room-settings-row"]').click()
    page.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
    page.wait_for_timeout(300)


def close_settings(page):
    page.locator('[data-testid="room-settings-close"]').click()
    page.wait_for_timeout(400)


def main():
    print('=== Web E2E: UNO 2v2 组队 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(20000)
        pb.set_default_timeout(20000)
        try:
            register(pa, f'ut{TS}a')
            register(pb, f'ut{TS}b')

            goto_tab(pa, '游戏')
            pa.get_by_text('UNO 卡牌', exact=True).first.click()
            pa.wait_for_timeout(600)
            pa.get_by_text('创建房间', exact=True).last.click()
            pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
            code = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text()).group(0)

            # ① 选「组队」档 —— 人不够时先点一次开始，确认服务端给的是人话
            open_settings(pa)
            pa.locator('[data-testid="room-settings-preset-team"]').click()
            pa.wait_for_timeout(600)
            close_settings(pa)
            body = pa.locator('body').inner_text()
            assert '组队' in body, f'房间页摘要没跟着变：{body[:200]}'
            ok('大厅里选到「组队」档，房间页摘要跟着变')

            # ② 先凑到 3 人：这时「开始游戏」是可点的（UNO 本来 ≥2 就能开），
            #    但组队档要求恰好 4 人 —— 服务端必须给一句能看懂的话。
            goto_tab(pb, '房间')
            pb.get_by_text('加入', exact=True).first.click()
            pb.get_by_placeholder('输入6位房间号').fill(code)
            pb.get_by_text('加入', exact=True).last.click()
            pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
            pb.get_by_text('准 备', exact=True).click()
            pa.get_by_text('添加人机', exact=True).click()
            pa.wait_for_timeout(900)
            pa.get_by_text('开始游戏', exact=True).click()
            pa.get_by_text('4 名玩家', exact=False).first.wait_for(timeout=8000)
            ok('3 人时开始：给出「需要恰好 4 名玩家」而不是点了没反应')

            # ③ 补到 4 人再开
            pa.get_by_text('添加人机', exact=True).click()
            pa.wait_for_timeout(900)
            pa.get_by_text('开始游戏', exact=True).click()

            # ④ 队友标识：A 是座位 0，队友必须是座位 2（对家）
            pa.locator('[data-testid="u-seat-2"]').wait_for(timeout=25000)
            mates = pa.locator('[data-testid^="u-mate-"]')
            assert mates.count() == 1, f'队友标识应该恰好一个：{mates.count()}'
            assert mates.first.get_attribute('data-testid') == 'u-mate-2', \
                mates.first.get_attribute('data-testid')
            assert mates.first.inner_text().strip() == '队友', mates.first.inner_text()
            ok('A（座位 0）的队友是座位 2，且标的是文字「队友」')

            # 对家配对是双向的：B 是座位 1，他的队友是座位 3
            pb.locator('[data-testid="u-seat-3"]').wait_for(timeout=25000)
            bmates = pb.locator('[data-testid^="u-mate-"]')
            assert bmates.count() == 1 and \
                bmates.first.get_attribute('data-testid') == 'u-mate-3', \
                bmates.first.get_attribute('data-testid')
            ok('B（座位 1）的队友是座位 3 —— 配对两边一致')
        finally:
            pa.close()
            pb.close()
            browser.close()
    print(f'=== UNO 2v2 E2E 通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
