#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：重力四子棋冒烟。
覆盖：建房/加入/开始 → 42 格棋盘渲染 → 按列落子与**重力落点行** →
提示标（「堵」/「连四」）→ 四连终局 → 返回房间 → 终局复位 ready → 续开复位棋盘。

重点两步：
1. **重力落点行**：客户端只发列号，落点行是服务端算的。三步都落在同一列，
   必须依次停在 row 5 / 4 / 3——客户端自己推落点就会错位。
2. **提示标**：对手三连时必须出现「堵」，自己能连四时必须出现「连四」——
   这是颜色之外的第二通道（颜色单独承载信息不符合 WCAG 1.4.1）。

用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_connect4_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'c4_a_{TS}', f'c4_b_{TS}'
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
    page.locator('[data-testid="c4-board"]').wait_for(timeout=timeout)


def disc_at(page, r, c):
    """逻辑格 (r,c) 上是黑/白棋子还是空。返回 'black' / 'white' / None。"""
    loc = page.locator(f'[data-testid="c4-disc-{r}-{c}"]')
    if loc.count() == 0:
        return None
    # 棋子盘沿色是固定的深墨，靠它区分黑盘/白盘要靠填充色 —— 读 computedStyle
    fill = loc.first.locator('div').first.evaluate(
        "el => getComputedStyle(el).backgroundColor"
    )
    return 'white' if fill in ('rgb(255, 255, 255)',) else 'black'


def drop(page, col):
    """点整列热区落子。"""
    page.locator(f'[data-testid="c4-col-{col}"]').click()
    page.wait_for_timeout(700)


def hint_chips(page):
    """当前看到的提示标文字集合。"""
    body = page.locator('body').inner_text()
    out = set()
    if '连四' in body:
        out.add('连四')
    # 「堵」单独一个汉字，容易和别的文案撞，用 chip 精确取
    chips = page.locator('text=堵')
    if chips.count() > 0:
        out.add('堵')
    return out


def main():
    print('=== Web E2E: 重力四子棋冒烟 ===')
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

        # A 创建重力四子棋房间
        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('重力四子棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建重力四子棋房间 {code}')

        # B 加入并准备，A 开始
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        assert '重力四子棋' in pb.locator('body').inner_text(), \
            '房间页未显示「重力四子棋」（漏接映射会显示成「五子棋」）'
        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_board(pa)
        wait_board(pb)
        ok('双方进入对局页，房间页游戏类型显示正确')

        # 棋盘：7 列 × 6 行 = 42 格，初始无子
        n_cells = pa.locator('[data-testid^="c4-cell-"]').count()
        assert n_cells == 42, f'应有 42 格，实际 {n_cells}'
        n_discs = pa.locator('[data-testid^="c4-disc-"]').count()
        assert n_discs == 0, f'开局不该有棋子，实际 {n_discs}'
        ok('棋盘渲染 42 格，开局无棋子')

        # 回合：房主执黑先行
        assert '轮到你落子' in pa.locator('body').inner_text(), 'A(黑) 未看到自己回合'
        assert '等待对方' in pb.locator('body').inner_text(), 'B(白) 未看到等待提示'
        ok('回合提示正确：黑方(A)先手')

        # 重力：同一列连落三手，落点行必须依次是 5 / 4 / 3
        drop(pa, 3)
        assert disc_at(pa, 5, 3) == 'black', f'黑子没落在列底: {disc_at(pa, 5, 3)}'
        assert disc_at(pb, 5, 3) == 'black', 'B 未同步到 A 的落子'
        assert '轮到你落子' in pb.locator('body').inner_text(), 'B 未轮到'
        ok('A 落第 3 列 → 停在最底行 (5,3)，B 侧同步一致')

        drop(pb, 3)
        assert disc_at(pb, 4, 3) == 'white', f'白子没堆在黑子上方: {disc_at(pb, 4, 3)}'
        assert disc_at(pa, 4, 3) == 'white', 'A 未同步到 B 的落子'
        ok('B 落同一列 → 堆到 (4,3)（重力生效）')

        drop(pa, 3)
        assert disc_at(pa, 3, 3) == 'black', f'第三颗没堆到 (3,3): {disc_at(pa, 3, 3)}'
        ok('A 再落同一列 → 堆到 (3,3)，落点行完全由服务端决定')

        # 提示标：A 在第 0 列叠出**竖三连**（B 走的三手特意分散在不同列，
