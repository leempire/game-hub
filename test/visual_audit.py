#!/usr/bin/env python3
"""
本地视觉审计（替代云AI视觉测试）：移动视口逐页检查 + 截图供人工/AI 目视。
检查项：横向溢出 / emoji 残留 / 可见文字最小字号 / Tab栏占位符与重影 / 弹窗视口内完整 / 点击反馈存在
       / 源码静态扫描（浏览器专属 API 不得进入原生路径，如 window.confirm）。
用法：前后端启动后 python test/visual_audit.py；截图输出 /tmp/audit_*.png
"""
import json
import os
import pathlib
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
REPO = pathlib.Path(__file__).resolve().parent.parent
APP_SRC = REPO / 'app/src'
APP_ENTRY = REPO / 'app/App.tsx'
SERVER_CONFIG = REPO / 'server/config/config.json'


def admin_credentials():
    """管理员账号：环境变量优先，其次 server/config/config.json。

    仓库里**没有默认口令**（那是公开仓库里的已知凭据），所以不再写死 admin/admin123。
    """
    cfg = {}
    if SERVER_CONFIG.exists():
        cfg = json.loads(SERVER_CONFIG.read_text(encoding='utf-8')).get('admin', {})
    user = os.environ.get('ADMIN_USERNAME') or cfg.get('username') or ''
    pwd = os.environ.get('ADMIN_PASSWORD') or cfg.get('password') or ''
    if not (user and pwd):
        sys.exit('未找到管理员账号：请设置 ADMIN_USERNAME/ADMIN_PASSWORD，'
                 f'或写进 {SERVER_CONFIG} 的 admin 段')
    return user, pwd
# 浏览器专属交互 API：原生端 undefined，调用即崩（历史 bug：window.confirm）
WEB_ONLY_API = re.compile(r'window\.(confirm|alert|prompt)|(?<![\w.$])(alert|confirm|prompt)\s*\(')
# DOM 事件/对象 API：仅允许出现在 backGuard.tsx（唯一 Web 返回枢纽，内部 IS_WEB 守卫）。
# 历史 bug：对局屏裸调 window.addEventListener('gh:confirm-leave') → 原生挂载即 TypeError 白屏
DOM_API = re.compile(r'window\.(addEventListener|removeEventListener|dispatchEvent)|(?<![\w.$])document\.|new\s+CustomEvent')
DOM_API_ALLOWED_FILES = {'backGuard.tsx'}
EMOJI = re.compile(r'[\U0001F000-\U0001FAFF' + chr(0x2600) + '-' + chr(0x27BF) + chr(0xFE0F) + chr(0x2B00) + '-' + chr(0x2BFF) + ']')
RESULTS = []


def check(page_name, item, ok, detail=''):
    RESULTS.append((page_name, item, ok, detail))
    print(f"  {'✓' if ok else '✗'} [{page_name}] {item} {detail if not ok else ''}")


def audit_page(pg, name):
    """通用页面审计：溢出/emoji/最小字号。"""
    m = pg.evaluate("""() => ({
        hOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        texts: [...document.querySelectorAll('*')]
            .filter(e => e.children.length === 0 && e.textContent.trim() && e.checkVisibility()
                && !e.closest('svg'))  // SVG 内文字属矢量图形（如图标里的"2048"），不适用 UI 字号规则
            .map(e => ({ t: e.textContent.trim(), fs: parseFloat(getComputedStyle(e).fontSize) })),
    })""")
    check(name, '无横向溢出', m['hOverflow'] <= 0, f"overflow={m['hOverflow']}px")
    emojis = [t['t'] for t in m['texts'] if EMOJI.search(t['t'])]
    check(name, '无emoji残留', not emojis, str(emojis[:3]))
    small = [t for t in m['texts'] if t['fs'] < 10]
    check(name, '可见文字≥10px', not small, str([(t['t'][:8], t['fs']) for t in small[:3]]) if small else '')
    pg.screenshot(path=f'/tmp/audit_{name}.png')


