#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：俄罗斯方块对战冒烟。

覆盖：建房/加入/开始 → 双方各自的盘面（各玩各的，不串盘）→ 本地预演与
服务端重放一致（左右移动/旋转/硬降后盘面真的变了）→ 重力自动下落 →
实时排行榜 → 认输只结束本人 → 全员结束结算名次 → 返回房间保留。

读盘面靠格子的 `aria-label`（值 = 方块名 / empty / garbage），不要去比颜色：
颜色编译进 class 名，读出来是一堆哈希，断言既脆又难读。

用法：先启动后端(50541)和前端 web(50540)，然后 python test/e2e_tetris_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'tt_a_{TS}', f'tt_b_{TS}'
STEP = 0

COLS, ROWS = 10, 20


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


def read_board(page):
    """读当前盘面：20×10 的标签矩阵（`empty` / `I`..`L` / `garbage`）。"""
    cells = page.locator('[data-testid^="t-c-"]')
    n = cells.count()
    assert n == ROWS * COLS, f'棋盘格子数应为 {ROWS * COLS}，实际 {n}'
    labels = []
    for i in range(n):
        r, c = divmod(i, COLS)
        assert cells.nth(i).get_attribute('data-testid') == f't-c-{r}-{c}', \
            '棋盘格子顺序被打乱（渲染顺序必须等于盘面顺序）'
        labels.append(cells.nth(i).get_attribute('aria-label'))
    return [labels[r * COLS:(r + 1) * COLS] for r in range(ROWS)]


def named_cells(board):
    """所有有色格子（下落中的方块 + 已固定的块）。攻击行灰块不算。

    **注意**：盘面上没法区分「这是正在下落的方块」还是「已固定的块」——两者都是
    方块名。所以断言时要靠位置分开：刚出生的方块在顶部几行，落定的块在底部。
    """
    return [(r, c) for r in range(ROWS) for c in range(COLS)
            if board[r][c] not in ('empty', 'garbage')]


def click(page, testid, wait=280):
    page.locator(f'[data-testid="{testid}"]').click()
    page.wait_for_timeout(wait)


def top_cells(page, rows=6):
    """只读顶部几行里有色格子的坐标（手势用例够用）。

    手势用例要在一秒内读好几次盘面，逐格读 200 个节点会跨过重力 tick；
    而且下落中的方块必然在顶部，读 6 行足够。
    """
    out = []
    for r in range(rows):
        for c in range(COLS):
            label = page.locator(f'[data-testid="t-c-{r}-{c}"]').get_attribute('aria-label')
            if label not in ('empty', 'garbage'):
                out.append((r, c))
    return sorted(out)


