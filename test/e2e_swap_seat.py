#!/usr/bin/env python3
"""Web E2E：等待大厅里互换座位（v1.16）。

用户要这条的理由：「棋类第一个位置是先手、牌类决定出牌顺序」。
本脚本盯三件事：
  ① 真人之间**必须对方点头**（请求方不能单方面挪座位）；
  ② 同意之后**双方看到的座位顺序都变了**（广播 lobby，不是只回给请求方）；
  ③ 人机**自动同意**（它没有「不同意」这个语义）。

用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_swap_seat.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


# 玩家列表里除名字以外的那些小标签（徽标 / 按钮）
BADGES = {'房主', '未准备', '已准备', '断线', '换位', '等待同意', '移除', '人机'}


def player_order(page):
    """从「玩家 (n/m)」那一段里按出现顺序取出玩家名（人机的名字也要能取到）。

    截到动作区为止 —— 那是玩家列表之后的东西。
    """
    body = page.locator('body').inner_text()
    seg = body.split('玩家 (', 1)[1].split('\n', 1)[1]     # 去掉 "2/4)" 那一行
    seg = re.split(r'开始游戏|等待玩家|等待准备|退出房间|准 备|取消准备', seg, 1)[0]
    return [ln.strip() for ln in seg.splitlines()
            if ln.strip() and ln.strip() not in BADGES]


def main():
    print('=== Web E2E: 房间内互换座位 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        pa = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pb = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        pa.set_default_timeout(20000)
        pb.set_default_timeout(20000)
        ua, ub = f'swap{TS}a', f'swap{TS}b'
        try:
            register(pa, ua)
            register(pb, ub)

            # 用「游戏」Tab 建房（UNO 有 4 个座位，等下还要加人机）
            goto_tab(pa, '游戏')
            pa.get_by_text('UNO 卡牌', exact=True).first.click()
            pa.wait_for_timeout(600)
            pa.get_by_text('创建房间', exact=True).last.click()
            pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
            code = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text()).group(0)

            goto_tab(pb, '房间')
            pb.get_by_text('加入', exact=True).first.click()
            pb.get_by_placeholder('输入6位房间号').fill(code)
            pb.get_by_text('加入', exact=True).last.click()
            pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
            assert player_order(pa) == [ua, ub], player_order(pa)
            ok(f'两人就位，座位顺序 [{ua}, {ub}]')

            # ① 真人之间：请求 → 对方弹窗 → 还没同意之前座位不动
            pa.get_by_text('换位', exact=True).first.click()
            pb.locator('[data-testid="swap-request-modal"]').wait_for(timeout=8000)
            assert f'「{ua}」想和你交换位置' in pb.locator('body').inner_text(), \
                pb.locator('body').inner_text()[:200]
            pa.wait_for_timeout(800)
            assert player_order(pa) == [ua, ub], '对方还没同意就换了座位'
            ok('B 收到换位弹窗；未同意前座位不动')

            # 待答态：发起方那颗按钮要**停在**「等待同意」。服务端对真人目标会立刻回
            # 一条 `accepted: null` 的「已转达」ack —— 曾经拿它清掉待答态，于是按钮
            # 只闪一帧就变回「换位」，玩家以为没发出去又点一次。
            assert pa.get_by_text('等待同意', exact=True).count() == 1, \
                '发起方没显示「等待同意」（待答态一帧就没了）'
            ok('发起方停在「等待同意」上')

            # ② 同意 → 双方看到的顺序都换过来
            pb.locator('[data-testid="swap-accept"]').click()
            pb.get_by_text('点击复制', exact=True).first.wait_for(timeout=10000)
            pa.wait_for_timeout(1200)
            assert player_order(pa) == [ub, ua], f'A 侧顺序没变：{player_order(pa)}'
            assert player_order(pb) == [ub, ua], f'B 侧顺序没变：{player_order(pb)}'
            assert pa.get_by_text('等待同意', exact=True).count() == 0, \
                '拿到最终答复后「等待同意」没复位'
            ok('同意后双方座位顺序都对调了（广播到位），待答态复位')

            # ③ 人机自动同意（人机是列表里最后一位，它的「换位」按钮也是最后一个）
            pa.get_by_text('添加人机', exact=True).click()
            pa.wait_for_timeout(1200)
            assert player_order(pa) == [ub, ua, '人机·棋手'], player_order(pa)
            pa.get_by_text('换位', exact=True).last.click()
            pa.wait_for_timeout(1800)
            order = player_order(pa)
            assert order == [ub, '人机·棋手', ua], f'和人机换位后顺序不对：{order}'
            ok(f'人机自动同意，顺序变成 {order}')

            # ④ **座位真的决定了出牌顺序**（用户要这条功能的理由就是它）。
            # 现在顺序是 [B, 人机, A]，UNO 座位 0 先手 —— 所以 B 那边「轮到你出牌」，
            # A 那边「等待 <B> 出牌」。这是整条链路的终局证据：
            # 换位 → players 下标 → `_start_uno` 的座位 → 下发的 `turn`。
            pb.get_by_text('准 备', exact=True).click()
            pa.wait_for_timeout(600)
            pa.get_by_text('开始游戏', exact=True).click()
            pb.get_by_text('轮到你出牌', exact=True).wait_for(timeout=20000)
            assert f'等待 {ub} 出牌' in pa.locator('body').inner_text(), \
                pa.locator('body').inner_text()[:200]
            ok('开局后座位 0（B）先出牌 —— 换位真的改变了出牌顺序')
        finally:
            pa.close()
            pb.close()
            browser.close()
    print(f'=== 互换座位 E2E 通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
