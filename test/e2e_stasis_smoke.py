#!/usr/bin/env python3
"""
Web 端浏览器级 E2E：深空休眠冒烟 v2（2-6 人合作塔防 —— 本项目唯一一个服务端 tick 的游戏）。

覆盖：建房/加入/准备/开始 → **跑动抢舱室阶段**（摇杆真的驱动角色、拖动屏幕真的平移相机、
气闸倒计时在走）→ 倒计时归零自动分配舱室并进入对局 → **整层楼平面图渲染**
（12 间舱室 / 12 扇门 / 自己的舱室有可建造格）→ 建哨戒炮 → 升气密门 →
**静默体沿通道寻路移动**（服务端 tick 在广播的唯一外部证据）→ 「已坚守」正计时在走 →
全站浮层列出 12 间舱室 → 弃守（二次确认）只结束本人、队友仍在局中 →
队友也弃守 → 全员失压结算 → 返回房间**不被弹回对局页** → 战绩落库 →
**换一张图真的再开一局**（v12 补：走廊里那把 suite 的几何画不出来时，地图盒是 0×0）。

最后那一轮是用户报的「双人套间打开全是黑的」的回归：在这之前，选图只验到
**大厅芯片**那一层，suite **一局都没开过** —— 于是「闸门的载荷叫 `cell`、
客户端的 `toDoor()` 读 `door`」这件事躲过了全部 30 步（见 `增强调研.md` §15）。

为什么这些断言放在浏览器层：
- 「跑动阶段摇杆驱动角色」与「拖动平移相机」是**交互**，只有真跑一遍才知道接线对不对；
- 「静默体在动」是**服务端 tick 在广播**的唯一外部可观测证据（单测里是手动调 advance）；
- 「弃守后返回房间不被弹回」踩过一次（房间页只看 status=playing 就自动跳对局页）。

服务端 tick 的正确性（时钟推进、任务生命周期、全员离线冻结、热重启重排）在
`server/test/test_ws_stasis.py` 里直接断言，那一层才看得见线路上的字节。

用法：先启动后端(50541)和前端 web(50540)，然后 python test/e2e_stasis_smoke.py
"""
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'sv_a_{TS}', f'sv_b_{TS}'
STEP = 0
# 跑动阶段 30 秒（stasis.RUN_MS）。这里是**等待上限**，不是固定 sleep。
RUN_WAIT = 45000
# 一格多少像素（`app/src/stasisMap.ts` 的 `TILE`）。地图**不缩放**，所以屏幕
# 位移与地图位移 1:1 —— 把某个格子的地图坐标换算成屏幕坐标要用它。
TILE = 44
AIRLOCK = (7, 0)          # `stasis_map.AIRLOCK`（静默体的入场口）


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


def body(page):
    return page.locator('body').inner_text()


def clock_seconds(page):
    """时钟读秒（M:SS）。跑动阶段是「气闸开启」的**倒计时**，对局中是
    「已坚守」的**正计时**（v8 起一局没有时长上限，没有终点可倒）。"""
    txt = page.locator('[data-testid="stasis-timer"]').first.inner_text()
    m = re.search(r'(\d+):(\d+)', txt)
    assert m, f'倒计时文案不是 M:SS：{txt!r}'
    return int(m.group(1)) * 60 + int(m.group(2))


def hero_box(page):
    el = page.locator('[data-testid="stasis-hero-me"]')
    return el.first.bounding_box() if el.count() else None


def hero_map_pos(page):
    """角色在**地图坐标系**里的位置 = 角色盒 − 地图盒。

    相机默认跟着自己走，所以角色的**屏幕**位置几乎恒定 —— 直接比较屏幕坐标
    量的是「相机有没有被夹住」而不是「人有没有动」（旧断言就是这么误判的：
    相机一放开夹取，位移就变成 0）。减掉地图盒才是相机无关的位移。
    """
    h = hero_box(page)
    m = page.locator('[data-testid="stasis-map"]').bounding_box()
    if not h or not m:
        return None
    return (h['x'] - m['x'], h['y'] - m['y'])


def entity_box(page):
    el = page.locator('[data-testid="stasis-entity"]')
    return el.first.bounding_box() if el.count() else None


def status_line(page):
    """顶部那行状态文案（静默体在游荡 / 正在逼近 XX / 正在砸 XX 的门）。

    它是**服务端 tick 驱动的**，所以可以用「它变没变」当作 tick 在广播的证据 ——
    比量位移稳：静默体走到门口就停下砸门，那时位移恒为 0。
    """
    el = page.locator('[data-testid="stasis-status"]')
    return el.first.inner_text() if el.count() else ''


def read_bio(page):
    """从底部资源条读自己的生物能。"""
    m = re.search(r'(\d+)\s*生物能', body(page))
    assert m, f'读不到生物能：{body(page)[:200]}'
    return int(m.group(1))


def my_room(page):
    """从底部提示条里读「你的舱室 X1」。"""
    m = re.search(r'你的舱室\s*([A-C][1-4])', body(page))
    return m.group(1) if m else None


def find_on_map(page, probes=((200, 330), (150, 400), (260, 300),
                              (110, 480), (300, 500), (200, 220))):
    """扫几个候选点，返回第一个**底下是地图本身**的坐标与全部候选的诊断串。

    判据只看 `stasis-map`：`none` 说明那一点底下没有带 testID 的祖先（手势链
    落在谁身上说不清），`stasis-door-*` / `stasis-tile-*` 说明那儿是门或格子。
    """
    tags = []
    for cx, cy in probes:
        tag = page.evaluate(
            "([x,y]) => { const e = document.elementFromPoint(x,y);"
            " return e ? (e.closest('[data-testid]')?.getAttribute('data-testid') || 'none') : 'none'; }",
            [cx, cy])
        tags.append(f'{cx},{cy}→{tag}')
        if tag == 'stasis-map':
            return (cx, cy), tags
    return None, tags