def main():
    print('=== Web E2E: 俄罗斯方块对战冒烟 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        ctx_args = {'viewport': {'width': 390, 'height': 844}}
        pa = browser.new_context(**ctx_args).new_page()
        pb = browser.new_context(**ctx_args).new_page()
        pa.set_default_timeout(15000)
        pb.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        # A 建房
        goto_tab(pa, '游戏')
        pa.get_by_text('俄罗斯方块对战', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建俄罗斯方块房间 {code}')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        pa.locator('[data-testid="t-drop"]').wait_for(timeout=20000)
        pb.locator('[data-testid="t-drop"]').wait_for(timeout=20000)
        ok('双方进入对局页，棋盘与控制区渲染')

        # 开局盘面：各自都有 1 个下落中的方块（4 格），还没有已固定的块
        for page, who in ((pa, 'A'), (pb, 'B')):
            b = read_board(page)
            cells = named_cells(b)
            assert len(cells) == 4, f'{who} 开局应只有 1 个方块（4 格）：{cells}'
            assert all(r <= 3 for r, _ in cells), f'{who} 开局方块应在顶部：{cells}'
        ok('双方各自拿到下落中的方块（同种子公平开局）')

        # 左右移动：本地预演即时生效（不用等回包），且服务端重放后位置一致
        b0 = read_board(pa)
        cols0 = sorted(c for _, c in named_cells(b0))
        click(pa, 't-left')
        b1 = read_board(pa)
        cols1 = sorted(c for _, c in named_cells(b1))
        assert len(named_cells(b1)) == 4, '左移后方块格数变了'
        assert cols1[0] < cols0[0] or cols0[0] == 0, \
            f'左移后方块没有左移（{cols0} → {cols1}）'
        click(pa, 't-right')
        cols2 = sorted(c for _, c in named_cells(read_board(pa)))
        assert cols2[0] == cols1[0] + 1 or cols1[0] == COLS - 1, \
            f'右移后方块没有右移（{cols1} → {cols2}）'
        ok('左右移动：本地预演即时生效')

        # 手势（v1.9.1）：单击棋盘 = 旋转，左右拖动 = **实时**移动
        box = pa.locator('[data-testid="tetris-board"]').bounding_box()
        cell_w = box['width'] / COLS
        tap_x, tap_y = box['x'] + box['width'] / 2, box['y'] + box['height'] * 0.08
        before_tap = top_cells(pa)
        pa.mouse.click(tap_x, tap_y)
        pa.wait_for_timeout(320)
        after_tap = top_cells(pa)
        assert before_tap != after_tap, f'单击棋盘应旋转方块（{before_tap} → {after_tap}）'
        ok('单击棋盘旋转方块')

        # 拖动：手指**还没抬起来**时就该已经走格（实时），松手后也不回弹
        drag_x = box['x'] + box['width'] * 0.15
        drag_y = box['y'] + box['height'] * 0.06
        col_before = min(c for _, c in after_tap)
        pa.mouse.move(drag_x, drag_y)
        pa.mouse.down()
        for i in range(1, 7):
            pa.mouse.move(drag_x + i * (cell_w * 0.6), drag_y, steps=2)
            pa.wait_for_timeout(50)
        pa.wait_for_timeout(160)
        mid_drag = top_cells(pa)            # 手指仍按着
        pa.mouse.up()
        pa.wait_for_timeout(260)
        end_drag = top_cells(pa)
        col_mid = min(c for _, c in mid_drag)
        col_end = min(c for _, c in end_drag)
        assert col_mid > col_before, \
            f'拖动过程中方块就该跟着手指走（实时），而不是松手才动：{after_tap} → {mid_drag}'
        assert col_end >= col_mid, f'松手后方块不该回弹：{mid_drag} → {end_drag}'
        ok(f'左右拖动实时移动（未松手即已右移 {col_mid - col_before} 列）')

        # 重力：什么都不点，方块自己往下掉
        before_rows = sorted(r for r, _ in named_cells(read_board(pa)))
        pa.wait_for_timeout(1400)   # 1 级重力 800ms/格，等两拍足够掉至少一格
        after = read_board(pa)
        after_rows = sorted(r for r, _ in named_cells(after))
        if len(named_cells(after)) == 4:
            assert after_rows[0] > before_rows[0], \
                f'方块没有自动下落（{before_rows} → {after_rows}）'
        ok('重力自动下落（客户端按等级节拍推进）')

        # 硬降：方块立刻落到底并锁定，盘面上多出固定块
        click(pa, 't-drop', wait=900)
        b2 = read_board(pa)
        cells2 = named_cells(b2)
        assert len(cells2) == 8, f'硬降后应是「4 格落定 + 4 格新方块」：{cells2}'
        # 服务端重放后必须回一份**新方块**（每块一次往返），所以顶部还有 4 格
        assert len([1 for r, _ in cells2 if r <= 5]) == 4, f'锁定后没有出下一块：{cells2}'
        assert len([1 for r, _ in cells2 if r >= ROWS - 4]) == 4, f'硬降的块没落到底部：{cells2}'
        ok('硬降：服务端重放并回权威盘面 + 下一块')

        # 暂存（v1.18.1）：点一下立刻换块（不锁定），每块限一次，回包前冻结输入
        click(pa, 't-hold', wait=800)
        hold_box = pa.locator('[data-testid="t-hold"]')
        assert '已用' in hold_box.inner_text(), '暂存后入口应标「已用」'
        held_piece = hold_box.get_attribute('aria-label') or ''
        assert '（空）' not in held_piece, f'暂存框应显示存下的方块：{held_piece}'
        after_hold = top_cells(pa)
        assert after_hold, '暂存后应立刻出现换出来的新方块'
        # 不锁定：底部落定的那 4 格还在（换块不消行、不落子）
        assert len(named_cells(read_board(pa))) >= 8, '暂存不应吞掉盘面'
        click(pa, 't-hold', wait=350)
        assert '已经暂存过' in pa.locator('body').inner_text(), \
            '同一块再点暂存要有提示（不能点了没反应）'
        ok('暂存：即时换块、每块限一次、用过的点击有提示')

        # B 的盘面没有被 A 的操作影响（各玩各的）。
        # 断言「底部 4 行没有落定的块」而不是「B 恰好 4 格」：B 的方块一直在
        # 自己下落，逐格读 200 个 DOM 节点的过程中会跨过一两次重力 tick，读到的
        # 是新旧混合的撕裂态（格数会对不上）。A 落定的那 4 格如果在 B 盘上出现，
        # 一定是压在底部——查底部既准又不受撕裂影响。
        bb = read_board(pb)
        leaked = [(r, c, bb[r][c]) for r in range(ROWS - 4, ROWS)
                  for c in range(COLS) if bb[r][c] != 'empty']
        assert not leaked, f'B 的盘面出现了落定的块（应各玩各的）：{leaked}'
        ok('A 落子不影响 B 的盘面（各玩各的）')

        # 实时排行榜：默认收起，点胶囊展开；两个玩家都要在榜上
        pa.get_by_label('排行榜').click()
        pa.wait_for_timeout(400)
        lb = pa.locator('body').inner_text()
        assert '实时排行' in lb, f'排行榜浮层没展开：{lb[:160]}'
        assert UA in lb and UB in lb, f'排行榜应列出双方用户名：{lb[:200]}'
        ok('实时排行榜可见（右上角胶囊展开，双方都在榜上）')

        # A 认输 → 只结束 A 自己，B 继续
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('你已结束', exact=False).wait_for(timeout=8000)
        assert '本局结束' not in pb.locator('body').inner_text(), \
            'A 认输不应终结整局（各玩各的盘）'
        # **出局提示必须真的在视口里**（v1.9.1 回归）：它曾经挂在页面最底下，
        # 20 行的棋盘把整屏撑满，手机视口里它被裁到屏幕外（实测 390×844 上按钮
        # bottom=850 > 844、360×640 上 top=657 > 640，页面没有滚动容器），于是
        # 出局 = 「盘面冻住、方块不再出现、按键全无反应」且没有任何解释。
        back = pa.locator('[data-testid="t-back-room"]').bounding_box()
        vh = pa.evaluate('() => window.innerHeight')
        assert back and back['y'] + back['height'] <= vh, \
            f'出局后的「返回房间」被挤出视口（{back} vs 视口高 {vh}）'
        assert '你已结束' in pa.locator('[data-testid="t-out-overlay"]').inner_text(), \
            '出局浮层没盖在盘面上（玩家会以为卡住了）'
        ok('A 认输只结束自己，B 的盘继续（且出局提示在视口内）')

        # B 也认输 → 全员结束 → 结算名次
        pb.get_by_text('认输', exact=True).click()
        pb.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pb.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('本局结束', exact=False).wait_for(timeout=10000)
        settle = pb.locator('body').inner_text()
        assert '本局结束' in settle, f'结算标题不对：{settle[:200]}'
        assert '分' in settle and '行' in settle, f'结算应给出分数与行数：{settle[:200]}'
        ok('全员结束 → 双方看到结算（分数 + 行数 + 名次）')

        # 返回房间：房间保留可续开
        pb.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_b = pb.locator('body').inner_text()
        assert code in body_b and '房间不存在' not in body_b, f'返回后房间异常：{body_b[:150]}'
        # 终局要复位 ready：打完一局返回房间应回到「未准备」。
        # **必须等**——房间页是驻留的，重新聚焦时先渲染上一次的快照、随后才
        # fetchRoom 拿到权威数据，立刻读会读到「已准备」的旧值。
        pb.get_by_text('准 备', exact=True).wait_for(timeout=8000)
        ok('返回房间保留（可续开），且 ready 已复位')

        # 战绩落库 + 战绩页快照渲染（新游戏最容易漏接的一环：漏了会显示成「五子棋」）
        # 房间页盖住主 Tab，先退出房间再进「我的」
        pb.get_by_text('退出房间', exact=True).first.click()
        pb.wait_for_timeout(1200)
        goto_tab(pb, '我的')
        pb.get_by_text('历史战绩', exact=True).first.click()
        pb.wait_for_timeout(1500)
        hist = pb.locator('body').inner_text()
        assert '俄罗斯方块对战' in hist, f'历史列表未见俄罗斯方块记录：{hist[:250]}'
        ok('历史战绩出现俄罗斯方块记录')

        # 点开第一条记录，展开终局盘面快照。
        # **要挑可见的那一个**：Tab 页是常驻挂载的，GamesTab 的「俄罗斯方块对战」
        # 卡片标题同样在 DOM 里（隐藏），`.first` 会点到它上面去（"not visible"）。
        cands = pb.get_by_text('俄罗斯方块对战', exact=False)
        target = next((cands.nth(i) for i in range(cands.count())
                       if cands.nth(i).is_visible()), None)
        assert target is not None, '历史列表里找不到可见的俄罗斯方块记录'
        target.click()
        pb.wait_for_timeout(1200)
        detail = pb.locator('body').inner_text()
        assert '最终排名' in detail, f'快照未渲染排名：{detail[:250]}'
        assert '分' in detail and '行' in detail, f'快照未渲染分数/行数：{detail[:250]}'
        ok('战绩详情渲染终局盘面与排名')

        browser.close()
    print(f'=== 俄罗斯方块对战冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())