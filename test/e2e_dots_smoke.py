#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：点格棋冒烟（建房/加入/开始/画边封格/认输终局）。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_dots_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'dt_a_{TS}', f'dt_b_{TS}'
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
    page.locator('[data-testid="h-0-0"]').wait_for(timeout=timeout)


def main():
    print('=== Web E2E: 点格棋冒烟 ===')
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

        # A 创建点格棋房间
        goto_tab(pa, '游戏')
        pa.get_by_text('点格棋', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建点格棋房间 {code}')

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
        ok('双方进入点格棋对局页，棋盘渲染')

        # 回合提示：A(蓝方) 先手
        assert '轮到你画边' in pa.locator('body').inner_text(), 'A 未看到自己回合'
        assert '等待对方' in pb.locator('body').inner_text(), 'B 未看到等待提示'
        ok('回合提示正确：蓝方(A)先手')

        # A 画边 h-0-0
        pa.locator('[data-testid="h-0-0"]').click()
        pa.wait_for_timeout(900)
        assert '轮到你画边' in pb.locator('body').inner_text(), '画边后未轮到 B'
        # 最近一步高亮：必须标在刚画的 h-0-0 上，且对手侧也同步
        assert pa.locator('[data-testid="dots-last-h-0-0"]').count() == 1, \
            'A 侧没有最近一步高亮'
        assert pb.locator('[data-testid="dots-last-h-0-0"]').count() == 1, \
            'B 侧没有同步到最近一步高亮'
        ok('A 画边 h-0-0 成功，回合移交 B（双方都有最近一步高亮）')

        # B 画边 h-0-1
        pb.locator('[data-testid="h-0-1"]').click()
        pb.wait_for_timeout(900)
        assert '轮到你画边' in pa.locator('body').inner_text(), 'B 画边后未轮到 A'
        # 高亮要跟着走：旧的消失、新的出现（不能两个都留着）
        assert pa.locator('[data-testid="dots-last-h-0-0"]').count() == 0, \
            '上一步的高亮没清掉'
        assert pa.locator('[data-testid="dots-last-h-0-1"]').count() == 1, \
            '高亮没跟到新画的边上'
        ok('B 画边 h-0-1 成功，回合回到 A（高亮跟着移动）')

        # —— 封格提示 ——
        # 点格棋真正的难点是「连画」：封格后立刻再走一次。16 个格子光看数字
        # 读不出哪一步会连锁，所以把「哪条边能封格」直接画在棋盘上。
        drawn = [('h', 0, 0), ('h', 0, 1)]
        # 把 (0,0) 格凑到 3 条边：A 补左边、B 补下边 → 轮到 A 时右边 v-0-1 就是封格边
        pa.locator('[data-testid="v-0-0"]').click()
        pa.wait_for_timeout(900)
        assert '轮到你画边' in pb.locator('body').inner_text(), '画边后未轮到 B'
        pb.locator('[data-testid="h-1-0"]').click()
        pb.wait_for_timeout(900)
        assert '轮到你画边' in pa.locator('body').inner_text(), '画边后未轮到 A'
        drawn += [('v', 0, 0), ('h', 1, 0)]
        ok('A/B 各补一条边，(0,0) 格凑到 3 条边')

        HINT = '[data-testid^="dots-hint-"]'
        assert pa.locator(HINT).count() == 1, \
            f'A 回合应恰好 1 条封格提示边，实际 {pa.locator(HINT).count()}'
        assert pb.locator(HINT).count() == 0, '非自己回合不该显示封格提示'
        assert pa.locator('[data-testid="dots-hint-v-0-1"]').count() == 1, \
            '封格提示没落在 (0,0) 格缺的那条边 v-0-1 上'
        ok('封格提示：只在自己回合出现，且指向唯一能封格的那条边')

        # 顺着提示画 → 封格 + 连画（回合不切换），提示随之消失
        pa.locator('[data-testid="v-0-1"]').click()
        pa.wait_for_timeout(900)
        assert '轮到你画边' in pa.locator('body').inner_text(), '封格后没有连画'
        assert pa.locator(HINT).count() == 0, '封格后提示边没有消失'
        drawn.append(('v', 0, 1))
        ok('按提示封格成功 → 连画，提示边消失')

        # 画完剩余所有边，走「自然终局」而不是认输。
        # 回归：终局未把房间置 finished 时，返回房间会被 sync 弹回对局页
        rows = cols = 5 - 1
        remaining = [('h', y, x) for y in range(rows + 1) for x in range(cols)]
        remaining += [('v', y, x) for y in range(rows) for x in range(cols + 1)]
        for edge in drawn:
            remaining.remove(edge)
        for orient, y, x in remaining:
            # 封格会加手，每步都重新判断轮到谁
            page = pa if '轮到你画边' in pa.locator('body').inner_text() else pb
            page.locator(f'[data-testid="{orient}-{y}-{x}"]').click()
            page.wait_for_timeout(220)
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=15000)
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=15000)
        body_a = pa.locator('body').inner_text()
        assert '胜' in body_a or '平局' in body_a, f'A 未看到终局胜负: {body_a[:200]}'
        ok('画完全部 40 条边 → 自然终局，双方弹出结算')

        # 结算弹窗有"返回房间"按钮
        pb.get_by_text('返回房间', exact=True).wait_for(timeout=5000)
        ok('结算弹窗有「返回房间」按钮（无再来一局）')

        # 回归：点「返回房间」必须真的回到房间页并停住
        pb.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=10000)
        pb.wait_for_timeout(2500)  # 留出 sync 往返时间，若被弹回则此处会重新出现结算弹窗
        body_after = pb.locator('body').inner_text()
        assert '对局结束' not in body_after, '点返回房间后又被弹回对局结束页'
        assert '房间号' in body_after, '未停留在房间页'
        ok('点「返回房间」稳定回到房间页（未被弹回对局页）')

        browser.close()
    print(f'=== 点格棋冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
