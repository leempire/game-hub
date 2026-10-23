#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：中国象棋冒烟。
覆盖：建房/加入/开始 → 棋盘与棋子渲染 → 红方两步落子 → 黑方（**翻转视角**）落子
→ 认输终局。

翻转视角那一步是重点：黑方看到的棋盘转了 180°，如果「显示坐标 ↔ 逻辑坐标」的
换算出错，黑方点自己想要的格子会打到别处，服务端就会回「这步棋不合规则」。

用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_xiangqi_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'xq_a_{TS}', f'xq_b_{TS}'
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
    page.locator('[data-testid="xq-0-0"]').wait_for(timeout=timeout)


def piece_at(page, r, c):
    """读逻辑坐标 (r,c) 上的棋子汉字；该格无子返回 None。"""
    loc = page.locator(f'[data-testid="xq-piece-{r}-{c}"]')
    if loc.count() == 0:
        return None
    text = loc.first.inner_text().strip()
    return text or None


def play(page, fr, fc, tr, tc):
    """两步落子：先点起点，再点落点。"""
    page.locator(f'[data-testid="xq-{fr}-{fc}"]').click()
    page.wait_for_timeout(250)
    page.locator(f'[data-testid="xq-{tr}-{tc}"]').click()
    page.wait_for_timeout(900)


def main():
    print('=== Web E2E: 中国象棋冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)
        pb.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        # A 创建中国象棋房间
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('中国象棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建中国象棋房间 {code}')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        # 房间页游戏类型不能显示成「五子棋」
        assert '中国象棋' in pb.locator('body').inner_text(), '房间页未显示「中国象棋」'
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_board(pa)
        wait_board(pb)
        ok('双方进入象棋对局页，房间类型显示正确')

        # 棋盘：32 枚棋子、初始布局正确
        n_pieces = pa.locator('[data-testid^="xq-piece-"]').count()
        assert n_pieces == 32, f'初始应有 32 枚棋子，实际 {n_pieces}'
        assert piece_at(pa, 9, 4) == '帅', f'红帅位置不对: {piece_at(pa, 9, 4)}'
        assert piece_at(pa, 0, 4) == '将', f'黑将位置不对: {piece_at(pa, 0, 4)}'
        assert piece_at(pa, 9, 0) == '车'
        assert piece_at(pa, 6, 0) == '兵'
        assert piece_at(pa, 3, 0) == '卒'
        assert piece_at(pa, 4, 4) is None, '(4,4) 应为空'
        ok('棋盘渲染 32 枚棋子，红帅黑将/车/兵卒汉字与红黑叫法正确')

        # 回合：房主执红先行
        assert '轮到你走' in pa.locator('body').inner_text(), 'A(红) 未看到自己回合'
        assert '等待对方' in pb.locator('body').inner_text(), 'B(黑) 未看到等待提示'
        ok('回合提示正确：红方(A)先手')

        # A(红) 炮二进…：(7,1) → (4,1)
        play(pa, 7, 1, 4, 1)
        assert piece_at(pa, 4, 1) == '炮', f'A 走子后 (4,1) 不是炮: {piece_at(pa, 4, 1)}'
        assert piece_at(pa, 7, 1) is None, 'A 的原位置 (7,1) 未清空'
        assert '等待对方' in pa.locator('body').inner_text(), 'A 走完未进入等待'
        assert '轮到你走' in pb.locator('body').inner_text(), 'B 未轮到'
        # 最近一步高亮：棋子右上角的小方块，双方都要有（且只有落点那一个）
        assert pa.locator('[data-testid="xq-last-mark"]').count() == 1, \
            'A 侧没有最近一步标记'
        assert pb.locator('[data-testid="xq-last-mark"]').count() == 1, \
            'B 侧没有同步到最近一步标记'
        ok('A 两步落子 (7,1)→(4,1) 成功，回合移交 B（双方都有最近一步标记）')

        # B 看到同一局面（testID 用逻辑坐标，两侧一致）
        assert piece_at(pb, 4, 1) == '炮', f'B 未同步到 A 的走子: {piece_at(pb, 4, 1)}'
        assert piece_at(pb, 7, 1) is None, 'B 侧 (7,1) 未清空'
        ok('B 侧局面同步一致')

        # B(黑) 走子——**此时棋盘是翻转视角**，坐标换算错了这步就发不出去
        play(pb, 2, 7, 5, 7)
        assert piece_at(pb, 5, 7) == '炮', f'B 走子后 (5,7) 不是炮: {piece_at(pb, 5, 7)}'
        assert piece_at(pb, 2, 7) is None, 'B 的原位置 (2,7) 未清空'
        assert '轮到你走' in pa.locator('body').inner_text(), 'A 未轮到'
        assert piece_at(pa, 5, 7) == '炮', 'A 侧未同步到 B 的走子'
        ok('B 翻转视角下落子 (2,7)→(5,7) 成功，坐标换算正确')

        # B 认输 → 红方胜
        pb.get_by_text('认输', exact=True).click()
        pb.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pb.get_by_text('确认认输', exact=True).click()
        # 等弹窗里的「红方胜」——顶栏也有「对局结束」，拿那个等会撞 strict mode
        pa.get_by_text('红方胜', exact=True).wait_for(timeout=10000)
        body_a = pa.locator('body').inner_text()
        assert '对方认输' in body_a, f'A 未看到认输原因: {body_a[:200]}'
        ok('B 认输 → 双方终局弹窗，红方(A)胜')

        # 「返回房间」必须真的回到房间页：终局后房间要落回等待态，
        # 否则房间页 sync 到 status=playing 会立刻把人弹回对局页（表现为点了没反应）
        pa.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_a = pa.locator('body').inner_text()
        assert code in body_a, f'A 返回后未显示房间号 {code}'
        assert '中国象棋' in body_a, 'A 返回后房间页游戏类型不对'
        ok('双方「返回房间」回到房间大厅，房间保留可续开')

        browser.close()
    print(f'=== 中国象棋冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
