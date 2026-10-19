#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：飞行棋冒烟（建房/加入/开始/掷骰/认输弃权/排名结算）。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_ludo_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'lu_a_{TS}', f'lu_b_{TS}'
STEP = 0

# 骰子描边是「动画是否在转」的可观测信号（起转 colors.primary / 落定 #111827）
DIE_STROKE = "document.querySelector('[data-testid=\"ludo-die\"] rect').getAttribute('stroke')"
DIE_PIPS = "document.querySelectorAll('[data-testid=\"ludo-die\"] circle').length"
ROLLING_STROKE = '#4F46E5'
SETTLED_STROKE = '#111827'


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
    print('=== Web E2E: 飞行棋冒烟 ===')
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

        # A 创建飞行棋房间
        goto_tab(pa, '游戏')
        pa.get_by_text('飞行棋', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建飞行棋房间 {code}')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        pa.get_by_text('掷骰子', exact=True).wait_for(timeout=20000)
        # B 可能先显示「玩家已重连」等瞬时消息（2-3s 自动消失），轮询等待回合提示
        pb.get_by_text('等待', exact=False).first.wait_for(timeout=10000)
        ok('双方进入飞行棋对局页，A(房主)先手掷骰')

        # 标准棋盘：赛道 52 格按红→黄→蓝→绿四色循环（每色 13 格），
        # 每人一条虚线捷径 → 画出来是 4 组「白垫底 + 深墨虚线」的箭头。
        # 用 testID 读格子 fill，别靠截图或颜色启发式。
        fills = [
            pa.evaluate(
                "i => document.querySelector(`[data-testid=\"ludo-cell-${i}\"]`).getAttribute('fill')",
                i,
            )
            for i in range(8)
        ]
        assert len(set(fills[:4])) == 4, f'赛道前 4 格应为 4 种颜色，实际 {fills[:4]}'
        assert fills[:4] == fills[4:8], f'配色循环周期应为 4，实际 {fills}'
        n_arrow = pa.locator('[data-testid^="ludo-arrow-"]').count()
        assert n_arrow == 4, f'虚线捷径应为 4 条，实际 {n_arrow}'
        # 每条箭头是三层描边（深墨垫底 / 奶油白 / 本色虚线）→ 4×3 = 12 条虚线元素
        n_dash = pa.evaluate("document.querySelectorAll('svg [stroke-dasharray]').length")
        assert n_dash == 12, f'虚线箭头应有 12 条虚线元素（4 条 × 3 层描边），实际 {n_dash}'
        # 骰子面常驻控制区（起转 → 落定的动效载体），不是只在轮到自己时才出现
        assert pa.locator('[data-testid="ludo-die"]').count() == 1, '控制区没有骰子面'
        ok('标准棋盘：赛道四色循环 + 4 条虚线捷径 + 常驻骰子面')

        # A 掷骰：按下即起转 → 动画播完才落定到服务端点数（回合推进也要等它）
        pa.get_by_text('掷骰子', exact=True).click()
        assert pa.evaluate(DIE_STROKE) == ROLLING_STROKE, '按下掷骰后骰子未立刻起转'
        t0 = time.time()
        while time.time() - t0 < 3 and pa.evaluate(DIE_STROKE) != SETTLED_STROKE:
            pa.wait_for_timeout(30)
        elapsed = time.time() - t0
        # 最短起转 DICE_MIN_MS = 420ms，留一点余量
        assert elapsed >= 0.38, f'掷骰动画只播了 {elapsed:.2f}s，被跳过了'
        pips = pa.evaluate(DIE_PIPS)
        assert 1 <= pips <= 6, f'落定后骰面点数异常: {pips}'
        # 落定后点数必须**定住**：起转用的 setInterval 若没 clear，骰面会继续每
        # 80ms 跳一次，玩家永远看不到真实点数（曾因「只把 ref 置空、没 clearInterval」
        # 漏了 4 个版本——只断言描边和点数范围是抓不到的）
        pa.wait_for_timeout(600)
        pips2 = pa.evaluate(DIE_PIPS)
        assert pips2 == pips, f'骰子落定后仍在跳动: {pips} → {pips2}'
        body_a = pa.locator('body').inner_text()
        body_b = pb.locator('body').inner_text()
        rolled = ('骰' in body_a) or ('点击骰子掷骰' in body_b) or ('等待' in body_a)
        assert rolled, f'掷骰后无骰子/回合流转迹象: A={body_a[:150]} B={body_b[:150]}'
        ok('掷骰：按下即起转、落定后骰面显示点数，回合状态已广播')

        # A 认输（弃权）：二次确认 → 排名结算，B 第 1 名
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        body_b = pb.locator('body').inner_text()
        assert '#1' in body_b, f'B 未看到排名: {body_b[:200]}'
        ok('A 认输弃权 → B 看到对局结束与排名（B 第1名）')

        # 结算弹窗「返回房间」：房间保留可续开
        pb.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_b = pb.locator('body').inner_text()
        assert code in body_b and '房间不存在' not in body_b, f'返回后房间异常: {body_b[:150]}'
        ok('B 点「返回房间」回到大厅，房间保留（可续开）')

        browser.close()
    print(f'=== 飞行棋冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
