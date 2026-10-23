#!/usr/bin/env python3
"""
Web E2E：飞行棋人机冒烟（建房 → 添加人机 → 开局 → 真人掷骰走子 → 人机自动应招）。

断言核心：真人回合结束后，回合能在 10 秒内回到真人手上——这只能由人机
掷骰+走子产生（2 人局里真人之后必定是人机），因此等价于「人机真的动了」。

用法：先启动后端(50541)与前端 web(50540)，然后
  python test/e2e_ludo_bot_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA = f'lb_a_{TS}'
STEP = 0

# 棋子用 testID 定位（ludo-piece-<seat>-<pi>），不用像素坐标——
# 棋盘格子尺寸现在是按可用空间自适应的，写死坐标会随屏幕/布局变化而失效。


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


def body(page):
    return page.locator('body').inner_text()


def click_base_piece(page):
    """点座位 0（红方）的飞机：掷 6 时基地飞机必然是可动的那个。

    4 架都点一遍，点中可动的那架后提示会从「点击飞机移动」变走。
    """
    for pi in range(4):
        loc = page.locator(f'[data-testid="ludo-piece-0-{pi}"]')
        if loc.count() == 0:
            continue
        try:
            loc.first.click(timeout=2000)
        except Exception:
            continue
        page.wait_for_timeout(500)
        if '点击飞机移动' not in body(page):
            return True
    return False


def main():
    print('=== Web E2E: 飞行棋人机冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)

        register(pa, UA)
        ok('注册登录')

        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('飞行棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', body(pa))
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'创建飞行棋房间 {code}')

        # 飞行棋支持人机，且可加多个
        assert '添加人机' in body(pa), '飞行棋房间未显示「添加人机」'
        pa.get_by_text('添加人机', exact=True).click()
        pa.wait_for_timeout(1000)
        text = body(pa)
        assert '人机' in text, '未看到人机徽标'
        assert '已准备' in text, '人机未自动准备'
        ok('添加人机成功，人机自动准备')

        pa.get_by_text('开始游戏', exact=True).click()
        pa.wait_for_timeout(2500)
        assert '点击骰子掷骰' in body(pa), f'未进入飞行棋对局或不是先手: {body(pa)[:200]}'
        ok('开局成功，真人(座位0)先手掷骰')

        # 阶段一：真人掷骰/走子，直到回合交给⼈机（掷 6 会加手，需多轮）
        deadline = time.time() + 30
        handed_over = False
        while time.time() < deadline:
            text = body(pa)
            if '等待' in text and '人机' in text:
                handed_over = True
                break
            if '点击骰子掷骰' in text:
                pa.get_by_text('掷骰子', exact=False).first.click()
                pa.wait_for_timeout(700)
            elif '点击飞机移动' in text:
                assert click_base_piece(pa), '轮到移动却找不到可点的基地飞机'
                pa.wait_for_timeout(700)
            else:
                pa.wait_for_timeout(400)
        assert handed_over, f'真人回合始终未交给⼈机: {body(pa)[:200]}'
        ok('真人回合结束，回合已交给⼈机')

        # 阶段二：人机自动掷骰+走子，应把回合送回真人
        deadline = time.time() + 15
        while time.time() < deadline:
            if '点击骰子掷骰' in body(pa):
                break
            pa.wait_for_timeout(300)
        assert '点击骰子掷骰' in body(pa), f'人机未把回合交回真人: {body(pa)[:200]}'
        ok('人机自动掷骰+走子，回合已回到真人')

        # 认输结算 → 返回房间
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        ok('认输后看到对局结束弹窗')

        pa.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        assert '房间不存在' not in body(pa), '返回后房间被删除'
        ok('返回房间成功，房间保留')

        browser.close()
    print(f'=== 飞行棋人机冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
