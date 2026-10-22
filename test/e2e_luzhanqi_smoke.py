#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：军棋冒烟。
覆盖：建房（含明棋/暗棋玩法选择）→ 双方进**布阵阶段** → 交换两子/随机/确认布阵
→ 开打 → 两步走子 → 认输终局 → 返回房间；**再开一局暗棋**，验证对手半场只画牌背。

两个重点：
1. **布阵是服务端判定的**：客户端只把「交换哪两枚」发上去，合法性（地雷只能在后两排、
   军旗必须在大本营、炸弹不能在第一排）全在服务端。这里交换两枚同型棋子，保证合法。
2. **暗棋下对手的棋子身份压根不下发**：所以「牌背格子没有 `lz-piece-*` 节点」
   就是这条不变量的可观测量 —— 明棋 50 个、暗棋只有自己那 25 个。

用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_luzhanqi_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB, UC = f'lz_a_{TS}', f'lz_b_{TS}', f'lz_c_{TS}'
STEP = 0
ROWS, COLS = 12, 5


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def open_settings(page):
    """打开「玩法设置」抽屉（v1.15：房间级设置从行内芯片收进了底部抽屉）。"""
    page.locator('[data-testid="room-settings-row"]').click()
    page.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
    page.wait_for_timeout(300)


def close_settings(page):
    page.locator('[data-testid="room-settings-close"]').click()
    page.wait_for_timeout(400)


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)


def create_room(page, other_page=None):
    """A 建房 → 返回房间号。

    先回根：**上次可能停在某个「压在 Tab 之上」的页面**（房间页 / 历史战绩），
    那时底部 Tab 栏根本不可见，`goto_tab` 会一直等不到可点的元素。
    根路径刷新会带着登录态重进主 Tab（登录态是持久化的）。
    """
    page.goto(BASE)
    page.get_by_text('房间', exact=True).last.wait_for(timeout=20000)
    page.wait_for_timeout(500)
    goto_tab(page, '房间')
    page.get_by_text('创建', exact=True).first.click()
    page.get_by_text('军棋', exact=True).last.click()
    page.get_by_text('创建', exact=True).last.click()
    page.get_by_text('房间号', exact=True).wait_for(timeout=15000)
    m = re.search(r'\b[A-Z0-9]{6}\b', page.locator('body').inner_text())
    assert m, '未找到6位房间号'
    return m.group(0)


def join_room(page, code):
    goto_tab(page, '房间')
    page.get_by_text('加入', exact=True).first.click()
    page.get_by_placeholder('输入6位房间号').fill(code)
    page.get_by_text('加入', exact=True).last.click()
    page.get_by_text('房间号', exact=True).wait_for(timeout=15000)
    assert '军棋' in page.locator('body').inner_text(), '房间页未显示「军棋」'


def wait_setup(page, timeout=20000):
    page.locator('[data-testid="lz-confirm"]').wait_for(timeout=timeout)


def backs(page):
    """牌背（暗棋下对手的棋子）数量。"""
    return page.locator('[data-testid^="lz-back-"]').count()


def pieces(page):
    """页面上所有「正面棋子」（能看见名字的）：testID → 文字。牌背用 lz-back-*。"""
    out = {}
    for el in page.locator('[data-testid^="lz-piece-"]').all():
        tid = el.get_attribute('data-testid')
        out[tid] = el.inner_text().strip()
    return out


def click_cell(page, r, c):
    page.locator(f'[data-testid="lz-cell-{r}-{c}"]').click()
    page.wait_for_timeout(220)


def find_move_to_empty(page, rows, cols):
    """找一步「走到空格」的着法（不是吃子），返回 (起点, 落点)。

    刻意挑空格：吃子那一步的结果取决于碰子胜负（攻击方可能自己阵亡、落点反而空掉），
    用它做「棋子有没有移过去」的断言会假红。
    """
    for r in range(rows):
        for c in range(cols):
            if page.locator(f'[data-testid="lz-piece-{r}-{c}"]').count() == 0:
                continue
            click_cell(page, r, c)
            for el in page.locator('[data-testid^="lz-target-"]').all():
                tid = el.get_attribute('data-testid')
                _, tr, tc = tid.rsplit('-', 2)
                tr, tc = int(tr), int(tc)
                occupied = (
                    page.locator(f'[data-testid="lz-piece-{tr}-{tc}"]').count()
                    + page.locator(f'[data-testid="lz-back-{tr}-{tc}"]').count()
                )
                if occupied == 0:
                    return (r, c), (tr, tc)
            click_cell(page, r, c)      # 取消选中
    return None


