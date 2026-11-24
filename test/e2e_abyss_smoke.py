#!/usr/bin/env python3
"""临时 E2E：深渊回声 UI 冒烟（建房 / 房规 / 开局 / 升级选择 / 装备面板）。

用完即删；只验证浏览器接线，不替代 server/test/test_abyss.py 的引擎断言。
"""
import re
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'ab_a_{TS}', f'ab_b_{TS}'
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
    page.get_by_text('我的', exact=True).last.wait_for(timeout=20000)


def body(page):
    return page.locator('body').inner_text()


def main():
    print('=== Web E2E: 深渊回声冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = {'viewport': {'width': 390, 'height': 844}}
        pa = browser.new_context(**ctx).new_page()
        pb = browser.new_context(**ctx).new_page()
        for pg in (pa, pb):
            pg.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        goto_tab(pa, '游戏')
        pa.get_by_text('深渊回声', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        match = re.search(r'\b[A-Z0-9]{6}\b', body(pa))
        assert match, '未找到6位房间号'
        code = match.group(0)
        ok(f'A 创建深渊回声房间 {code}')

        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.wait_for_timeout(600)
        assert '2/4' in body(pa), f'房间人数应显示 2/4：{body(pa)[:200]}'

        pa.locator('[data-testid="room-settings-row"]').click()
        pa.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
        assert '对局时长' in body(pa) and '经验节奏' in body(pa), '深渊回声房规未下发'
        pa.locator('[data-testid="room-settings-duration-1200"]').click()
        pa.wait_for_timeout(600)
        assert '20 分钟' in body(pa), '切到 20 分钟后摘要没变'
        pa.locator('[data-testid="room-settings-xp_pace-fast"]').click()
        pa.wait_for_timeout(600)
        assert '快速' in body(pa), '切到快速经验后摘要没变'
        pa.locator('[data-testid="room-settings-close"]').click()
        pa.wait_for_timeout(400)
        ok('房规：20 分钟 + 快速经验')

        pa.get_by_text('开始游戏', exact=True).click()
        for pg in (pa, pb):
            pg.locator('[data-testid="abyss-arena"]').wait_for(timeout=20000)
        ok('双方进入战场')

        clock = pa.locator('[data-testid="abyss-clock"]').inner_text()
        assert clock.startswith('20:') or clock.startswith('19:'), f'对局时长不是 20 分钟：{clock}'
        assert '武器 1/6' in body(pa), f'初始武器槽显示不对：{body(pa)[:300]}'
        assert pa.locator('[data-testid="abyss-inventory"]').count() == 1
        ok(f'HUD 正常（倒计时 {clock}，初始 1 把武器）')

        # Web 返回键：必须命中 backGuard 的 gh:confirm-leave（Abyss 在 PROTECTED_SCREENS 里），
        # 只弹确认框，不能直接导航回房间页。
        pa.go_back()
        pa.locator('[data-testid="abyss-confirm-modal"]').wait_for(timeout=8000)
        assert '结束本次试潜？' in body(pa)
        pa.get_by_text('取消', exact=True).click()
        pa.wait_for_timeout(400)
        assert pa.locator('[data-testid="abyss-confirm-modal"]').count() == 0
        ok('对局中浏览器返回：弹出确认框并可取消')

        pa.get_by_text('选择强化', exact=False).wait_for(timeout=90000)
        choices = pa.locator('[data-testid^="abyss-choice-"]')
        assert choices.count() >= 3, f'升级候选不足 3 个：{choices.count()}'
        text = body(pa)
        assert '等待队友选择' not in text, '自己的升级候选没有下发'
        choices.first.click()
        pa.get_by_text('选择强化', exact=False).wait_for(state='hidden', timeout=15000)
        ok('升级候选出现并可选择')

        pa.locator('[data-testid="abyss-inventory"]').click()
        pa.locator('[data-testid="abyss-inventory-modal"]').wait_for(timeout=8000)
        text = body(pa)
        assert '装备与属性' in text and '伤害' in text and '冷却' in text, '装备面板缺少属性区'
        assert '武器' in text and '被动' in text, '装备面板缺少武器/被动区'
        pa.locator('[data-testid="abyss-inventory-close"]').click()
        ok('装备面板显示武器 / 被动 / 属性')

        pa.locator('[data-testid="abyss-leave"]').click()
        pa.locator('[data-testid="abyss-confirm-modal"]').wait_for(timeout=8000)
        assert '结束本次试潜？' in body(pa)
        pa.get_by_text('取消', exact=True).click()
        pa.wait_for_timeout(400)
        ok('离开确认弹窗可打开并取消')

        browser.close()
    print(f'=== 通过：{STEP} 步 ===')


if __name__ == '__main__':
    main()