def geometry_dump(page):
    """视口 / 地图 / 角色的几何快照（只在断言失败时打印）。

    **每次都打全**：这套界面里「看不到地图」有四种完全不同的成因（没渲染 /
    被浮层盖住 / 相机夹错 / 地图尺寸为 0），只看一句「找不到下手点」定不了案。
    """
    return page.evaluate(
        "() => { const m = document.querySelector('[data-testid=\"stasis-map\"]');"
        " const v = document.querySelector('[data-testid=\"stasis-room-panel\"]');"
        " const r = (e) => e ? JSON.stringify(e.getBoundingClientRect()) : 'null';"
        " return 'inner=' + window.innerHeight + 'x' + window.innerWidth"
        " + ' scrollY=' + window.scrollY"
        " + ' mapStyle=' + (m ? m.style.transform + '|' + m.style.position : 'null')"
        " + ' mapRect=' + r(m) + ' panelRect=' + r(v)"
        " + ' heroRect=' + r(document.querySelector('[data-testid=\"stasis-hero-me\"]')); }")


def drag(page, x0, y0, x1, y1, steps=12):
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        page.wait_for_timeout(35)
    page.mouse.up()


def main():
    print('=== Web E2E: 深空休眠冒烟 v2（双人合作） ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        ctx_args = {'viewport': {'width': 390, 'height': 844}}
        pa = browser.new_context(**ctx_args).new_page()
        pb = browser.new_context(**ctx_args).new_page()
        for pg in (pa, pb):
            pg.set_default_timeout(15000)

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        # ── 建房 / 加入 / 开始 ────────────────────────────────
        goto_tab(pa, '游戏')
        pa.get_by_text('深空休眠', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        m = re.search(r'\b[A-Z0-9]{6}\b', body(pa))
        assert m, '未找到6位房间号'
        code = m.group(0)
        ok(f'A 创建深空休眠房间 {code}')

        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.wait_for_timeout(600)
        assert '2/6' in body(pa), f'房间人数应显示 2/6（v2 扩到 6 人），实际：{body(pa)[:200]}'

        # ── 房主选地图（v9）──────────────────────────────────
        # 这一条只有浏览器层看得见：服务端那条路（`room_setting` → `lobby` 广播）
        # 单测覆盖了，但「房主点了芯片、界面真的跟着换」是接线问题。
        # **切过去再切回来**：后面的断言全部按 dorm（25×30、气闸 (7,0)）写。
        #
        # v1.15 起芯片在「玩法设置」抽屉里。断言也跟着**升级了语义**：从「芯片存不
        # 存在」变成「**可点控件**存不存在」—— 非房主打开抽屉应该一个能点的设置控件
        # 都没有（不是「有但点不动」，RNW 下 disabled 等于点了没反应），
        # 但当前值仍然看得到。
        open_settings(pa)
        assert pa.locator('[data-testid="room-settings-map-suite"]').count() == 1, \
            '房主的抽屉里没有地图芯片'
        open_settings(pb)
        assert pb.locator('[data-testid^="room-settings-map-"]').count() == 0, \
            '非房主不该有可点的地图芯片'
        assert '标准货舱区' in body(pb), '非房主仍应看得到当前是哪张图'
        close_settings(pb)

        pa.locator('[data-testid="room-settings-map-suite"]').click()
        pa.wait_for_timeout(1000)
        assert '双人套间' in body(pa), f'切成 suite 后抽屉里没显示新图名：{body(pa)[:200]}'
        assert '外闸门' in body(pa) and '分工' in body(pa), \
            'suite 的地图说明没写出来（那张图的存在意义就是「两人共用一道闸门、适合分工」）'
        close_settings(pa)
        assert '双人套间' in body(pa), '抽屉关了但房间页摘要没更新'
        ok('房主把地图切成「双人套间」（suite），房间页跟着换了名字与说明')
        open_settings(pa)
        pa.locator('[data-testid="room-settings-map-dorm"]').click()
        pa.wait_for_timeout(1000)
        assert '标准货舱区' in body(pa), '切回 dorm 失败'
        close_settings(pa)
        ok('再切回默认图「标准货舱区」（后面的断言按 25×30 的 dorm 走）')

        pa.get_by_text('开始游戏', exact=True).click()
        ok('B 加入并准备，A 开始游戏（房间人数上限已是 6）')

        # ── 阶段一：跑动抢舱室 ────────────────────────────────
        for pg, who in ((pa, 'A'), (pb, 'B')):
            pg.locator('[data-testid="stasis-run-panel"]').wait_for(timeout=20000)
        assert '跑进一间空舱室' in body(pa), '跑动阶段的引导文案未渲染'
        # v4 的规则 1+2 就写在这条引导里：倒计时内躺下、躺下不能起身。
        # （真正「跑动阶段就能躺」由服务端单测锁；浏览器里要在 30 秒内自己走到
        #   床边才能点到按钮，脚本化太脆，这里只锁文案。）
        assert '躺下' in body(pa) and '起身' in body(pa), \
            f'跑动阶段引导应说明「躺下之后不能再起身」：{body(pa)[:200]}'
        ok('双方进入跑动抢舱室阶段（30 秒倒计时，引导里写明躺下不可逆）')

        # 摇杆真的驱动角色 —— 这是 v2 全新的输入路径，接线错了只会表现为「点了没反应」
        assert pa.locator('[data-testid="stasis-stick"]').count() == 1, \
            '跑动阶段就该有摇杆（那正是最需要移动的时候）'
        b0 = hero_map_pos(pa)
        assert b0 is not None, '地图上找不到自己的角色'
        box = pa.locator('[data-testid="stasis-stick"]').bounding_box()
        cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        drag(pa, cx, cy, cx + 34, cy + 34, steps=16)
        pa.wait_for_timeout(400)
        b1 = hero_map_pos(pa)
        moved = abs(b1[0] - b0[0]) + abs(b1[1] - b0[1]) if b1 else 0
        assert moved > 12, f'摇杆推了 16 步，角色只动了 {moved:.0f}px —— 移动输入没接上？'
        ok(f'摇杆驱动角色移动（位移 {moved:.0f}px，客户端预演 + 服务端权威校正）')

        # ── v4 规则 1：**倒计时里就能躺下**（用户要求的第一条）──────────
        # 这一步必须真的走进去躺下，不能只断言文案：B 出生在 (8,4)，B1 的门在
        # (8,2)→(9,2)、床在 (12,4)。用一个**比例控制器**推摇杆（每轮按屏幕坐标
        # 差推一次，直到贴近目标），比写死「推 0.6 秒再推 0.5 秒」稳得多 ——
        # 服务端积分 + 客户端预演都有延迟，写死时长必然在某个机器上偏掉。
        #
        # ⚠️ **这一步必须排在 A 的相机拖动之前**（v5 调过顺序）。跑动阶段只有
        # 30 秒，把 B 的走位排在一堆相机操作之后，会变成一场和倒计时的赛跑：
        # 一旦倒计时先结束，服务端会**随机分配一间舱室并把人传送过去**
        # （`_start_playing` 里没占房的人走这条路），脚本再走去 B1 的床边就
        # 永远等不到「躺下」按钮 —— 因为那时 B 的舱室已经不是 B1 了。
        # 症状是 `stasis-lie` 超时，而根因是时序。E2E 实测就是这么红的。
        def _push(pg, dx, dy):
            """朝 (dx, dy) 方向推一下摇杆。比例控制器的一步。"""
            sb = pg.locator('[data-testid="stasis-stick"]').bounding_box()
            sx, sy = sb['x'] + sb['width'] / 2, sb['y'] + sb['height'] / 2
            n = max(abs(dx), abs(dy), 1.0)
            # **必须推到摇杆的边沿**（`STICK_R` = 52）。摇杆有径向死区 0.12 与
            # 响应曲线 `t^1.25`（v5 手感改动）：推 40px（77% 量程）只给到
            # 69% 速度，而真人要跑起来就是把杆推到底。写成 40 会让这个
            # 比例控制器每轮走得比预期慢三成，最终在某个机器上超时。
            drag(pg, sx, sy, sx + 52 * dx / n, sy + 52 * dy / n, steps=6)
            pg.wait_for_timeout(240)

        def walk_to(pg, testid, tries=16, tol=12):
            """走到某个贴图**跟前**（屏幕上盒子中心对齐）。

            **只用来走到门口这类中间目标。** 终点判据不能是坐标 ——
            `stasis-hero-me` 那个盒子只装得下昵称文字（实测 14×19.5px），盒心离
            脚底十几像素，「脚本认为到了」与「服务端认为到了」是两回事。
            终点一律走 `walk_until_lie`。
            """
            for _ in range(tries):
                hb = pg.locator('[data-testid="stasis-hero-me"]').bounding_box()
                tb = pg.locator(f'[data-testid="{testid}"]').bounding_box()
                if not hb or not tb:
                    return False
                dx = (tb['x'] + tb['width'] / 2) - (hb['x'] + hb['width'] / 2)
                dy = (tb['y'] + tb['height'] / 2) - (hb['y'] + hb['height'] / 2)
                if abs(dx) < tol and abs(dy) < tol:
                    return True
                _push(pg, dx, dy)
            return False

        def walk_until_lie(pg, room, tries=26):
            """走到**「躺下」按钮真的出现**为止，判据是按钮本身（§10.5.3）。

            v6 起判据收紧成「人在休眠舱的九宫格之内」，而且**由服务端随 tick
            下发**（客户端不再自己算）。所以「走到床边」这件事现在必须真的走到，
            并且要等一个 tick（200ms）按钮才出现 —— 循环里每轮 240ms，正好覆盖。
            """
            bed = f'stasis-bed-{room}'
            for _ in range(tries):
                if pg.locator('[data-testid="stasis-lie"]').count():
                    return True
                hb = pg.locator('[data-testid="stasis-hero-me"]').bounding_box()
                tb = pg.locator(f'[data-testid="{bed}"]').bounding_box()
                if not hb or not tb:
                    return False
                _push(pg, (tb['x'] + tb['width'] / 2) - (hb['x'] + hb['width'] / 2),
                      (tb['y'] + tb['height'] / 2) - (hb['y'] + hb['height'] / 2))
            return pg.locator('[data-testid="stasis-lie"]').count() > 0

        def push_until_room(pg, want, dx, dy, tries=14):
            """朝一个**固定方向**推摇杆，直到提示条里的归属变成 `want`。

            `want=None` 表示「还没有舱室」（提示条那句「跑进一间空舱室就能占下」）。
            判据是**服务端下发的归属**（提示条那句「你的舱室 X」），不是坐标 ——
            同 `walk_until_lie`：客户端自己算的第二份判据必然与服务端漂移。
            归属随 tick（2.5 Hz）刷新，每轮 240ms 正好覆盖。
            """
            for _ in range(tries):
                if my_room(pg) == want:
                    return True
                _push(pg, dx, dy)
            return my_room(pg) == want

        # 先走到 B1 的门（跨舱壁只能走门那一对格子），再走到床边。
        # **v6 起「走到床边」是必须的** —— 判据从「整间舱室」收回成「休眠舱的
        # 九宫格」，进门就躺下的老路走不通了。
        assert walk_to(pb, 'stasis-door-B1', tries=10), 'B 走不到 B1 的门（摇杆/寻路？）'
        assert push_until_room(pb, 'B1', 1.0, 0.0, tries=8), \
            f'B 走进 B1 却没占下它（提示条：{body(pb)[:120]}）'
        # ── 用户实测报的那条：**躺下之前舱室可以随便换** ──────────────
        # 原来只在「手里没有舱室」时才认领，于是走出去的人**一直攥着第一间房**
        # （`p["room"]` 不释放），换到别的房间后 `_can_lie` 判的还是旧房，
        # 「躺下」按钮永远不出现。这里用「走出去 → 归属变空 → 走回来 → 重新占下」
        # 锁住这条（B1 的门开在西墙上，进/出都是横向，推杆方向固定）。
        assert push_until_room(pb, None, -1.0, 0.0, tries=8), \
            f'B 走出了 B1 却还占着它 —— 舱室没跟着人走（提示条：{body(pb)[:120]}）'
        assert push_until_room(pb, 'B1', 1.0, 0.0, tries=8), \
            f'B 走回 B1 却没重新占下（提示条：{body(pb)[:120]}）'
        ok('跑动阶段舱室跟着人走：走出去让出、走回来重新占下（躺下之前可随便换）')
        # 走位失败时**把「B 实际占了哪间」写进报错**：占错房的话按钮永远不出现，
        # 而失败会落在这里、看不出根因（判据本身是按钮，见 `walk_until_lie`）。
        assert walk_until_lie(pb, 'B1'), \
            f'B 走进了舱室却走不到床边（按钮没出现；B 的舱室是 {my_room(pb)}）'
        assert my_room(pb) == 'B1', f'B 应该占下 B1，实际 {my_room(pb)}'
        pb.locator('[data-testid="stasis-lie"]').wait_for(timeout=8000)
        pb.locator('[data-testid="stasis-lie"]').click()
        pb.wait_for_timeout(400)
        assert '躺好了' in body(pb), f'倒计时里躺下后引导应变成「躺好了」：{body(pb)[:200]}'
        rb0 = read_bio(pb)
        pb.wait_for_timeout(2000)
        rb1 = read_bio(pb)
        # **倒计时里躺着也产资源**：不这样的话「躺下」在这个阶段就又是一个
        # 「点了没反应」的动作（v3 用户报过的那一类）。
        assert rb1 > rb0, f'跑动阶段躺着没产资源（{rb0} → {rb1}）'
        assert pb.locator('[data-testid="stasis-run-panel"]').count() == 1, \
            '此时应该还在跑动阶段（这一步要验的就是「倒计时结束前就能躺」）'
        ok(f'倒计时结束前就能躺下，且躺着已在产生物能（{rb0} → {rb1}）')


        # 拖动屏幕平移相机 —— 「滑动查看地图上其他位置」是用户明确要的能力。
        # **起点不能写死坐标**：地图上的门与休眠舱是 TouchableOpacity，落在谁身上
        # 取决于当前相机位置。这里先问浏览器「这个点底下是什么」，挑一个空地起步；
        # 下面再单独验一次「从门上开始拖」——那才是真人的常见起手。
        def map_box():
            return pa.locator('[data-testid="stasis-map"]').bounding_box()

        def pan_from(x, y, dx, dy):
            a = map_box()
            drag(pa, x, y, x + dx, y + dy, steps=14)
            pa.wait_for_timeout(320)
            c = map_box()
            return abs(c['x'] - a['x']) + abs(c['y'] - a['y'])

        # 找一块**落在地图自己身上**的空地：从视口里扫几条候选。
        # 只认 `stasis-map`（不认 `none`）—— `none` 说明那一点底下是个没有
        # testID 祖先的元素，手势链落在谁身上说不清（实测过「拖了 14 步只动 3px」，
        # 那 3px 是相机阻尼的余量，说明 responder 压根没授给地图）。
        safe, tags = find_on_map(pa)
        # 失败时把「每个候选点底下是谁」和一份几何快照一起打出来 —— 这条断言原来
        # 只写一句「地图没铺满？」，真出事时分不清是「地图没渲染」「被别的东西盖住」
        # 还是「地图被相机推到屏幕外」（三种的修法完全不同）。**v9 那个「相机被
        # 夹到屏幕外」的 bug 就是靠这份快照一眼定位的**（见 `增强调研.md` §13.8）。
        assert safe, ('视口里找不到一块落在地图上的空地下手：'
                      + ' / '.join(tags) + f' ｜ 地图盒 {map_box()} ｜ {geometry_dump(pa)}')
        moved_cam = pan_from(safe[0], safe[1], -110, -70)
        # 拖一次没接上就再拖一次（**拖动是幂等的**，重试不改语义）。这里不放松
        # 阈值：`moved_cam` 只有个位数 px 时是「手势没授给地图」，不是「拖得少」。
        if moved_cam <= 20:
            moved_cam = pan_from(safe[0], safe[1], -110, -70)
        assert moved_cam > 20, f'在空地 {safe} 拖了 14 步只平移 {moved_cam:.0f}px —— 相机没接上拖动？'
        ok(f'拖动屏幕平移相机（空地 {safe} 起步，地图平移 {moved_cam:.0f}px）')

        # 从**门**上起手也要能拖 —— 门是 TouchableOpacity，按下就抢 responder，
        # 父层必须用 capture 阶段才抢得回来（否则表现是「按在门上拖不动地图」）。
        door_box = None
        doors_all = pa.locator('[data-testid^="stasis-door-"]')
        for i in range(doors_all.count()):
            bb = doors_all.nth(i).bounding_box()
            if bb and 0 < bb['x'] < 370 and 170 < bb['y'] < 600:
                door_box = (bb['x'] + bb['width'] / 2, bb['y'] + bb['height'] / 2)
                break
        assert door_box, '视口里看不到任何一扇门'
        moved_door = pan_from(door_box[0], door_box[1], -110, -70)
        assert moved_door > 20, f'从门上起手拖只平移 {moved_door:.0f}px —— 门把手势吃掉了？'
        ok(f'从气密门上起手拖也能平移相机（{moved_door:.0f}px，capture 阶段抢回手势）')

        pa.locator('[data-testid="stasis-recenter"]').click()
        pa.wait_for_timeout(400)

        # ── 倒计时走完 → 自动分配舱室 ─────────────────────────
        # **不要在这里固定 sleep**：跑动阶段 30 秒，固定等待会落在错误的时间点。
        # 让 `wait_for` 自己卡在「提示条出现舱室号」这一刻。
        for pg, who in ((pa, 'A'), (pb, 'B')):
            pg.wait_for_function(
                "() => /你的舱室\\s*[A-C][1-4]/.test(document.body.innerText)",
                timeout=RUN_WAIT)
        room_a, room_b = my_room(pa), my_room(pb)
        assert room_a and room_b, f'跑动阶段结束没分到舱室（{room_a} / {room_b}）'
        assert '已坚守' in body(pa), f'这一栏应是「已坚守」（v8 起一局没有时长上限）：{body(pa)[:200]}'
        ok(f'倒计时归零，双方自动分配舱室（A={room_a}，B={room_b}）')

        # ── v4 规则：**没躺下就不能建造**（A 此刻还站着，正好验这条）──────
        # 客户端先拦一道并说清原因；服务端 `_require_lying` 是同一规则的权威。
        # 只断言「建造面板没打开」——面板开了才点、点了才被拒，就是用户报过的
        # 「点了没反应」那一类。
        # ⚠️ **要等一个 tick**：v6 起按钮的显隐是服务端随 tick 下发的
        # （`players[uid].lie`），`_start_playing` 把人放到床心之后，
        # 广播 `stasis_state` 与下一个 tick 之间最多差 200ms。
        pa.locator('[data-testid="stasis-lie"]').wait_for(timeout=6000)
        tile = None
        tiles = pa.locator('[data-testid^="stasis-tile-"]')
        for i in range(tiles.count()):
            bb = tiles.nth(i).bounding_box()
            if bb and 40 < bb['x'] < 350 and 200 < bb['y'] < 560:
                tile = bb
                break
        assert tile, 'A 的视口里看不到自己舱室的空格'
        pa.mouse.click(tile['x'] + tile['width'] / 2, tile['y'] + tile['height'] / 2)
        pa.wait_for_timeout(500)
        assert pa.locator('[data-testid="stasis-build-turret"]').count() == 0, \
            '没躺下却打开了建造面板 —— 客户端没拦住（服务端也会拒，但玩家会白点一次）'
        ok('没躺下时点空格打不开建造面板（v4：躺下是建造与升级的前置）')

        # ── 整层楼平面图渲染 ──────────────────────────────────
        doors = pa.locator('[data-testid^="stasis-door-"]').count()
        assert doors == 12, f'12 间舱室应有 12 扇气密门，实际 {doors}'
        beds = pa.locator('[data-testid^="stasis-bed-"]').count()
        assert beds == 12, f'12 间舱室应有 12 张休眠舱，实际 {beds}'
        slots = pa.locator('[data-testid^="stasis-tile-"]').count()
        # 自己舱室的可建造格 = 房型格数 − 1（休眠舱）。房型每局随机（16~20 格），
        # 所以只能断言落在区间里。
        assert 13 <= slots <= 19, f'自己舱室的可建造格应在 13~19（房型 16~20 减休眠舱），实际 {slots}'
        ok(f'整层楼平面图渲染：12 扇门 / 12 张床 / 自己舱室 {slots} 个可建造格')

        # ── 躺下才产资源（用户报的核心缺陷）────────────────────
        # ① 归位之后是**站着**的：站着零收入，界面上要明说。
        # A 是被 `_start_playing` 直接放到**床心**的，所以 v6 的九宫格判据天然满足
        # —— 这一步同时锁住「自动归位落在床边而不是门口」。
        pa.locator('[data-testid="stasis-lie"]').wait_for(timeout=6000)
        bio0 = read_bio(pa)
        pa.wait_for_timeout(1500)
        assert read_bio(pa) == bio0, '还没躺下就在产生物能？'
        ok(f'站在床边不产资源（生物能恒为 {bio0}），「躺下」按钮已出现')

        # ② 点「躺下」→ 生物能开始涨，**而且再也起不来**（v4 规则 2）
        pa.locator('[data-testid="stasis-lie"]').click()
        pa.wait_for_timeout(2500)
        bio1 = read_bio(pa)
        assert bio1 > bio0, f'躺下后生物能没涨（{bio0} → {bio1}）'
        assert pa.locator('[data-testid="stasis-lie"]').count() == 0, \
            '躺下之后不该还留着「躺下」按钮'
        # **「起身」这条路整个删掉了**（v4）：按钮、面板里的入口、协议动作三处
        # 都不该存在。少了任何一处，玩家就会以为自己还能下床。
        assert pa.locator('[data-testid="stasis-stand"]').count() == 0, \
            '躺下后不该出现「起身」按钮 —— v4 起躺下不可逆'
        ok(f'点「躺下」开始产生物能（{bio0} → {bio1}），且没有「起身」这条路')

        # ③ 躺着不能移动：推摇杆，角色不动
        b0 = hero_map_pos(pa)
        box = pa.locator('[data-testid="stasis-stick"]').bounding_box()
        cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        drag(pa, cx, cy, cx, cy + 40, steps=10)
        pa.wait_for_timeout(700)
        b1 = hero_map_pos(pa)
        moved_lying = abs(b1[0] - b0[0]) + abs(b1[1] - b0[1]) if b1 else 999
        assert moved_lying < 6, f'躺下后推摇杆角色动了 {moved_lying:.0f}px —— 躺着不该能动'
        ok('躺着推摇杆角色纹丝不动（躺下之后整局都不能动）')

        # ④ 点头像底下的床也能开面板（**角色不能吃掉点击** —— 人正躺在床上）
        pa.locator(f'[data-testid="stasis-bed-{room_a}"]').click()
        pa.locator('[data-testid="stasis-pod-upgrade"]').wait_for(timeout=5000)
        # 产率标注必须与服务端同口径（曾经下标错位：Lv2 标 1 实产 2）
        assert '每秒 1 生物能' in body(pa), f'Lv1 产率应标注 1/秒：{body(pa)[:300]}'

        # 升一级床（25 生物能，开局就有）→ 门槛立刻现形：升床要气密门跟上。
        # **门槛必须写在弹窗里**：用户原话「应该有个门的等级的前置条件，但是
        # 升级弹窗中看不出来，生物能够了也没法升级」。
        pa.locator('[data-testid="stasis-pod-upgrade"]').click()
        pa.wait_for_timeout(1200)
        assert 'Lv2' in body(pa), f'休眠舱应升到 Lv2：{body(pa)[:300]}'
        # v4 的门槛是**间断的**：床 2 → 3 需要铁门 2（门现在是铁门 1）。
        # 文案从「需气密门 Lv2」改成门自己的名字（铁门 2 / 钢门 1 / 振金门 3）。
        assert '需铁门 2' in body(pa), \
            f'床 Lv2 之后升 Lv3 需要铁门 2，弹窗必须写明：{body(pa)[:300]}'
        pa.locator('[data-testid="stasis-sheet-close"]').click()
        pa.wait_for_timeout(300)
        ok('点休眠舱能打开升级弹窗（角色不再吞点击），且门槛「需铁门 2」写在弹窗里')

        # ── 静默体沿通道寻路移动 ──────────────────────────────
        # 服务端 tick 在广播的外部证据。**判据不能用「这几秒里它位移了多少」**：
        # 它走到门口就停下砸门（一停能停几分钟），采样窗口刚好落在「已经到门口」
        # 上就会量到 0 —— 那是一条随机的假失败（实测踩过两次，第一版还把
        # 「状态行变没变」当兜底，可状态也早就变完了，同样会误报）。
        #
        # 确定性的判据是：**它已经不在气闸上了**。它出生在 `AIRLOCK = (7,0)`，
        # 而「离开气闸」只能靠 tick 一步步积分出来 —— 位置对不对由下面的地图盒
        # 换算，状态行必须显示它正在做事（逼近/撞门/追人），两者都是 tick 的产物。
        ent = entity_box(pa)
        assert ent is not None, '地图上找不到静默体'
        mb = pa.locator('[data-testid="stasis-map"]').bounding_box()
        # 气闸格 (7,0) 的中心在地图坐标系里是 ((7+0.5)*TILE, 0.5*TILE)，
        # 减掉地图盒就是屏幕坐标（地图不缩放，1:1）。
        ax = mb['x'] + (AIRLOCK[0] + 0.5) * TILE
        ay = mb['y'] + (AIRLOCK[1] + 0.5) * TILE
        ecx, ecy = ent['x'] + ent['width'] / 2, ent['y'] + ent['height'] / 2
        off_airlock = abs(ecx - ax) + abs(ecy - ay)
        line = status_line(pa)
        acting = any(w in line for w in ('逼近', '撞', '追'))
        assert off_airlock > TILE or acting, \
            f'静默体还待在气闸上、状态行也没显示它在做事（离气闸 {off_airlock:.0f}px，' \
            f'状态「{line}」）—— 服务端 tick 没在广播？'
        ok(f'静默体已离开气闸（{off_airlock:.0f}px）且状态由服务端下发：「{line}」')

        # ── 服务端 tick 真的在广播（游戏内时钟在走）───────────
        t1 = clock_seconds(pa)
        pa.wait_for_timeout(3000)
        t2 = clock_seconds(pa)
        assert t2 > t1, f'「已坚守」3 秒没走（{t1}s → {t2}s）—— 服务端 tick 没在广播？'
        assert t1 < 60, f'对局刚开始，「已坚守」不该已经很大（实际 {t1}s）'
        ok(f'「已坚守」正计时在走（{t1}s → {t2}s，游戏内时钟由服务端 tick 累加）')

        # ── 建造哨戒炮（8 生物能，开局 25）────────────────────
        slot = pa.locator('[data-testid^="stasis-tile-"]').first
        cell_id = slot.get_attribute('data-testid')
        slot.click()
        pa.locator('[data-testid="stasis-build-turret"]').wait_for(timeout=5000)
        assert '哨戒炮' in body(pa), '建造菜单未列出哨戒炮'
        # **四座矿是四个独立的建造条目**（v5）。菜单里少一个就说明拆分没落地，
        # 或者服务端的 kind 名与客户端对不上（那种情况点了会静默收到一条 error）。
        for mine, label in (('copper', '铜矿'), ('silver', '银矿'),
                            ('gold', '金矿'), ('diamond', '钻石矿')):
            assert pa.locator(f'[data-testid="stasis-build-{mine}"]').count() == 1, \
                f'建造菜单缺少 {label}（{mine}）'
        assert '培养槽' not in body(pa), '建造菜单里还留着旧的「培养槽」条目'
        ok('建造菜单有四个独立的矿条目（铜/银/金/钻石），没有旧的「培养槽」')

        # ── 控场四件也在菜单里（v13）──────────────────────────
        # **只验「菜单接线」**：四件的效果在引擎里，靠 6 条单测锁（`TestControls`）。
        # 这里要抓的是另一类事故 —— 服务端的 kind 名与客户端对不上，玩家点下去
        # 只会静默收到一条 error（界面上「点了没反应」）。
        for kind, label in (('shield', '应急力场'), ('binder', '束缚弹射器'),
                            ('cryo', '低温阱'), ('accelerator', '粒子加速器')):
            assert pa.locator(f'[data-testid="stasis-build-{kind}"]').count() == 1, \
                f'建造菜单缺少 {label}（{kind}）'
        assert '应急力场' in body(pa) and '束缚弹射器' in body(pa), \
            '控场四件的名字没渲染出来（只验了 testID）'
        ok('建造菜单也有控场四件（应急力场/束缚弹射器/低温阱/粒子加速器）')
        pa.locator('[data-testid="stasis-build-turret"]').click()
        pa.wait_for_timeout(1200)
        key = cell_id.replace('stasis-tile-', '')       # "7-6"
        built = pa.locator(f'[data-testid="stasis-building-{key}"]').count()
        assert built == 1, f'建造后该格应变成建筑（{key}），实际没有'
        # 建造完该格的「+」提示要消失（同一格不能既空又有建筑）
        assert pa.locator(f'[data-testid="stasis-tile-{key}"]').count() == 0, \
            '建造后那一格还留着空格提示'
        ok(f'点空格 {key} 建造哨戒炮，该格变成建筑（+ 提示消失）')

        # ── 点炮台 → 画出攻击范围圈（v5）──────────────────────
        assert pa.locator('[data-testid="stasis-range"]').count() == 0, \
            '没点炮台时不该画攻击范围圈'
        pa.locator(f'[data-testid="stasis-building-{key}"]').click()
        pa.locator('[data-testid="stasis-range"]').wait_for(timeout=5000)
        assert '射程' in body(pa), '炮台面板里没写射程'
        ok('点炮台弹出面板并画出攻击范围圈（射程 4 格）')
        pa.locator('[data-testid="stasis-sheet-close"]').click()
        pa.wait_for_timeout(400)
        assert pa.locator('[data-testid="stasis-range"]').count() == 0, \
            '关掉面板后范围圈应该消失'

        # ── 升级气密门（16 生物能）────────────────────────────
        # ⚠️ **门被砸时会横向抖动**（`doorShake`，主循环每帧写一个 2.2px 的正弦位移），
        # 而 Playwright 的 `click` 默认要求元素「连续两帧位置不变」—— 抖着的门**永远
        # 不满足**，于是这里会 15 秒超时（报 `element is not stable`）。真人点得动
        # （2.2px 而已），所以这是测试的判据问题，不是产品的：这两次点击加 `force`。
        pa.locator(f'[data-testid="stasis-door-{room_a}"]').click(force=True)
        pa.locator('[data-testid="stasis-door-upgrade"]').wait_for(timeout=5000)
        assert '升级' in body(pa), '气密门面板未给出升级按钮文案'
        pa.locator('[data-testid="stasis-door-upgrade"]').click()
        pa.wait_for_timeout(1200)
        pa.locator('[data-testid="stasis-sheet-close"]').click()
        pa.wait_for_timeout(400)
        pa.locator(f'[data-testid="stasis-door-{room_a}"]').click(force=True)
        pa.locator('[data-testid="stasis-door-upgrade"]').wait_for(timeout=5000)
        # v4：门按原型的档位名显示（铁门 1-5 → 钢门 1-5 → 振金门 1-3），
        # 不再是「Lv2」。
        assert '铁门 2' in body(pa), f'升级后门应是铁门 2：{body(pa)[:250]}'
        pa.locator('[data-testid="stasis-sheet-close"]').click()
        pa.wait_for_timeout(300)
        ok('气密门升级到铁门 2（升级即满血）')

        # ── 全站浮层：12 间舱室全列出来 ───────────────────────
        pa.locator('[data-testid="stasis-wholemap"]').click()
        pa.locator('[data-testid^="stasis-overlay-door-"]').first.wait_for(timeout=5000)
        rows = pa.locator('[data-testid^="stasis-overlay-door-"]').count()
        assert rows == 12, f'全站浮层应列出 12 间舱室，实际 {rows}'
        pa.locator('[data-testid="stasis-overlay-close"]').click()
        pa.wait_for_timeout(400)
        ok('全站浮层列出 12 间舱室（含队友的门，可远程修补）')

        # ── 弃守只结束本人，遗产分给队友 ──────────────────────
        pa.locator('[data-testid="stasis-surrender"]').click()
        pa.locator('[data-testid="stasis-confirm"]').wait_for(timeout=5000)
        assert '确认弃守' in body(pa), '弃守未走二次确认'
        pa.locator('[data-testid="stasis-confirm"]').click()
        pa.wait_for_timeout(1800)
        assert pa.locator('[data-testid="stasis-out-overlay"]').count() == 1, \
            f'弃守后应显示出局浮层：{body(pa)[:250]}'
        assert '遗产' in body(pa), '出局浮层未说明遗产已分给队友'
        assert pa.locator('[data-testid="stasis-back-room"]').count() == 1, '出局后应有「返回房间」按钮'
        ok('A 弃守（二次确认）→ 只结束本人，遗产分给队友，B 仍在局中')

        # B 那边不能因为队友弃守而终局
        assert '已坚守' in body(pb), '队友弃守不应结束整局'
        assert '全员失压' not in body(pb), '队友弃守被误判成全员失压'
        ok('B 的对局继续（合作塔防里队友弃守 ≠ 团灭）')

        # ── 全员失压 → 结算 ───────────────────────────────────
        pb.locator('[data-testid="stasis-surrender"]').click()
        pb.locator('[data-testid="stasis-confirm"]').wait_for(timeout=5000)
        pb.locator('[data-testid="stasis-confirm"]').click()
        pb.locator('[data-testid="stasis-result-back"]').wait_for(timeout=12000)
        # **v8 改了结算文案**：救援抵达这条胜利条件已删，所以标题不再写「守住了」，
        # 而是「静默体被击杀 / 任务失败」；原因那一行写的是「全员舱室失压」。
        assert '任务失败' in body(pb), f'结算标题应是「任务失败」：{body(pb)[:250]}'
        assert '全员舱室失压' in body(pb), f'结算未说明原因：{body(pb)[:250]}'
        ok('全员失压 → 结算弹窗（标题「任务失败」，原因「全员舱室失压」）')

        # ── 返回房间不被弹回对局页 ────────────────────────────
        pb.locator('[data-testid="stasis-result-back"]').click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=10000)
        pb.wait_for_timeout(2000)
        assert pb.locator('[data-testid="stasis-room-panel"]').count() == 0, \
            '返回房间后被自动弹回对局页（房间页只看 status=playing 就跳转）'
        assert '准 备' in body(pb), '回房间后 ready 未复位'
        ok('返回房间后停在大厅、ready 已复位（未被弹回对局页）')

        # ── 战绩落库 ──────────────────────────────────────────
        leave = pb.get_by_text('退出房间', exact=True)
        if leave.count():
            leave.first.click()
            pb.wait_for_timeout(1200)
        goto_tab(pb, '我的')
        pb.get_by_text('历史战绩', exact=True).first.click()
        pb.wait_for_timeout(1500)
        hist = body(pb)
        assert '深空休眠' in hist, f'历史列表未见深空休眠记录：{hist[:250]}'
        cands = pb.get_by_text('深空休眠', exact=False)
        target = next((cands.nth(i) for i in range(cands.count())
                       if cands.nth(i).is_visible()), None)
        assert target is not None, '历史列表里找不到可见的深空休眠记录'
        target.click()
        pb.wait_for_timeout(1200)
        detail = body(pb)
        assert '失压' in detail, f'快照未渲染结局原因：{detail[:250]}'
        ok('战绩落库：历史列表出现深空休眠记录且快照可渲染')

        # ── 换一张图**真的开一局**：几何画得出来吗（v12 修的「双人套间全是黑的」）
        #
        # 上面那条「房主选地图」只证明「芯片点得动、房间页文案跟着换」—— 它**从没在
        # suite 上开过一局**，于是「闸门的几何在载荷里叫 `cell`、而客户端的
        # `toDoor()` 读的是 `door`」这件事一路躲过了 30 步断言：`buildMap` 抛
        # `TypeError`、`socket.ts` 的 catch 又把它静默吞掉，界面上唯一的表现是
        # **地图 0×0（全黑）**，而跑动阶段的面板、倒计时、摇杆全都照常工作。
        #
        # 这一轮**顺便**锁住「一局打完回大厅可以换图再开」（房间级设置本来就该在
        # 局间改），比另起一个房间少一半步骤。
        # A 那一边**这时整局已经结束了**（B 也弃守 ⇒ 全员失压）：屏幕上同时挂着
        # **出局浮层**（弃守那一刻留下的）与盖在它上面的**结算弹窗**，两个按钮的
        # testID 不同、**点击必须落在结算那一个**（另一个被盖住，Playwright 会报
        # 「intercepts pointer events」）。所以先等「两者之一」出现，再按存在的挑。
        pa.locator('[data-testid="stasis-result-back"], [data-testid="stasis-back-room"]'
                   ).first.wait_for(timeout=15000)
        pa.wait_for_timeout(600)
        back = pa.locator('[data-testid="stasis-result-back"]')
        (back if back.count() else pa.locator('[data-testid="stasis-back-room"]')).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=10000)
        # pb 这会儿停在**历史战绩的详情页**（对局屏式的全屏页，底部 Tab 不在视口里），
        # 重新进一次首页回到主 Tab 最省事（登录态在 localStorage 里，不用重新注册）。
        pb.goto(BASE)
        pb.get_by_text('游戏', exact=True).last.wait_for(timeout=20000)
        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_text('准 备', exact=True).click()
        pa.wait_for_timeout(600)
        open_settings(pa)
        pa.locator('[data-testid="room-settings-map-suite"]').click()
        pa.wait_for_timeout(900)
        assert '双人套间' in body(pa), '第二局切图失败'
        close_settings(pa)
        pa.get_by_text('开始游戏', exact=True).click()
        pa.locator('[data-testid="stasis-run-panel"]').wait_for(timeout=30000)
        pa.wait_for_timeout(1500)

        # 判据是**地图盒的像素尺寸**（31×24 格 × 44px）—— 「图是黑的」的成因有四种
        # （没渲染 / 被浮层盖住 / 相机夹错 / 尺寸为 0），而 `buildMap` 抛异常那一种
        # 唯一的外部特征就是**尺寸为 0**（地图盒的 style 里写着 `width: 0px`）。
        mb = pa.locator('[data-testid="stasis-map"]').bounding_box()
        assert mb and abs(mb['width'] - 31 * TILE) < 2 and abs(mb['height'] - 24 * TILE) < 2, \
            f'suite 的地图盒尺寸不对（应是 {31 * TILE}×{24 * TILE}）：{mb} ｜ {geometry_dump(pa)}'
        # 只画了「空地图」时这三种元素一个都不会有（它们都要 `map.roomIds` / `map.gates`）
        assert pa.locator('[data-testid^="stasis-door-"]').count() == 12, 'suite 没画出 12 扇房门'
        assert pa.locator('[data-testid^="stasis-bed-"]').count() == 12, 'suite 没画出 12 张休眠舱'
        assert pa.locator('[data-testid^="stasis-gate-"]').count() == 6, \
            'suite 没画出 6 扇共用外闸（房门的载荷形状对了、闸门的那一份没有）'
        assert '外闸' in body(pa), '共用闸门没打上「外闸」标签（它和自家房门必须一眼分得开）'
        ok(f'suite 图真的画出来了（地图盒 {31 * TILE}×{24 * TILE}、12 门 / 6 外闸 / 12 床）')

        # 收尾：两边的弃守在跑动阶段也能点（本局不打了）。
        # ⚠️ **必须断言真的生效**：这一步原来只点按钮、不看结果，于是
        # `stasis.surrender` 在跑动阶段抛 `ValueError("对局还没开始")` 那一轮
        # （v1.16 修的用户实报 bug）**照样绿**。现在等结算浮层出现。
        for pg in (pa, pb):
            pg.locator('[data-testid="stasis-surrender"]').click()
            pg.locator('[data-testid="stasis-confirm"]').wait_for(timeout=5000)
            pg.locator('[data-testid="stasis-confirm"]').click()
            pg.wait_for_timeout(800)
        pa.locator('[data-testid="stasis-result-back"]').wait_for(timeout=15000)
        ok('跑动阶段也能弃守（一局没打完就收摊，不留 playing 的空房）')

        browser.close()
    print(f'=== 深空休眠冒烟 v2 通过：{STEP} 步 ===')


if __name__ == '__main__':
    sys.exit(main())
