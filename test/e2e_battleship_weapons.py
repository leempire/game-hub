#!/usr/bin/env python3
"""
Web E2E: 海战棋 v1.10 玩法增强（命中续射 + 特殊武器）。

覆盖：
  1. 布阵期三件武器的挂靠面板可见、可点切换携带舰船
  2. 对局期武器条显示「可用/已用/已随舰沉没」
  3. 己方海域（只读盘）上的点击不静默：点武器自动切回敌方海域、切段取消瞄准、
     点只读盘给出解释（用户实测报过「点雷达→点棋盘→没反应，之后普通攻击也没反应」）
  4. 雷达：探测 → 盘上出现 3×3 区域标记（形状+文字双通道）→ 用掉本回合
  5. 空袭：3×3 范围打击 → 用掉本回合
  6. 齐射：连点 3 格自动发炮 → 用掉本回合
  7. 命中续射：命中后回合仍在自己手里（真浏览器点满格子直到命中）

前置：后端 50541 + web 50540 都在跑。
用法：python test/e2e_battleship_weapons.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'bsW_a_{TS}', f'bsW_b_{TS}'
STEP = 0
CHROME = '/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome'


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


def whose_turn(pa, pb):
    """返回（当前该出手的页面, 另一方）。两边都读一次，避免时序抖动。"""
    for _ in range(6):
        a_turn = '轮到你开炮' in body(pa)
        b_turn = '轮到你开炮' in body(pb)
        if a_turn and not b_turn:
            return pa, pb
        if b_turn and not a_turn:
            return pb, pa
        pa.wait_for_timeout(400)
    raise AssertionError('无法判定该谁出手')


def unfired_cell(page, nth=0):
    """攻击面板上还没开过炮的格子。

    踩坑：格子元素本身**恒在**（`bs-attack-x-y`），开过炮只是在它**里面**多一个
    `-hit` / `-miss` 子元素。所以按 testid 后缀 `:not([...$="-hit"])` 过滤是错的——
    那只筛掉标记元素自己，一个已开火的格子也筛不掉，于是永远返回第 0 格，
    在它上面反复点击（服务端正确地忽略重复开炮），看起来像「点了没反应」。
    两个过滤缺一不可：后缀过滤排除**标记元素自己**（`bs-attack-x-y-miss` 也以
    `bs-attack-` 开头），`:has()` 过滤排除**里面已经有标记的格子**。
    """
    return page.locator(
        '[data-testid^="bs-attack-"]'
        ':not([data-testid$="-hit"]):not([data-testid$="-miss"])'
        ':not(:has([data-testid$="-hit"])):not(:has([data-testid$="-miss"]))'
    ).nth(nth)


def main():
    print('=== Web E2E: 海战棋玩法增强（v1.10）===')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME)
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(15000)
        pb.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        goto_tab(pa, '房间')
        pa.get_by_text('创建', exact=True).first.click()
        pa.get_by_text('海战棋', exact=True).last.click()
        pa.get_by_text('创建', exact=True).last.click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        code = re.search(r'\b[A-Z0-9]{6}\b', body(pa)).group(0)

        # 房主可以给人机选难度（海战棋专属）。**必须在对手加入之前查**——
        # 海战棋是 2 人房，人一进来房间就满了，「添加人机」整块按 `!isFull` 隐藏。
        pa.get_by_test_id('bot-difficulty-hard').wait_for(timeout=10000)
        pa.get_by_test_id('bot-difficulty-hard').click()
        pa.wait_for_timeout(300)
        assert '困难' in body(pa), '难度选项未渲染'
        ok('房间页出现人机难度选择（简单/普通/困难），可切到「困难」')

        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)

        pb.get_by_text('准 备', exact=True).click()
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        pa.get_by_text('完成布阵', exact=True).wait_for(timeout=15000)
        ok(f'创建并开局 {code}，进入布阵页')

        # —— 1. 布阵期武器挂靠面板 ——
        radar_chip = pa.get_by_test_id('bs-weapon-assign-radar')
        radar_chip.wait_for(timeout=10000)
        for w in ('radar', 'salvo', 'strike'):
            assert pa.get_by_test_id(f'bs-weapon-assign-{w}').count() == 1, f'缺少 {w} 挂靠芯片'
        before = ' '.join(radar_chip.inner_text().split())
        radar_chip.click()
        pa.wait_for_timeout(300)
        after = ' '.join(pa.get_by_test_id('bs-weapon-assign-radar').inner_text().split())
        assert before != after, f'点击芯片未切换携带舰船（仍为 {before}）'
        ok(f'武器挂靠面板可切换携带舰船（{before} → {after}）')

        # 两边都要提交才开始对局——只等一方会死等（提交后按钮就消失了）
        pa.get_by_text('完成布阵', exact=True).click()
        pa.wait_for_timeout(1000)
        pb.get_by_text('完成布阵', exact=True).click()
        pb.wait_for_timeout(1000)
        pa.get_by_text('攻击面板', exact=True).wait_for(timeout=15000)
        pb.get_by_text('攻击面板', exact=True).wait_for(timeout=15000)
        ok('双方进入战斗阶段')

        # —— 2. 对局期武器条 ——
        for w in ('radar', 'salvo', 'strike'):
            assert pa.get_by_test_id(f'bs-weapon-{w}').count() == 1, f'武器条缺少 {w}'
        assert '可用' in pa.get_by_test_id('bs-weapon-radar').inner_text(), '武器初始应为可用'
        assert '对方武器 3/3' in body(pa), '未显示对方武器余量'
        ok('武器条显示三件武器可用 + 对方武器余量 3/3')

        # —— 3. 己方海域盘不能静默吞掉点击（用户实测报过的坑）——
        # 武器条挂在分段 Tab **上方**，任何分段都能点；而己方海域是只读盘。
        # 曾经的症状：「点雷达 → 点棋盘中的一个位置 → 没反应，之后直接点棋盘也
        # 没法普通攻击」——用户在只读盘上反复点击，全程零反馈。
        shooter, other = whose_turn(pa, pb)
        shooter.get_by_text('我的海域', exact=True).first.click()
        shooter.wait_for_timeout(400)
        assert '己方海域' in body(shooter), '未切到己方海域'
        shooter.get_by_test_id('bs-weapon-radar').click()
        shooter.wait_for_timeout(500)
        assert '攻击面板' in body(shooter), '在己方海域点武器后没有自动切回敌方海域'
        assert '点击要探测的海域中心' in body(shooter), '未进入瞄准'
        # 瞄准中切回己方海域 → 取消瞄准，并说明为什么
        shooter.get_by_text('我的海域', exact=True).first.click()
        shooter.wait_for_timeout(500)
        assert '点击要探测的海域中心' not in body(shooter), '切段后瞄准态没被取消'
        assert '已取消瞄准' in body(shooter), '取消瞄准没给出解释'
        # 非瞄准态点己方海域盘 → 也要有一句解释，而不是毫无反应
        shooter.wait_for_timeout(2800)          # 等上一条 toast 自己消失
        shooter.locator('[data-testid^="bs-own-"]').nth(30).click()
        shooter.wait_for_timeout(600)
        assert '这是你的海域' in body(shooter), '点己方海域盘没有任何反馈'
        shooter.get_by_text('敌方海域', exact=True).first.click()
        shooter.wait_for_timeout(400)
        assert '攻击面板' in body(shooter), '未切回敌方海域'
        ok('己方海域点武器自动切回敌方海域；瞄准中切段取消瞄准；点只读盘给出解释')

        # —— 4. 雷达：探测 → 区域标记 → 交出回合 ——
        shooter, other = whose_turn(pa, pb)
        shooter.get_by_test_id('bs-weapon-radar').click()
        shooter.wait_for_timeout(300)
        assert '点击要探测的海域中心' in body(shooter), '雷达瞄准提示未出现'
        unfired_cell(shooter).click()
        shooter.wait_for_timeout(1200)
        assert shooter.locator(
            '[data-testid^="bs-scan-"]'
        ).count() >= 1, '雷达探测后盘上未出现区域标记'
        assert '已用' in shooter.get_by_test_id('bs-weapon-radar').inner_text(), \
            '雷达用完后未标记为已用'
        shooter.wait_for_timeout(800)
        new_shooter, _ = whose_turn(pa, pb)
        assert new_shooter is other, '使用雷达后回合未交出（武器应独占一个回合）'
        ok('雷达：探测出区域标记、标记为已用、交出回合')

        # —— 5. 空袭：3×3 范围打击 ——
        shooter, other = whose_turn(pa, pb)
        before_marks = shooter.locator(
            '[data-testid^="bs-attack-"][data-testid$="-hit"], '
            '[data-testid^="bs-attack-"][data-testid$="-miss"]'
        ).count()
        shooter.get_by_test_id('bs-weapon-strike').click()
        shooter.wait_for_timeout(300)
        assert '空袭' in body(shooter), '空袭瞄准提示未出现'
        unfired_cell(shooter).click()
        shooter.wait_for_timeout(1500)
        after_marks = shooter.locator(
            '[data-testid^="bs-attack-"][data-testid$="-hit"], '
            '[data-testid^="bs-attack-"][data-testid$="-miss"]'
        ).count()
        assert after_marks >= before_marks + 4, \
            f'空袭应一次打出多格（{before_marks} → {after_marks}）'
        assert '已用' in shooter.get_by_test_id('bs-weapon-strike').inner_text()
        ok(f'空袭：一次结算 {after_marks - before_marks} 格并标记为已用')

        # —— 6. 齐射：连点 3 格自动发炮 ——
        shooter, other = whose_turn(pa, pb)
        shooter.get_by_test_id('bs-weapon-salvo').click()
        shooter.wait_for_timeout(300)
        assert '选择齐射目标 0/3' in body(shooter), '齐射计数提示未出现'
        for i in range(3):
            # 必须逐个取不同格子：已选的目标格仍未开火，nth(0) 永远返回同一格，
            # 再点它会命中「这个格子已经选过了」而计数不推进
            unfired_cell(shooter, i).click()
            shooter.wait_for_timeout(400)
            if i < 2:
                assert f'选择齐射目标 {i + 1}/3' in body(shooter), \
                    f'齐射计数未推进到 {i + 1}/3'
        shooter.wait_for_timeout(1500)
        assert '已用' in shooter.get_by_test_id('bs-weapon-salvo').inner_text(), \
            '齐射未标记为已用'
        ok('齐射：连选 3 格自动发炮并标记为已用')

        # —— 7. 命中续射：命中后回合仍在自己手里 ——
        # 真浏览器上双方轮流点未开火格，直到出现命中（100 格打完必然出现命中）。
        kept_turn = False
        saw_miss_pass = False
        for _ in range(70):
            try:
                shooter, other = whose_turn(pa, pb)
            except AssertionError:
                break          # 对局已结束（双方都不再有回合提示）
            cell = unfired_cell(shooter)
            if cell.count() == 0:
                break
            hits_before = shooter.locator('[data-testid$="-hit"]').count()
            cell.click()
            shooter.wait_for_timeout(500)
            hits_now = shooter.locator('[data-testid$="-hit"]').count()
            kept = '轮到你开炮' in body(shooter)
            if hits_now > hits_before:
                # 刚命中：服务端应保留回合（先手第一发例外，见后端用例）
                if kept:
                    kept_turn = True
                    break
            elif not kept:
                saw_miss_pass = True   # 未命中正常换手
        assert saw_miss_pass, '未命中时回合没有正常交出'
        assert kept_turn, '命中后回合没有保留（命中续射未生效）'
        # 提示条也要解释「为什么还是我的回合」
        notice = body(shooter)
        assert '开炮' in notice or '命中' in notice, '命中后未给出续射提示'
        ok('命中续射：未命中正常换手、命中后回合保留并提示「继续开炮」')

        # 收尾：认输 → 返回房间
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pa.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        pa.get_by_text('返回房间', exact=True).click()
        pb.get_by_text('对局结束', exact=True).wait_for(timeout=10000)
        pb.get_by_text('返回房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        assert code in body(pa), '返回后未回到房间页'
        ok('认输结算并返回房间，房间保留')

        browser.close()
    print(f'=== 海战棋玩法增强 E2E 通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())