def audit_tabbar(pg):
    info = pg.evaluate("""() => {
        const leaves = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && e.checkVisibility());
        const labels = leaves.filter(e => ['游戏','房间','社交','我的'].includes(e.textContent.trim()));
        return {
            dots: leaves.filter(e => e.textContent.trim() === '•').length,
            latin: labels.filter(e => /[A-Za-z]/.test(e.parentElement.textContent)).length,
            fs: labels.map(e => parseFloat(getComputedStyle(e).fontSize)),
        };
    }""")
    check('Tab栏', '无•占位符', info['dots'] == 0)
    check('Tab栏', '无英文路由名重影', info['latin'] == 0)
    check('Tab栏', '标签≥12px', all(f >= 12 for f in info['fs']), str(info['fs']))


def audit_source_static():
    """静态扫描前端源码：禁止浏览器专属 API 进入原生路径（undefined is not a function → 白屏）。"""
    files = [APP_ENTRY] + sorted(APP_SRC.rglob('*.ts')) + sorted(APP_SRC.rglob('*.tsx'))
    bad = []
    bad_dom = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding='utf-8').splitlines(), 1):
            code = line.split('//')[0]  # 粗暴剔除行尾注释，避免注释误报
            if WEB_ONLY_API.search(code):
                bad.append(f'{f.name}:{i}: {line.strip()[:60]}')
            if f.name not in DOM_API_ALLOWED_FILES and DOM_API.search(code):
                bad_dom.append(f'{f.name}:{i}: {line.strip()[:60]}')
    check('源码静态', '无浏览器专属confirm/alert/prompt', not bad, str(bad[:3]))
    check('源码静态', 'DOM事件API仅在backGuard', not bad_dom, str(bad_dom[:3]))


def audit_back_wiring():
    """静态对账：**每个对局屏都必须接上「返回双路径」**。

    用户实测报过「一些游戏在对局中点返回想强制退出时没反应，返回后闪一下还在原处」。
    深空休眠就这么漏了一整轮：`Stasis` 不在 `backGuard.PROTECTED_SCREENS` 里，
    Web 的 popstate 因此不派发 `gh:confirm-leave`，按返回只是**导航回房间页** ——
    而房间页又因为「房间在游戏中且自己仍在局中」把人弹回对局页，玩家看到的就是
    「闪一下」。**这类漏接线不会有任何报错**（TS 绿、跑起来也不崩），只会在真机上
    表现为「点了没反应」，所以拿 navigation.tsx 的路由表逐个对账。

    两条判据：① 路由在 `PROTECTED_SCREENS` 里；② 那个屏自己调了 `onConfirmLeave`。
    """
    nav = (APP_SRC / 'navigation.tsx').read_text(encoding='utf-8')
    guard = (APP_SRC / 'backGuard.tsx').read_text(encoding='utf-8')
    m = re.search(r'PROTECTED_SCREENS\s*=\s*new Set\(\[(.*?)\]\)', guard, re.S)
    protected = set(re.findall(r"'([^']+)'", m.group(1))) if m else set()

    # 对局屏 = 组件是 `<X>ScreenWrapper` 的那些路由（房间页也是 wrapper，但它不是对局屏）
    routes = re.findall(r'name="(\w+)"\s*\n\s*component=\{(\w+)ScreenWrapper\}', nav)
    games = [(name, comp) for name, comp in routes if comp != 'Room']

    missing_route = [name for name, _ in games if name not in protected]
    stray = sorted(protected - {name for name, _ in games})
    check('源码静态', '对局屏全在PROTECTED_SCREENS', not missing_route and not stray,
          f'缺={missing_route} 多={stray}')

    no_wire = []
    for name, comp in games:
        # wrapper 叫 `<X>ScreenWrapper`，文件是 `screens/<X>Screen.tsx`
        f = APP_SRC / 'screens' / f'{comp}Screen.tsx'
        if not f.exists() or 'onConfirmLeave(' not in f.read_text(encoding='utf-8'):
            no_wire.append(f'{name}({comp})')
    check('源码静态', '对局屏都接了onConfirmLeave', not no_wire, str(no_wire))


