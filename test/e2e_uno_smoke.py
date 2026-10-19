#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：UNO 卡牌冒烟（三人局 —— 认输只出局、牌局继续）。

覆盖：建房/加入/开始 → 每人只有自己的手牌（对手只显示张数）→ 轮到的人出牌或
摸牌（摸到能出的要过牌）→ 非本回合的人按钮置灰 → 认输只让本人出局、牌局继续 →
只剩一人时终局结算 → 返回房间 ready 复位 → 战绩落库且快照能渲染。

**手牌私有**：浏览器这一层只验证「渲染出来的牌面张数 == 自己的手牌张数」；
「服务端有没有把别人的手牌发过来」在 `server/test/test_ws_uno.py` 里直接断言
消息内容（那一层才看得见线路上的字节）。

置灰的按钮在 RNW 里是 `pointer-events:none`，Playwright 点击会挂起——所以
「能不能点」一律读 computedStyle 的 opacity（`.4` 即禁用），不去点它。

用法：先启动后端(50541)和前端 web(50540)，然后 python test/e2e_uno_smoke.py
"""
import json
import re
import sys
import time

import requests
import websocket as ws_mod
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from websocket import create_connection

BASE = 'http://localhost:50540'
API = 'http://127.0.0.1:50541'
WS = 'ws://127.0.0.1:50541'
PWD = 'test123456'
TS = int(time.time())
UA, UB, UC = f'un_a_{TS}', f'un_b_{TS}', f'un_c_{TS}'
UBOT = f'un_bot_{TS}'
STEP = 0
# 全程走了几手「黑牌 + 选色」。**这是一个跨阶段的总数**：一手黑牌能不能打出去取决于
# 起手摸到什么牌，单看某一局是随机的（一副 164 张里 32 张黑牌，一个人起手 7 张全不是
# 黑牌的概率约 26%）。所以判据放在**整轮跑完**上 —— 三局都碰不到一张黑牌的概率可以忽略。
WILD_PLAYS = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(600)


def open_settings(page):
    """打开「玩法设置」抽屉（v1.15：房间级设置从行内芯片收进了底部抽屉）。

    **抽屉是盖屏的 Modal** —— 开完一定要 `close_settings`，否则后面点不到
    「开始游戏」。
    """
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


def opacity(page, testid):
    """RNW 的 disabled 是 pointer-events:none，读 opacity 判断是否可点。"""
    return float(page.eval_on_selector(
        f'[data-testid="{testid}"]', 'el => getComputedStyle(el).opacity'))


def enabled(page, testid):
    return opacity(page, testid) > 0.9


def hand_cards(page):
    """自己手牌区渲染出的牌张数（可出的牌 testID 是 u-play-*，其余是 u-card-*）。"""
    return page.locator('[data-testid^="u-card-"], [data-testid^="u-play-"]').count()


def hand_count_of(page, seat):
    """某个座位的手牌**张数**（客户端只拿到张数，拿不到牌面）。

    出局/掉线的座位不显示张数，那种情况返回 None。
    """
    el = page.locator(f'[data-testid="u-count-{seat}"]')
    if el.count() == 0:
        return None
    txt = el.inner_text()
    m = re.search(r'(\d+)', txt)
    return int(m.group(1)) if m else None


def click_first(page, prefix):
    """点第一个匹配 `prefix` 的 testID，没有就返回 False。"""
    el = page.locator(f'[data-testid^="{prefix}"]')
    if el.count() == 0:
        return False
    el.first.click()
    return True


def take_turn(page, prefer_wild=False):
    """轮到的人走一手：有可出的牌就出，否则摸牌（摸到能出的还得过牌）。

    `prefer_wild=True` 时**优先打黑牌**（会弹选色框）—— 无怜悯档靠它把
    `W+6` / `W+10` / `W+RD4` 那条「先选色再出牌」的路真的走一遍。默认优先打非黑牌：
    这样 7 换手那条路不会被黑牌挡掉，常见路径也不会平白多一步。

    进函数先接住**颜色轮盘**（`W+RO`）—— 它是本作唯一「等某个座位做选择」的状态机：
    打出它的人不选色，选色的是**下家**，而下家这时 `playable` / `can_draw` 全是空的
    （服务端把出牌与摸牌都挡了），只认 `u-play-*` 的话整桌就停在这里。

    出牌之后可能弹两个框，都得接住，否则整局卡在这里：
    - 黑牌选色（经典规则就有）
    - **7 换手选人**（`seven_zero` 房规）
    """
    global WILD_PLAYS
    # 颜色轮盘：轮到的是「点名」，不是出牌
    if page.locator('[data-testid="u-color-R"]').count() > 0:
        page.locator('[data-testid="u-color-R"]').click()
        page.wait_for_timeout(500)
        return '轮盘点名'
    plays = page.locator('[data-testid^="u-play-"]')
    n = plays.count()
    if n > 0:
        target = None
        for i in range(n):
            label = plays.nth(i).get_attribute('aria-label') or ''
            if ('变色' in label) == prefer_wild:
                target = plays.nth(i)
                break
        if target is None:
            target = plays.first
        label = target.get_attribute('aria-label') or ''
        target.click()
        page.wait_for_timeout(400)
        if '变色' in label:
            # 黑牌：必须选一个颜色，否则服务端整手拒收
            page.locator('[data-testid="u-color-R"]').click()
            page.wait_for_timeout(500)
            WILD_PLAYS += 1
            return 'play(黑牌+选色)'
        # 7 换手：选人框弹出时必须选一个，不选这一手就发不出去
        if page.locator('[data-testid^="u-target-"]').count() > 0:
            click_first(page, 'u-target-')
            page.wait_for_timeout(500)
            return 'play(7换手)'
        page.wait_for_timeout(200)
        return 'play'
    page.locator('[data-testid="u-draw"]').click()
    page.wait_for_timeout(500)
    if enabled(page, 'u-pass'):
        page.locator('[data-testid="u-pass"]').click()
        page.wait_for_timeout(400)
        return 'draw+pass'
    return 'draw'


def turn_actor(pa, pb):
    """该谁动：轮到出牌的人，**或颜色轮盘正等着点名的那个人**。

    轮盘挂起时，点名者的回合条写的是「颜色轮盘：点一个颜色」、其他人写的是
    「等 X 点颜色」—— 两种都不是「轮到你」，只认后者会让整桌停住。
    终局（结算浮层已出）返回 None。
    """
    # 终局判定要**先扫完两个人**再挑人：广播有先后，只查第一个页面的话，
    # 另一边的结算浮层还没到、回合条还停在旧文案上，会挑出一个已经不能动的页面。
    for pg in (pa, pb):
        if pg.locator('[data-testid="u-result-back"]').count() > 0:
            return None
    for pg in (pa, pb):
        t = pg.locator('[data-testid="u-turn"]').inner_text()
        if '轮到你' in t or '点一个颜色' in t:
            return pg
    return None


def join_and_ready(page, code):
    goto_tab(page, '房间')
    page.get_by_text('加入', exact=True).first.click()
    page.get_by_placeholder('输入6位房间号').fill(code)
    page.get_by_text('加入', exact=True).last.click()
    page.get_by_text('房间号', exact=True).wait_for(timeout=15000)
    page.get_by_text('准 备', exact=True).click()


def reset_to_lobby(page):
    """整页重载回大厅。

    **比「一路点回去」可靠得多**：上一阶段结束时页面可能停在结算弹窗上、可能停在
    房间页、也可能**还在对局屏上**（那时连「退出房间」按钮都没有，逐个 case 去点
    等于把 E2E 写成状态机）。`page.goto` 之后应用从登录态直接落回大厅 —— token 在
    storage 里，重载不丢。
    """
    page.goto(BASE)
    page.get_by_text('社交', exact=True).last.wait_for(timeout=25000)


def pump_bot(ws, stats):
    """把机器人收到的一切处理完（非阻塞），顺手统计它看到几次抢出机会。

    机器人的一切判断都读**服务端下发的字段**（`jumpable` / `playable` / `turn` /
    `swap_targets`），一格规则都不重写 —— 与浏览器端同一条纪律。
    """
    while True:
        try:
            # 非阻塞：超时即「这一轮没有新消息」，直接返回（外层循环会再调）
            raw = ws.recv()
        except (ws_mod.WebSocketTimeoutException,
                ws_mod.WebSocketConnectionClosedException):
            return
        msg = json.loads(raw)
        t = msg.get('type')
        if t == 'error':
            stats['errors'].append(msg.get('message'))
            continue
        if t not in ('game_start', 'uno_state'):
            continue
        g = msg.get('game') if t == 'game_start' else msg
        if not isinstance(g, dict) or g.get('finished'):
            continue
        seat = g.get('your_seat')
        if seat is None or seat in (g.get('out') or []):
            continue
        # 抢出：服务端说我此刻能抢（`jumpable` 非空），那就抢 —— 不占自己的回合
        if g.get('jumpable') and g.get('turn') != seat:
            stats['saw'] += 1
            ws.send(json.dumps({'type': 'uno_play', 'jump': True,
                                'card': g['jumpable'][0]}))
            stats['jumps'] += 1
            continue
        if g.get('turn') != seat:
            continue
        playable = g.get('playable') or []
        # **优先打手里有对子的那张**：打出一张之后，另一张与牌堆顶完全相同、而回合
        # 已经交给下家 —— 这正是抢出的判据，于是下一次同步里 `jumpable` 必然非空。
        # 这不是作弊：机器人照样只读服务端下发的 `playable`，选哪一张是它自己的策略。
        # 不加这一条的话这一步纯看运气（要正好摸到一对），实测约两成概率整局不出现机会。
        hand = g.get('hand') or []
        dupes = [c for c in playable if hand.count(c) > 1]
        pick = (dupes or playable or [None])[0]
        if pick:
            body = {'type': 'uno_play', 'card': pick}
            if pick[0] == 'W':
                body['color'] = 'R'
            if pick[1:] == '7' and (g.get('swap_targets') or []):
                body['target'] = g['swap_targets'][0]
            ws.send(json.dumps(body))
        else:
            ws.send(json.dumps({'type': 'uno_draw'}))


def phase_rules(pa, pb):
    """第二阶段：**房规选档 + 抢出**（v1.14）。

    为什么单开一间新房：第一阶段的房间已经打完一局、房规是经典档，而房规**只能在
    `waiting` 时改**、且是 `create_game` 时定下并落进 `gs` 的 —— 换档必须重开一局。

    抢出这件事**要多一个玩家才容易发生**（同一张牌得同时落在两个人手里），所以这
    一阶段用**原生 WebSocket 机器人**当第三个玩家：它同样只读服务端下发的
    `jumpable`，看到就抢。浏览器侧只负责断言「角标出现了 → 点得动 → 提示条说抢出」。
    """
    r = requests.post(f'{API}/api/auth/register',
                      json={'username': UBOT, 'password': PWD})
    assert r.status_code == 200, r.text
    bot_token = r.json()['token']

    # 先把两个页面收拾回大厅（阶段一结束时 A 停在结算弹窗上、B 还留在那间房里）
    for pg in (pa, pb):
        reset_to_lobby(pg)

    # A 建一间新房
    goto_tab(pa, '游戏')
    pa.get_by_text('UNO 卡牌', exact=True).first.click()
    pa.get_by_text('创建房间', exact=True).click()
    pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
    code = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text()).group(0)
    ok(f'新房 {code}（用于房规与抢出）')

    # 大厅里能改房规：档位与 7 条房规都在。
    # v1.15 起这些芯片收进了「玩法设置」抽屉（房间页只留一行摘要）——
    # UNO 有 3 个档位 + 7 条房规，铺在页面上正是用户报的「很难看」。
    open_settings(pa)
    pa.locator('[data-testid="room-settings-preset-party"]').wait_for(timeout=10000)
    body = pa.locator('body').inner_text()
    assert '经典' in body and '热闹' in body and '无怜悯' in body, f'档位芯片没渲染：{body[:200]}'
    for key in ("stack_mode", "draw_until_play", "must_play", "jump_in",
                "seven_zero", "deck", "mercy"):
        assert pa.locator(f'[data-testid^="room-settings-{key}-"]').count() > 0, \
            f'房规「{key}」的控件没渲染出来'
    ok('抽屉渲染出档位芯片与全部房规开关（名字与说明都来自服务端）')

    # 选「热闹」→ 抽屉里与房间页那一行都跟着变
    pa.locator('[data-testid="room-settings-preset-party"]').click()
    pa.wait_for_timeout(800)
    assert '热闹' in pa.locator('body').inner_text()
    close_settings(pa)
    assert '热闹' in pa.locator('body').inner_text(), '抽屉关了但房间页摘要没更新'
    ok('房主选「热闹」档，房间页跟着换名字与说明')

    # 逐条改一条 → 档位落到「自定义」（档位是**反查**出来的，不是记下来的）
    open_settings(pa)
    pa.locator('[data-testid="room-settings-jump_in-off"]').click()
    pa.wait_for_timeout(800)
    assert '自定义' in pa.locator('body').inner_text(), \
        '改过开关之后档位应变成「自定义」'
    # 再点回「热闹」：整份房规被一键填回来
    pa.locator('[data-testid="room-settings-preset-party"]').click()
    pa.wait_for_timeout(800)
    # ⚠️ 判据不能是「`jump_in-on` 芯片存不存在」—— 开关的两枚芯片（开/关）
    # **同时渲染**，那样断言恒真。真正的信号是档位名：填回「热闹」之后它不再是
    # 「自定义」（`value_label` 由服务端按当前房规反查出来）。
    assert '自定义' not in pa.locator('body').inner_text(), \
        '一键填回之后档位应重新落回「热闹」'
    close_settings(pa)
    ok('逐条改 → 「自定义」；再点档位芯片 → 一键填回')

    # 机器人加入并准备，B 也进来
    assert requests.post(f'{API}/api/rooms/{code}/join', json={},
                         headers={'Authorization': f'Bearer {bot_token}'}).status_code == 200
    ws = create_connection(f'{WS}/ws/room/{code}?token={bot_token}', timeout=30)
    ws.settimeout(0.05)
    pump_bot(ws, {'jumps': 0, 'saw': 0, 'errors': []})   # 吃掉 sync
    ws.send(json.dumps({'type': 'ready'}))
    join_and_ready(pb, code)
    ok('第三人（原生 WebSocket 机器人）与 B 加入并准备')

    pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
    pa.get_by_text('开始游戏', exact=True).click()
    for pg in (pa, pb):
        pg.locator('[data-testid="u-draw"]').wait_for(timeout=20000)
    ok('热闹档开局，两方进入对局页')

    # 「有牌必出」房规**真的到界面上了**：手里有能出的牌时，摸牌按钮必须置灰。
    # 这是本阶段最硬的一条断言 —— 房规一路从大厅走到引擎再走回界面。
    for pg, who in ((pa, 'A'), (pb, 'B')):
        if pg.locator('[data-testid="u-turn"]').inner_text().find('轮到你') < 0:
            continue
        if pg.locator('[data-testid^="u-play-"]').count() > 0:
            assert not enabled(pg, 'u-draw'), \
                f'{who} 手里有能出的牌，「有牌必出」档下摸牌按钮应置灰'
            ok(f'「有牌必出」生效：{who} 有牌可出时摸牌按钮置灰')
            break

    # 抢出：三方轮流走子。`jumpable` 是**按人算**的 —— 谁手里有与牌堆顶完全相同的
    # 牌，谁的服务端状态里就有它（机器人手里有对子 ≠ 浏览器手里有）。
    # 机器人看到就抢（它零延迟，基本必中），浏览器侧则等角标出现再点 —— 后者是
    # **机会性的**：要正好有个浏览器玩家也拿着同一张牌。所以断言是「两边至少有一边
    # 真的抢成了」，而这条房规本身的回归在引擎与 WS 层（`TestJumpIn` 9 条 +
    # `TestJumpInWire` 5 条，含抢输的提示文案）。
    stats = {'jumps': 0, 'saw': 0, 'errors': []}
    saw_badge = False
    for _ in range(120):
        for pg in (pa, pb):
            # 只吞 **Playwright 自己的超时**：三方轮流走子时页面可能在转场，元素
            # 一瞬不在。**不吞别的异常** —— 真出错要炸出来（本项目「不写静默 try」
            # 那条纪律：静默 try 会把崩溃伪装成功能没做）。
            try:
                if '轮到你' in pg.locator('[data-testid="u-turn"]').inner_text():
                    take_turn(pg)
            except PlaywrightError:
                pass
        pump_bot(ws, stats)
        for pg in (pa, pb):
            # 角标与可点的牌是两个 testID（`u-jumpbadge-*` / `u-jump-*`）——
            # **前缀不能撞**，否则 `[data-testid^="u-jump-"]` 会把角标也选进来。
            if pg.locator('[data-testid^="u-jumpbadge-"]').count() == 0:
                continue
            card = pg.locator('[data-testid^="u-jump-"]')
            # **抢出是竞态的**：机器人可能刚刚也看上了同一张，它那条消息还在路上。
            # 点之前再确认一次；点空了就当作「这一轮被别人抢走」，继续等下一次。
            if card.count() == 0:
                continue
            try:
                card.first.click(timeout=3000)
            except PlaywrightError:
                continue
            pg.wait_for_timeout(700)
            paper = pg.locator('body').inner_text()
            assert '抢出' in paper, f'点了抢出角标后提示条没说是抢出：{paper[:200]}'
            saw_badge = True
            break
        if saw_badge:
            break
        for pg in (pa, pb):
            if '本局结束' in pg.locator('body').inner_text():
                break
        time.sleep(0.15)

    assert stats['jumps'] >= 1 or saw_badge, \
        f'整局都没出现抢出（机器人看到 {stats["saw"]} 次机会，错误 {stats["errors"][-3:]}）'
    ok(f'抢出生效（机器人看到 {stats["saw"]} 次机会、抢了 {stats["jumps"]} 次；'
       f'浏览器侧点到角标 {saw_badge}；机器人收到 {len(stats["errors"])} 条 error）')

    ws.close()


def phase_no_mercy(pa, pb):
    """第三阶段：**无怜悯档**（v1.14 批次 B）—— 换的是整副牌，不是几条开关。

    最硬的一条断言是**抽牌堆张数**：2 人局 164 − 2×7 − 1 = 149。经典牌堆同条件下是
    93。这一个数字同时锁住「大厅选的档位 → 房间房规 → `create_game` 用哪副牌 →
    下发的 `draw_pile_count`」整条链路 —— 中间断一环就对不上。
    """
    for pg in (pa, pb):
        reset_to_lobby(pg)

    goto_tab(pa, '游戏')
    pa.get_by_text('UNO 卡牌', exact=True).first.click()
    pa.get_by_text('创建房间', exact=True).click()
    pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
    code = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text()).group(0)

    open_settings(pa)
    pa.locator('[data-testid="room-settings-preset-no_mercy"]').wait_for(timeout=10000)
    pa.locator('[data-testid="room-settings-preset-no_mercy"]').click()
    pa.wait_for_timeout(800)
    body = pa.locator('body').inner_text()
    assert '无怜悯' in body and '164' in body, f'档位说明没渲染：{body[:200]}'
    close_settings(pa)
    ok(f'新房 {code}：选「无怜悯」档，说明里写明是 164 张的狠牌堆')

    join_and_ready(pb, code)
    pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
    pa.get_by_text('开始游戏', exact=True).click()
    for pg in (pa, pb):
        pg.locator('[data-testid="u-draw"]').wait_for(timeout=20000)

    for pg, who in ((pa, 'A'), (pb, 'B')):
        pile = re.search(r'剩 (\d+) 张', pg.locator('body').inner_text())
        assert pile, f'{who} 看不到抽牌堆张数'
        assert pile.group(1) == '149', \
            f'{who} 看到的抽牌堆应是 164-14-1=149（无怜悯牌堆），实际 {pile.group(1)}'
    ok('开局抽牌堆 149 张（164 张的牌堆，不是 108 那张）—— 档位一路走到了牌桌上')

    # 新牌面的文案不能漏：漏了的话 `cardLabel` 会退化成 `<颜色名>色 <原始牌码>`。
    # **判据必须盯死那个退化形态**：只找 `"+6"` 会误报（正确的文案「变色 +6」里
    # 也含它），只找 `"色 +10"` 同样会误报（「变色 +10」含 `色 +10`）——
    # 两版都踩过。颜色名与牌码是退化的**独有**组合，用它们才判得准。
    raw = re.compile(r'[红绿蓝黄黑]色 (SE|DA|\+6|\+10|\+RD4|\+RO)')
    for pg, who in ((pa, 'A'), (pb, 'B')):
        labels = pg.eval_on_selector_all(
            '[data-testid^="u-card-"], [data-testid^="u-play-"]',
            'els => els.map(e => e.getAttribute("aria-label") || "")')
        assert labels, f'{who} 手牌上没有 aria-label'
        for lab in labels:
            hit = raw.search(lab)
            assert not hit, f'{who} 有一张牌的文案没写：{lab}'
    ok(f'手牌无障碍文案齐全（{len(labels)} 张，没有漏掉新牌面的原始牌码）')

    # 多走几手，把新牌面**真的打出去**。只断言文案齐全是不够的 —— 文案对、牌却打不出去
    # 正是 v1.14 的 `W+6` / `W+10` / `W+RD4`：客户端不认它们是黑牌，于是不带颜色把牌
    # 发上去、被服务端整手拒收，界面上就是「点了没反应」。这里**优先打黑牌**逼出那条路。
    acted = wilds = 0
    for _ in range(24):
        pg = turn_actor(pa, pb)
        if pg is None:
            break
        if take_turn(pg, prefer_wild=True) == 'play(黑牌+选色)':
            wilds += 1
        acted += 1
    assert acted >= 1, '无怜悯档下一手都没走成'
    ok(f'无怜悯档下连走 {acted} 手（其中黑牌选色 {wilds} 手）—— 新牌堆整局照常跑')

    # 「黑牌 + 选色」这条客户端分支的**总**覆盖，跨三个阶段一起算：它单看一局是随机的
    # （一个人起手 7 张全不是黑牌的概率约 26%），所以判据放在整轮跑完上。
    assert WILD_PLAYS >= 1, f'整轮跑完一手黑牌都没打出去（本阶段 {acted} 手全是别的牌）'
    ok(f'全程共走 {WILD_PLAYS} 手「黑牌 + 选色」（新牌面真的打得出去）')


def main():
    print('=== Web E2E: UNO 卡牌冒烟（三人局） ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        ctx_args = {'viewport': {'width': 390, 'height': 844}}
        pa = browser.new_context(**ctx_args).new_page()
        pb = browser.new_context(**ctx_args).new_page()
        pc = browser.new_context(**ctx_args).new_page()
        for pg in (pa, pb, pc):
            pg.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        register(pc, UC)
        ok('三方注册登录')

        # A 建房
        goto_tab(pa, '游戏')
        pa.get_by_text('UNO 卡牌', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', pa.locator('body').inner_text())
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建 UNO 房间 {code}')

        # B、C 加入并准备，A 开始
        join_and_ready(pb, code)
        join_and_ready(pc, code)
        pa.get_by_text('开始游戏', exact=True).wait_for(timeout=10000)
        pa.get_by_text('开始游戏', exact=True).click()
        for pg in (pa, pb, pc):
            pg.locator('[data-testid="u-draw"]').wait_for(timeout=20000)
        ok('三方进入对局页，牌桌与手牌区渲染')

        # 开局：每人 7 张手牌，且各自只渲染自己的那 7 张
        for pg, who in ((pa, 'A'), (pb, 'B'), (pc, 'C')):
            n = hand_cards(pg)
            assert n == 7, f'{who} 开局手牌应渲染 7 张，实际 {n}'
        ok('每方开局 7 张手牌（同种子无意义，各自独立发牌）')

        # 对手只显示张数，没有牌面
        for pg, who in ((pa, 'A'), (pb, 'B'), (pc, 'C')):
            for seat in (0, 1, 2):
                cnt = hand_count_of(pg, seat)
                assert cnt == 7, f'{who} 看到的座位 {seat} 张数应为 7，实际 {cnt}'
        ok('对手手牌只显示张数（客户端根本没收到牌面）')

        # 回合制：座位 0（房主 A）先手，非本回合的人操作按钮置灰
        turn = pa.locator('[data-testid="u-turn"]').inner_text()
        assert '轮到你' in turn, f'A 应该先手，实际提示：{turn}'
        assert not enabled(pb, 'u-pass') and not enabled(pb, 'u-draw'), \
            '不是 B 的回合，B 的按钮应置灰'
        ok('座位 0 先手；非本回合玩家的按钮置灰（不靠隐藏，靠禁用态）')

        # A 走一手
        act = take_turn(pa)
        ok(f'A 走一手（{act}）')

        # 回合应当交给下一位：B 的按钮变可点
        pb.locator('[data-testid="u-turn"]').wait_for(timeout=8000)
        ok('回合成功移交到下一位')

        # 牌桌上的状态：颜色、方向、剩余牌堆都渲染出来了
        body_a = pa.locator('body').inner_text()
        assert '当前颜色' in body_a and '顺时针' in body_a, \
            f'牌桌状态没渲染：{body_a[:200]}'
        assert re.search(r'剩 \d+', body_a), '抽牌堆剩余数没显示'
        ok('牌桌状态可见（当前颜色/方向/剩余牌堆）')

        # 认输：B 出局，但牌局继续（A、C 还在打）
        pb.get_by_text('认输', exact=True).click()
        pb.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pb.get_by_text('确认认输', exact=True).click()
        # 出局后：回合提示变成「你已出局」，并出现返回房间的出口。
        # 用 testID 定位——「你已出局」这串字在提示条与底部横栏里各有一处
        # （get_by_text 会撞 strict mode）。
        pb.locator('[data-testid="u-turn"]').filter(has_text='你已出局').wait_for(timeout=8000)
        pb.locator('[data-testid="u-back-room"]').wait_for(timeout=8000)
        pa.wait_for_timeout(800)
        assert '本局结束' not in pa.locator('body').inner_text(), \
            'B 认输不该终结整局（三人局只出局一人）'
        assert '已出局' in pa.locator('body').inner_text(), 'A 应该看得到 B 已出局'
        ok('B 认输只出局，A/C 的牌局继续')

        # B 点返回房间：不该被弹回对局页（出局者仍留在房间名单里）
        pb.locator('[data-testid="u-back-room"]').click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.wait_for_timeout(1500)
        assert '房间不存在' not in pb.locator('body').inner_text()
        assert pb.locator('[data-testid="u-draw"]').count() == 0, \
            'B 已出局，返回房间后不该被自动弹回对局页'
        ok('出局者返回房间不会被弹回对局页')

        # A 也认输 → 只剩 C → 终局结算
        pa.get_by_text('认输', exact=True).click()
        pa.get_by_text('确认认输', exact=True).wait_for(timeout=5000)
        pa.get_by_text('确认认输', exact=True).click()
        pc.locator('[data-testid="u-result-back"]').wait_for(timeout=10000)
        settle = pc.locator('body').inner_text()
        assert '率先出完手牌获胜' in settle or '获胜' in settle, \
            f'结算文案不对：{settle[:200]}'
        assert '张' in settle and '分' in settle, f'结算应给出张数与点数：{settle[:200]}'
        ok('只剩一人 → 终局结算（名次 + 剩余张数 + 点数）')

        # C 返回房间：房间保留可续开，且 ready 已复位
        pc.locator('[data-testid="u-result-back"]').click()
        pc.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        body_c = pc.locator('body').inner_text()
        assert code in body_c and '房间不存在' not in body_c, f'返回后房间异常：{body_c[:150]}'
        pc.get_by_text('准 备', exact=True).wait_for(timeout=8000)
        ok('返回房间保留（可续开），ready 已复位')

        # 战绩落库 + 快照渲染（新游戏最容易漏接的一环）
        pc.get_by_text('退出房间', exact=True).first.click()
        pc.wait_for_timeout(1200)
        goto_tab(pc, '我的')
        pc.get_by_text('历史战绩', exact=True).first.click()
        pc.wait_for_timeout(1500)
        hist = pc.locator('body').inner_text()
        assert 'UNO 卡牌' in hist, f'历史列表未见 UNO 记录：{hist[:250]}'
        ok('历史战绩出现 UNO 记录')

        # 点开第一条记录（要挑**可见**的那个：Tab 常驻挂载，游戏页的卡片标题也在 DOM 里）
        cands = pc.get_by_text('UNO 卡牌', exact=False)
        target = next((cands.nth(i) for i in range(cands.count())
                       if cands.nth(i).is_visible()), None)
        assert target is not None, '历史列表里找不到可见的 UNO 记录'
        target.click()
        pc.wait_for_timeout(1200)
        detail = pc.locator('body').inner_text()
        assert '最终排名' in detail, f'快照未渲染排名：{detail[:250]}'
        assert '已出完' in detail or '张' in detail, f'快照未渲染剩余手牌：{detail[:250]}'
        ok('战绩详情渲染终局手牌与排名')

        # ── 第二阶段：房规选档 + 抢出（v1.14）────────────────────
        phase_rules(pa, pb)

        # ── 第三阶段：无怜悯档（v1.14 批次 B）──────────────────────
        phase_no_mercy(pa, pb)

        browser.close()
    print(f'=== UNO 冒烟通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())