def main():
    print('=== Web E2E: 军棋冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        for pg in (pa, pb):
            pg.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        # ---- 第一局：明棋 ----
        code = create_room(pa)
        body = pa.locator('body').inner_text()
        assert '玩法' in body and '明棋' in body, '房间页未显示玩法选择'
        ok(f'A 创建军棋房间 {code}，房间页显示玩法选择（默认明棋）')

        # 房主切暗棋再切回明棋 —— 这条广播要给全房，不能只回房主。
        # v1.15 起这些芯片在「玩法设置」抽屉里（房间页只留一行摘要）。
        open_settings(pa)
        pa.locator('[data-testid="room-settings-mode-hidden"]').click()
        pa.wait_for_timeout(500)
        assert '暗棋' in pa.locator('body').inner_text(), '切暗棋后页面未更新'
        # 「阵亡公开身份」是开关，按两枚芯片渲染（服务端下发两个状态的中文名）
        pa.locator('[data-testid="room-settings-reveal-off"]').click()
        pa.wait_for_timeout(400)
        assert '不公开' in pa.locator('body').inner_text(), '身份公开开关未生效'
        pa.locator('[data-testid="room-settings-mode-open"]').click()
        pa.wait_for_timeout(500)
        # 明棋下「阵亡公开身份」整项不该出现（`visible_when` 由客户端求值）——
        # 常显的话它点了界面上什么都不会变，正是本项目最忌讳的「点了没反应」
        assert pa.locator('[data-testid^="room-settings-reveal"]').count() == 0, \
            '明棋下不该还显示「阵亡公开身份」'
        close_settings(pa)
        assert '明棋' in pa.locator('body').inner_text(), '切回明棋失败'
        ok('房主可切换明棋/暗棋与「阵亡是否公开身份」，切换后页面同步')

        join_room(pb, code)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_setup(pa)
        wait_setup(pb)
        ok('双方进入对局页的**布阵阶段**')

        # 布阵阶段：自己半场 25 枚可见，对手半场 25 枚是牌背（没有 lz-piece 节点）
        mine_a = pieces(pa)
        assert len(mine_a) == 25, f'A 布阵阶段应看到自己 25 枚，实际 {len(mine_a)}'
        red_rows = [k for k in mine_a if int(k.rsplit('-', 2)[1]) >= 6]
        assert len(red_rows) == 25, 'A(红) 的棋子应当都在下半场'
        assert backs(pa) == 25, f'对手半场应当是 25 张牌背，实际 {backs(pa)}'
        ok('布阵阶段：自己半场 25 枚可见，对手半场 25 张牌背（明棋同样先扣着）')

        # 交换两枚同型棋子（师长）—— 合法性由服务端判定
        shi = [k for k, v in mine_a.items() if v == '师长']
        assert len(shi) == 2, f'应当有两枚师长，实际 {shi}'
        a1 = shi[0].rsplit('-', 2)[1:]
        a2 = shi[1].rsplit('-', 2)[1:]
        click_cell(pa, int(a1[0]), int(a1[1]))
        click_cell(pa, int(a2[0]), int(a2[1]))
        pa.wait_for_timeout(500)
        after = pieces(pa)
        assert after[shi[0]] == '师长' and after[shi[1]] == '师长', '交换两枚师长后阵型变了'
        assert len(after) == 25, '交换后棋子数变了'
        ok('交换两枚同型棋子成功（交换合法性由服务端判定）')

        pa.locator('[data-testid="lz-random"]').click()
        pa.wait_for_timeout(700)
        assert len(pieces(pa)) == 25, '随机后棋子数不对'
        ok('「随机」重掷阵型，仍是 25 枚')

        # 经典阵型芯片（v1.17）：点一下整套摆好。这里验的是**芯片接线 + 真的生效** ——
        # 双喜临门的第一排是「师兵连排师」，是这套阵最好认的一行。
        pa.locator('[data-testid="lz-preset-shuangxi"]').wait_for(timeout=10000)
        pa.locator('[data-testid="lz-preset-shuangxi"]').click()
        pa.wait_for_timeout(700)
        preset = pieces(pa)
        assert len(preset) == 25, f'套用阵型后棋子数不对：{len(preset)}'
        front = sorted(
            ((int(k.rsplit('-', 2)[1]), int(k.rsplit('-', 2)[2]), v)
             for k, v in preset.items() if int(k.rsplit('-', 2)[1]) == 6),
            key=lambda t: t[1],
        )
        assert [v for _, _, v in front] == ['师长', '工兵', '连长', '排长', '师长'], \
            f'双喜临门的第一排不对：{front}'
        ok('点「双喜临门」芯片 → 整套阵型一次摆好（第一排 师兵连排师）')

        pa.locator('[data-testid="lz-confirm"]').click()
        pa.wait_for_timeout(600)
        assert '已确认' in pa.locator('body').inner_text(), 'A 确认后没有提示'
        assert '对方已确认布阵' in pb.locator('body').inner_text(), 'B 没看到对方已确认'
        ok('A 确认布阵 → 自己进入等待、B 看到「对方已确认布阵」')

        pb.locator('[data-testid="lz-confirm"]').click()
        pa.wait_for_timeout(900)
        ok('B 确认布阵 → 双方进入对局阶段')

        # 明棋：50 枚全部可见
        all_a = pieces(pa)
        assert len(all_a) == 50, f'明棋应当 50 枚全可见，实际 {len(all_a)}'
        assert backs(pa) == 0, '明棋下不该有牌背'
        assert '轮到你走' in pa.locator('body').inner_text(), 'A(红) 未看到自己回合'
        assert '等待对方' in pb.locator('body').inner_text(), 'B 未看到等待提示'
        ok('明棋：50 枚棋子全部可见；红方(A)先手，回合提示正确')

        # A(红) 两步走子
        move = find_move_to_empty(pa, ROWS, COLS)
        assert move, 'A 找不到任何能走到空格的着法'
        (fr, fc), (tr, tc) = move
        click_cell(pa, fr, fc)
        click_cell(pa, tr, tc)
        pa.wait_for_timeout(900)
        after_a = pieces(pa)
        assert f'lz-piece-{tr}-{tc}' in after_a, f'A 走子后 ({tr},{tc}) 没有棋子'
        assert f'lz-piece-{fr}-{fc}' not in after_a, f'A 原位置 ({fr},{fc}) 未清空'
        assert '等待对方' in pa.locator('body').inner_text(), 'A 走完未进入等待'
        assert '轮到你走' in pb.locator('body').inner_text(), 'B 未轮到'
        ok(f'A 两步走子 ({fr},{fc})→({tr},{tc}) 成功，回合移交')

        # B(黑) 翻转视角走子 —— 坐标换算错了这步就发不出去
        move_b = find_move_to_empty(pb, ROWS, COLS)
        assert move_b, 'B 找不到任何能走到空格的着法'
        (frb, fcb), (trb, tcb) = move_b
        click_cell(pb, frb, fcb)
        click_cell(pb, trb, tcb)
        pb.wait_for_timeout(900)
        assert f'lz-piece-{trb}-{tcb}' in pieces(pb), 'B 走子后落点没有棋子'
        assert '轮到你走' in pa.locator('body').inner_text(), 'A 未轮到'
        assert f'lz-piece-{trb}-{tcb}' in pieces(pa), 'A 侧未同步到 B 的走子'
        ok(f'B 翻转视角下落子 ({frb},{fcb})→({trb},{tcb}) 成功，双方局面同步')

        # B 认输 → 红方胜
        pb.locator('[data-testid="lz-surrender"]').click()
        pb.locator('[data-testid="lz-confirm-surrender"]').click()
        pa.get_by_text('红方胜', exact=True).wait_for(timeout=10000)
        assert '对方认输' in pa.locator('body').inner_text(), 'A 未看到认输原因'
        ok('B 认输 → 双方终局弹窗，红方(A)胜')

        pa.locator('[data-testid="lz-back-room"]').click()
        pb.locator('[data-testid="lz-back-room"]').click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_a = pa.locator('body').inner_text()
        assert code in body_a and '军棋' in body_a, 'A 返回后房间页不对'
        ok('双方「返回房间」回到大厅，房间保留可续开')

        # ---- 第二局：暗棋（验证对手半场只画牌背） ----
        open_settings(pa)
        pa.locator('[data-testid="room-settings-mode-hidden"]').click()
        pa.wait_for_timeout(600)
        assert '暗棋' in pa.locator('body').inner_text(), '切暗棋失败'
        close_settings(pa)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_setup(pa)
        pa.locator('[data-testid="lz-confirm"]').click()
        pb.locator('[data-testid="lz-confirm"]').click()
        pa.wait_for_timeout(1200)

        hidden_a = pieces(pa)
        assert len(hidden_a) == 25, f'暗棋下 A 只该看到自己 25 枚，实际 {len(hidden_a)}'
        hidden_b = pieces(pb)
        assert backs(pa) == 25, f'暗棋下对手半场应当全是牌背，实际 {backs(pa)}'
        assert len(hidden_b) == 25, f'暗棋下 B 只该看到自己 25 枚，实际 {len(hidden_b)}'
        assert backs(pb) == 25, f'暗棋下 B 侧对手半场应当全是牌背，实际 {backs(pb)}'
        assert '暗棋' in pa.locator('body').inner_text(), '顶栏未标注暗棋'
        ok('暗棋：双方各自只渲染自己那 25 枚，对手半场是牌背（身份没有下发到线上）')

        # 暗棋下走一步仍然正常
        move_h = find_move_to_empty(pa, ROWS, COLS)
        assert move_h, '暗棋下 A 找不到能走到空格的着法'
        (hfr, hfc), (htr, htc) = move_h
        click_cell(pa, hfr, hfc)
        click_cell(pa, htr, htc)
        pa.wait_for_timeout(900)
        assert '等待对方' in pa.locator('body').inner_text(), '暗棋下 A 走完未移交回合'
        assert '轮到你走' in pb.locator('body').inner_text(), '暗棋下 B 未轮到'
        ok('暗棋下两步走子正常，回合正常移交')

        # 认输收尾
        pb.locator('[data-testid="lz-surrender"]').click()
        pb.locator('[data-testid="lz-confirm-surrender"]').click()
        pa.get_by_text('红方胜', exact=True).wait_for(timeout=10000)
        pa.locator('[data-testid="lz-back-room"]').click()
        pb.locator('[data-testid="lz-back-room"]').click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        ok('暗棋局终局并返回房间')

        # ---- 历史战绩：快照能渲染 ----
        # 先退出房间回到主 Tab（房间页盖在 Tab 之上，不退出去点不到底部 Tab）
        leave = pa.get_by_text('退出房间', exact=True)
        if leave.count():
            leave.first.click()
            pa.wait_for_timeout(1200)
        goto_tab(pa, '我的')
        pa.get_by_text('历史战绩', exact=True).first.click()
        pa.wait_for_timeout(1200)
        assert '军棋' in pa.locator('body').inner_text(), '战绩列表没有军棋记录'
        ok('历史战绩里能看到军棋记录（快照分支已接）')

        # ---- 人机：bot 自动确认布阵 + 轮到它时自己走一步 ----
        code2 = create_room(pa)
        pa.get_by_text('添加人机', exact=True).first.click()
        pa.wait_for_timeout(800)
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_setup(pa)
        assert '对方已确认布阵' in pa.locator('body').inner_text(), \
            '人机应当自动确认布阵（服务端替它确认）'
        ok('人机局：人机自动确认布阵，真人看到「对方已确认布阵」')

        pa.locator('[data-testid="lz-confirm"]').click()
        pa.wait_for_timeout(900)
        assert '轮到你走' in pa.locator('body').inner_text(), '开局后未轮到真人'
        move_bot = find_move_to_empty(pa, ROWS, COLS)
        assert move_bot, '人机局：真人找不到能走到空格的着法'
        (bfr, bfc), (btr, btc) = move_bot
        click_cell(pa, bfr, bfc)
        click_cell(pa, btr, btc)
        # 人机有 0.8~1.2s 的「思考」延迟，等回合转回自己
        pa.get_by_text('轮到你走', exact=True).wait_for(timeout=20000)
        ok('人机局：真人走子后人机应招，回合回到自己')

        pa.locator('[data-testid="lz-surrender"]').click()
        pa.locator('[data-testid="lz-confirm-surrender"]').click()
        pa.wait_for_timeout(1200)

        browser.close()
    print(f'=== 军棋冒烟通过：{STEP} 步 ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())