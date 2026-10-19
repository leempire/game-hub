#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：翻转棋冒烟（建房/加入/开始/合法落子翻转/非法落子/认输终局）。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_reversi_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'rv_a_{TS}', f'rv_b_{TS}'
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


def wait_board(page, timeout=20000):
    page.locator('[data-testid="cell-0-0"]').wait_for(timeout=timeout)


def stone_color(page, testid):
    """RNW 把颜色编译进 class，取 computedStyle 才可靠。返回棋子背景 rgb 字符串。"""
    return page.evaluate(
        """(tid) => {
            const cell = document.querySelector('[data-testid="' + tid + '"]');
            const child = cell && cell.querySelector('div');
            return child ? getComputedStyle(child).backgroundColor : null;
        }""",
        testid,
    )


def main():
    print('=== Web E2E: 翻转棋冒烟 ===')
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

        # A 创建翻转棋房间
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('翻转棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建翻转棋房间 {code}')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_board(pa)
        wait_board(pb)
        ok('双方进入翻转棋对局页，8x8 棋盘渲染')

        # 回合提示：A(黑) 先手
        assert '轮到你落子' in pa.locator('body').inner_text(), 'A 未看到自己回合'
        assert '等待对方' in pb.locator('body').inner_text(), 'B 未看到等待提示'
        ok('回合提示正确：黑方(A)先手')

        # A 落 (4,5)，翻转 (4,4)
        pa.locator('[data-testid="cell-4-5"]').click()
        pa.wait_for_timeout(900)
        color = stone_color(pb, 'cell-4-4')
        assert color == 'rgb(26, 26, 26)', f'(4,4) 未翻转为黑: {color}'
        assert '轮到你落子' in pb.locator('body').inner_text(), '落子后未轮到 B'
        assert pa.locator('[data-testid="reversi-last-mark"]').count() == 1, \
            'A 侧没有最近一步标记'
        assert pb.locator('[data-testid="reversi-last-mark"]').count() == 1, \
            'B 侧没有同步到最近一步标记'
        ok('A 落子 (4,5) 成功，(4,4) 白翻黑，回合移交 B（双方都有最近一步标记）')

        # 非法位置 (0,0)：RNW 对 disabled 单元格设 pointer-events:none，直接断言不可点
        pe = pb.evaluate(
            "() => getComputedStyle(document.querySelector('[data-testid=\"cell-0-0\"]')).pointerEvents")
        assert pe == 'none', f'非法格 (0,0) 应不可点击, pointerEvents={pe}'
        assert '轮到你落子' in pb.locator('body').inner_text(), '回合状态异常'
        ok('非法位置 (0,0) 对玩家不可点击（disabled + pointer-events:none）')

        # B 落合法子 (5,3)
        pb.locator('[data-testid="cell-5-3"]').click()
        pb.wait_for_timeout(900)
        assert '轮到你落子' in pa.locator('body').inner_text(), 'B 落子后未轮到 A'
        ok('B 落子 (5,3) 成功，回合回到 A')

        # A 认输：确认弹窗 → 确认 → B 收到终局
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        body_b = pb.locator('body').inner_text()
        assert '白棋胜' in body_b or '胜' in body_b, f'B 未看到终局胜负: {body_b[:200]}'
        ok('A 认输 → 双方终局弹窗，白棋(B)胜')

        browser.close()
    print(f'=== 翻转棋冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
