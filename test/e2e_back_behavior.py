#!/usr/bin/env python3
"""
Web 端浏览器返回行为 E2E：主Tab双击返回离开 / 弹窗打开时返回关弹窗 / 对局中返回弹确认框。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_back_behavior.py
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
    print('=== Web E2E: 返回行为 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )

        # --- Part 1: 主Tab 双击返回离开；弹窗返回关闭 ---
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)
        register(pa, f'bk_a_{TS}')
        ok('注册进入主Tab')

        pa.go_back()
        pa.get_by_text('再按一次返回退出 GameHub', exact=False).first.wait_for(timeout=5000)
        assert '社交' in pa.locator('body').inner_text(), '首次返回后离开了应用'
        ok('主Tab首次返回：提示再按一次，停留在应用内')

        pa.wait_for_timeout(2500)  # 超过 2s 窗口，计数重置
        pa.go_back()
        pa.get_by_text('再按一次返回退出 GameHub', exact=False).first.wait_for(timeout=5000)
        pa.wait_for_timeout(400)  # 等 forward() 取消导航落定，模拟真实按键间隔
        pa.go_back()  # 窗口内第二次 → 离开站点
        pa.wait_for_timeout(800)
        assert 'localhost:50540' not in pa.url, f'双击返回后未离开站点: {pa.url}'
        ok('窗口内二次返回：离开站点（Web 的退出语义）')

        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb.set_default_timeout(15000)
        register(pb, f'bk_b_{TS}')
        goto_tab(pb, '房间')
        pb.get_by_text('创建', exact=True).first.click()
        pb.get_by_text('创建房间', exact=True).first.wait_for(timeout=5000)
        pb.go_back()
        pb.wait_for_timeout(700)
        body = pb.locator('body').inner_text()
        assert '创建房间' not in body, '返回后弹窗未关闭'
        assert '浏览房间' in body or '加入' in body, '返回后离开了房间Tab'
        ok('弹窗打开时返回：关闭弹窗且停留在原页面')

        # 房间页的「玩法设置」抽屉也是弹窗（v1.15 新用户）—— 返回键应当**只关抽屉**，
        # 不能把人也带出房间页。这条是 `useModalBackGuard` 的契约测试。
        # 走「游戏」Tab 建房（其余五个 E2E 都是这条路径）。**不能用「房间」Tab 的
        # 建房弹窗**：这一步之前刚用它开过一次并返回关掉了，RNW 的 Modal 第二次打开时
        # 背板会拦下点击（实测 TimeoutError: backdrop intercepts pointer events）。
        goto_tab(pb, '游戏')
        pb.get_by_text('UNO 卡牌', exact=True).first.click()
        pb.wait_for_timeout(600)
        pb.get_by_text('创建房间', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.locator('[data-testid="room-settings-row"]').click()
        pb.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
        pb.go_back()
        pb.wait_for_timeout(700)
        body = pb.locator('body').inner_text()
        assert pb.locator('[data-testid="room-settings-sheet"]').count() == 0, \
            '返回后设置抽屉没关'
        assert '房间号' in body, f'返回后离开了房间页：{body[:200]}'
        ok('设置抽屉打开时返回：只关抽屉，仍留在房间页')

        # --- Part 2: 对局中返回弹确认框 ---
        pc = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pd = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pc.set_default_timeout(15000)
        pd.set_default_timeout(15000)
        register(pc, f'bk_c_{TS}')
        register(pd, f'bk_d_{TS}')
        goto_tab(pc, '房间')
        pc.get_by_text('创建', exact=True).first.click()
        pc.get_by_text('创建', exact=True).last.click()
        pc.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        code = re.search(r'\b[A-Z0-9]{6}\b', pc.locator('body').inner_text()).group(0)
        goto_tab(pd, '房间')
        pd.get_by_text('加入', exact=True).first.click()
        pd.get_by_placeholder('输入6位房间号').fill(code)
        pd.get_by_text('加入', exact=True).last.click()
        pd.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pd.get_by_text('准 备', exact=True).click()
        pc.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pc.get_by_text('开始游戏', exact=True).click()
        pc.get_by_text('认输', exact=True).wait_for(timeout=15000)
        pd.get_by_text('认输', exact=True).wait_for(timeout=15000)
        ok('五子棋对局开始（用于返回确认测试）')

        pc.go_back()
        pc.get_by_text('确认退出', exact=True).first.wait_for(timeout=5000)
        assert '再想想' in pc.locator('body').inner_text(), '确认弹窗不完整'
        ok('对局中返回：弹出退出确认框，未直接离开')

        pc.get_by_text('再想想', exact=True).click()
        pc.wait_for_timeout(500)
        pc.go_back()
        pc.get_by_text('确认退出', exact=True).first.wait_for(timeout=5000)
        pc.get_by_text('确认退出', exact=True).first.click()
        pc.wait_for_timeout(1200)
        body = pc.locator('body').inner_text()
        assert '选择游戏' in body or '房间号' in body, f'确认退出后未离开对局页: {body[:150]}'
        # 新契约：确认退出 = 认输/淘汰语义，房间保留（finished 状态）
        # 验证无错误提示（如「房间不存在」或「房间已解散」）
        assert '房间不存在' not in body, f'确认退出后房间被删除: {body[:150]}'
        assert '房间已解散' not in body, f'确认退出后房间被解散: {body[:150]}'
        ok('确认退出后离开对局页，房间保留（无错误提示）')

        # --- Part 3: 深空休眠（跑动阶段）返回 → 确认离开 → 停在房间页 ---
        # 用户实测报的「一些游戏在游戏中点返回想强制退出时没反应、返回后闪一下还在
        # 原处」有一半出在深空休眠，而且是**两个缺陷叠在一起**：
        #   ① `Stasis` 不在 backGuard 的 PROTECTED_SCREENS 里 → 按返回压根不弹确认框，
        #      只是导航回房间页，而房间页又把人弹回对局页（= 闪一下）；
        #   ② `stasis.surrender` 要求 `phase == "playing"` → **跑动阶段**点退出时
        #      REST `/leave` 抛 500（响应没有 CORS 头，浏览器报成 CORS 错误），
        #      客户端静默吞掉、人没被淘汰 → 房间页照样把人弹回去。
        # 这条走的是「浏览器返回」这条真实路径，两个缺陷任缺一个都会红。
        pe = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pe.set_default_timeout(20000)
        register(pe, f'bk_e_{TS}')
        goto_tab(pe, '游戏')
        pe.get_by_text('深空休眠', exact=True).first.click()
        pe.wait_for_timeout(600)
        pe.get_by_text('创建房间', exact=True).last.click()
        pe.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pe.get_by_text('添加人机', exact=True).click()
        pe.wait_for_timeout(800)
        pe.get_by_text('开始游戏', exact=True).click()
        pe.locator('[data-testid="stasis-map"]').first.wait_for(timeout=25000)
        ok('深空休眠对局开始（跑动阶段）')

        pe.go_back()
        pe.get_by_text('确认离开', exact=True).first.wait_for(timeout=5000)
        ok('深空休眠对局中返回：弹出「确认离开对局？」确认框')

        pe.get_by_text('确认离开', exact=True).first.click()
        pe.get_by_text('点击复制', exact=True).first.wait_for(timeout=15000)
        pe.wait_for_timeout(1800)          # 给可能发生的「弹回对局页」留出时间
        body = pe.locator('body').inner_text()
        assert pe.locator('[data-testid="stasis-map"]').count() == 0, \
            f'确认离开后被弹回对局页: {body[:150]}'
        assert '点击复制' in body, f'确认离开后未停在房间页: {body[:150]}'
        ok('确认离开后停在房间页，未被弹回对局页（跑动阶段退出可用）')

        browser.close()
    print(f'=== 返回行为 E2E 通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