def main():
    audit_source_static()
    audit_back_wiring()
    ts = int(time.time())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={'width': 390, 'height': 844}, device_scale_factor=2,
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148')
        pg = ctx.new_page()

        # 登录/注册页
        pg.goto(BASE, timeout=120000)
        pg.get_by_text('注册', exact=True).first.wait_for(timeout=30000)
        audit_page(pg, 'auth')
        pg.get_by_text('注册', exact=True).first.click()
        audit_page(pg, 'auth_register')

        # 注册进入主界面
        pg.get_by_placeholder('3-20位字母数字下划线').fill(f'va_{ts}')
        pg.get_by_placeholder('6-32位').fill('test123456')
        pg.get_by_placeholder('再次输入密码').fill('test123456')
        pg.get_by_text('注 册', exact=True).click()
        pg.get_by_text('社交', exact=True).last.wait_for(timeout=30000)
        audit_tabbar(pg)
        audit_page(pg, 'games')

        for tab, name in (('房间', 'rooms'), ('社交', 'social'), ('我的', 'profile')):
            pg.get_by_text(tab, exact=True).last.click()
            pg.wait_for_timeout(900)
            audit_page(pg, name)

        # 创建房间弹窗：视口内完整
        pg.get_by_text('房间', exact=True).last.click()
        pg.wait_for_timeout(600)
        pg.get_by_text('创建', exact=True).first.click()
        pg.wait_for_timeout(600)
        box = pg.get_by_text('创建房间', exact=True).first.bounding_box()
        btn = pg.get_by_text('取消', exact=True).last.bounding_box()
        in_vp = box and btn and box['y'] >= 0 and btn['y'] + btn['height'] <= 844
        check('创建弹窗', '窄屏完整可见', bool(in_vp))
        audit_page(pg, 'create_modal')
        pg.get_by_text('取消', exact=True).last.click()

        # 房间页 + 「玩法设置」抽屉（v1.15）。
        # **只审 UNO 房**：它有 3 个档位 + 7 条房规、说明也最长，是这一屏的最坏情况，
        # 能把「横向溢出」这条检查打满；再各建一个扫雷房属于重复投入。
        # 注意房间页挂在根 Stack 上、**没有底部 Tab 栏**，审完必须点「退出房间」。
        pg.get_by_text('游戏', exact=True).last.click()
        pg.wait_for_timeout(600)
        pg.get_by_text('UNO 卡牌', exact=True).first.click()
        pg.get_by_text('创建房间', exact=True).click()
        pg.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        audit_page(pg, 'room')
        pg.locator('[data-testid="room-settings-row"]').click()
        pg.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
        pg.wait_for_timeout(400)
        head = pg.get_by_text('玩法设置', exact=True).last.bounding_box()
        close = pg.locator('[data-testid="room-settings-close"]').bounding_box()
        in_vp = head and close and head['y'] >= 0 \
            and close['y'] + close['height'] <= 844
        check('设置抽屉', '窄屏完整可见', bool(in_vp))
        audit_page(pg, 'room_settings')
        pg.locator('[data-testid="room-settings-close"]').click()
        pg.wait_for_timeout(400)
        pg.get_by_text('退出房间', exact=True).first.click()
        pg.wait_for_timeout(1000)

        # 我的页：移动UA应有下载APP、无检查更新
        pg.get_by_text('我的', exact=True).last.click()
        pg.wait_for_timeout(800)
        body = pg.locator('body').inner_text()
        check('我的(移动网页)', '显示下载APP', '下载 APP' in body)
        check('我的(移动网页)', '不显示检查更新', '检查更新' not in body)

        # 管理员用户管理页
        ctx2 = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
        pa = ctx2.new_page()
        pa.goto(BASE, timeout=120000)
        admin_user, admin_pass = admin_credentials()
        pa.get_by_placeholder('3-20位字母数字下划线').fill(admin_user)
        pa.get_by_placeholder('请输入密码').fill(admin_pass)
        pa.get_by_text('登 录', exact=True).click()
        pa.get_by_text('社交', exact=True).last.wait_for(timeout=30000)
        pa.get_by_text('我的', exact=True).last.click()
        pa.wait_for_timeout(800)
        pa.get_by_text('用户管理', exact=True).first.click()
        pa.get_by_text('封禁', exact=True).first.wait_for(timeout=15000)
        audit_page(pa, 'admin')
        browser.close()

    failed = [r for r in RESULTS if not r[2]]
    print(f"\n=== 视觉审计: {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 ===")
    if failed:
        for f in failed:
            print('FAIL:', f)
        sys.exit(1)


if __name__ == '__main__':
    main()