# 不给自己造威胁，这样才能单独验证「堵」提示）
        drop(pb, 6)      # (5,6) 白
        drop(pa, 0)      # (5,0) 黑
        drop(pb, 5)      # (5,5) 白
        drop(pa, 0)      # (4,0) 黑
        drop(pb, 4)      # (5,4) 白 —— 白 row5 是 4/5/6 三连，但 (5,3) 是黑子，
                         # 两头都接不上第四子，所以白没有威胁
        drop(pa, 0)      # (3,0) 黑 → 黑第 0 列竖三连，威胁 (2,0)
        chips_b = hint_chips(pb)
        assert '堵' in chips_b, f'对手三连时 B 应看到「堵」提示，实际 {chips_b}'
        assert '连四' not in chips_b, \
            f'B 自己没有连四的机会，不该出现「连四」提示: {chips_b}'
        ok('对手竖三连 → B 侧出现「堵」提示（且没有误报「连四」）')

        # B 不堵，A 应看到「连四」并直接连成四子
        drop(pb, 2)      # (5,2) 白：白 row5 成了 2/4/5/6，(5,3) 是黑子隔开，仍非四连
        chips_a = hint_chips(pa)
        assert '连四' in chips_a, f'自己能连四时应看到「连四」提示，实际 {chips_a}'
        ok('自己能连四 → A 侧出现「连四」提示')

        drop(pa, 0)      # (2,0) 黑 → 第 0 列竖四连
        # 等一下落子动画（DROP_MS 160ms）再断言
        pa.wait_for_timeout(500)
        assert disc_at(pa, 2, 0) == 'black', f'(2,0) 应为黑子: {disc_at(pa, 2, 0)}'
        pa.get_by_text('黑方胜', exact=True).wait_for(timeout=10000)
        body_a = pa.locator('body').inner_text()
        assert '连成四子' in body_a, f'A 未看到获胜原因: {body_a[:200]}'
        pb.get_by_text('黑方胜', exact=True).wait_for(timeout=10000)
        ok('A 竖四连 → 双方终局弹窗「黑方胜 · 连成四子」')

        # 「返回房间」必须真的回到房间页：终局后房间落回等待态，
        # 否则房间页 sync 到 status=playing 会立刻把人弹回对局页
        pa.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_a = pa.locator('body').inner_text()
        assert code in body_a, f'A 返回后未显示房间号 {code}'
        assert '重力四子棋' in body_a, 'A 返回后房间页游戏类型不对'
        ok('双方「返回房间」回到房间大厅，房间保留可续开')

        # 终局把真人非房主复位为未准备：B 回到房间页应看到「准 备」而不是「已准备」
        body_b = pb.locator('body').inner_text()
        assert '准 备' in body_b or '取消准备' not in body_b, \
            f'终局后 B 应回到未准备状态: {body_b[:200]}'
        pb.get_by_text('准 备', exact=True).click()
        ok('终局复位 ready：B 需重新点准备')

        # 续开：棋盘必须复位
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        wait_board(pa)
        pa.wait_for_timeout(600)
        n_discs = pa.locator('[data-testid^="c4-disc-"]').count()
        assert n_discs == 0, f'新一局棋盘没复位，还有 {n_discs} 枚棋子'
        assert '轮到你落子' in pa.locator('body').inner_text(), '新一局黑方未先手'
        ok('续开一局：棋盘复位、黑方先手')

        # 满列提示：把第 0 列填满（6 颗）后点它应弹应用内提示（不是浏览器 alert）
        for _ in range(3):
            drop(pa, 0)
            drop(pb, 0)
        assert disc_at(pa, 0, 0) == 'white', '第 0 列没填满'
        pa.locator('[data-testid="c4-col-0"]').click()
        pa.wait_for_timeout(500)
        body_a = pa.locator('body').inner_text()
        assert '放不下了' in body_a, f'满列点击应给出应用内提示: {body_a[:200]}'
        ok('满列点击给出应用内提示（放不下了）')

        # 人机：房间页要有「添加人机」（supportsBots 漏接就看不见这个按钮），
        # 开局后轮到人机时文案要是「XX 思考中…」而不是干等
        pb.get_by_text('退出', exact=True).click()
        pb.get_by_text('确认退出', exact=True).click()
        pa.get_by_text('退出', exact=True).click()
        pa.get_by_text('确认退出', exact=True).click()
        pa.wait_for_timeout(1200)
        pa.get_by_text('退出房间', exact=True).click()
        pa.wait_for_timeout(1200)
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('重力四子棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pa.get_by_text('添加人机', exact=True).wait_for(timeout=10000)
        pa.get_by_text('添加人机', exact=True).click()
        pa.wait_for_timeout(600)
        assert '人机' in pa.locator('body').inner_text(), '人机徽标未出现'
        pa.get_by_text('开始游戏', exact=True).click()
        wait_board(pa)
        pa.wait_for_timeout(600)
        ok('重力四子棋房支持添加人机并开局')

        # A 落子后轮到人机：文案应为「思考中」，随后人机自己落一子
        drop(pa, 3)
        pa.wait_for_timeout(300)
        assert '思考中' in pa.locator('body').inner_text(), \
            f'人机回合应显示「思考中…」: {pa.locator("body").inner_text()[:200]}'
        # 人机思考 0.8~1.2s + 0.5s 搜索
        pa.wait_for_timeout(2500)
        n_discs = pa.locator('[data-testid^="c4-disc-"]').count()
        assert n_discs == 2, f'人机没有应招（棋盘上应有 2 枚棋子，实际 {n_discs}）'
        assert '轮到你落子' in pa.locator('body').inner_text(), '人机走完回合没交回来'
        ok('人机应招并交回回合')

        browser.close()
    print(f'=== 重力四子棋冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'\n!!! 失败于第 {STEP + 1} 步：{e}')
        raise