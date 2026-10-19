#!/usr/bin/env python3
"""
Web E2E：热重启（后端进程重启后对局无缝恢复）。

流程：双人五子棋开局 → A 落子 → **重启后端** → 客户端自动重连 →
B 落子成功且双方都看到 2 枚棋子。

关键点：只断言「棋盘上还有棋子」是不够的——客户端状态在 WS 断开期间不会清空，
旧棋子照样显示。必须验证重启后**还能继续走子**，才能证明服务端真的从磁盘
恢复了房间与对局状态（否则 make_move 会因「房间不存在/游戏未在进行中」失败）。

用法：先启动后端(50541)与前端 web(50540)，然后
  python test/e2e_hot_restart.py
"""
import os
import re
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'hr_a_{TS}', f'hr_b_{TS}'
STEP = 0

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESTART_SH = os.path.join(REPO, 'scripts', 'restart.sh')


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


def restart_backend():
    """重启后端进程并等待端口恢复（restart.sh 内部已做 HTTP 探活）。

    走 scripts/restart.sh：它用「端口 + 工作目录 + 命令行」三重指纹定位进程，
    不会误杀本机上别的项目（novel_reader / quant_view 也跑着 uvicorn）。
    """
    subprocess.run(['bash', RESTART_SH, 'backend'], check=True, timeout=180)


def count_stones(page):
    """数棋盘上的棋子（GomokuScreen 每枚棋子带 testID stone-x-y）。"""
    return page.locator('[data-testid^="stone-"]').count()


def click_cell(page, cells, x, y, settle_ms=250):
    """点一个交叉点。

    五子棋是**二次确认**落子（第一下只放半透明预览子），所以要连点两下；
    settle_ms 是给「预览子渲染 → 第二次点击被判定为同一点」留的间隔。
    """
    top, left, w = cells[y * 15 + x]
    page.mouse.click(left + w / 2, top + w / 2)
    page.wait_for_timeout(settle_ms)
    page.mouse.click(left + w / 2, top + w / 2)


def wait_board(page, timeout=20000):
    """返回棋盘 225 个交叉点 (top, left, w)，按 (y,x) 排序。"""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        cells = square_cells(page)
        if len(cells) >= 225:
            return cells
        page.wait_for_timeout(500)
    raise AssertionError(f'棋盘未出现，仅找到 {len(square_cells(page))} 个方形按钮')


def square_cells(page):
    btns = page.locator('div[tabindex]')
    squares = []
    for i in range(btns.count()):
        try:
            b = btns.nth(i).bounding_box()
        except Exception:
            continue
        if b and abs(b['width'] - b['height']) < 2 and 8 < b['width'] < 80:
            squares.append((round(b['y'], 1), round(b['x'], 1), round(b['width'])))
    if not squares:
        return []
    from collections import Counter
    size, _ = Counter(s[2] for s in squares).most_common(1)[0]
    return sorted(s for s in squares if s[2] == size)


def main():
    print('=== Web E2E: 热重启恢复 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(20000)
        pb.set_default_timeout(20000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        goto_tab(pa, '游戏')
        pa.get_by_text('五子棋', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)

        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        cells_a = wait_board(pa)
        wait_board(pb)
        ok(f'房间 {code} 五子棋开局')

        click_cell(pa, cells_a, 7, 7)
        pa.wait_for_timeout(1200)
        assert count_stones(pb) >= 1, 'B 未同步到 A 的落子'
        ok('A 落子 (7,7)，B 已同步')

        # ── 重启后端 ──
        restart_backend()
        ok('后端进程已重启')

        # 客户端自动重连（指数退避，首次约 1s）。重连完成前发起的走子会被丢弃
        # （对局内动作不排队，防过期操作），所以这里重试点击直到服务端接受
        cells_b = wait_board(pb)
        resumed = False
        deadline = time.time() + 40
        while time.time() < deadline:
            click_cell(pb, cells_b, 8, 8)
            pb.wait_for_timeout(1500)
            if count_stones(pa) >= 2:
                resumed = True
                break
        assert resumed, (
            f'重启后对局未恢复：A 只看到 {count_stones(pa)} 枚棋子（应为 2）'
        )
        ok('重启后对局恢复：B 落子 (8,8) 成功，双方均见 2 枚棋子')

        # 页面不应被踢回大厅
        body_a = pa.locator('body').inner_text()
        assert '轮到你落子' in body_a or '等待对方' in body_a, \
            f'重启后被踢出对局页: {body_a[:200]}'
        ok('重启后仍停留在对局页（未被踢回大厅）')

        browser.close()
    print(f'=== 热重启恢复通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
