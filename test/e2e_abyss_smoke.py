#!/usr/bin/env python3
"""深渊回声跨浏览器冒烟。使用 --isolated 可在临时后端运行。"""
import argparse
from contextlib import nullcontext
import os
import re
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get('ABYSS_TEST_WEB_URL', 'http://localhost:50540')
PWD = 'test123456'
TS = int(time.time())
UA, UB = f'ab_a_{TS}', f'ab_b_{TS}'
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
    page.get_by_text('我的', exact=True).last.wait_for(timeout=20000)


def body(page):
    return page.locator('body').inner_text()


def main(browser_name='chromium', executable_path=None, isolated=False):
    print(f'=== Web E2E: 深渊回声冒烟 ({browser_name}) ===')
    if isolated:
        from e2e_abyss_complete import isolated_backend, use_backend
        backend = isolated_backend()
    else:
        backend = nullcontext((None, None))

    with backend as (origin, in_match), sync_playwright() as p:
        browser = getattr(p, browser_name).launch(headless=True, executable_path=executable_path)
        ctx = {'viewport': {'width': 390, 'height': 844}}
        pa = browser.new_context(**ctx).new_page()
        pb = browser.new_context(**ctx).new_page()
        for pg in (pa, pb):
            pg.set_default_timeout(15000)
            if origin:
                use_backend(pg, origin)

        pa.add_init_script("""(() => {
          const play = HTMLMediaElement.prototype.play;
          HTMLMediaElement.prototype.play = function (...args) {
            if (this.src.includes('recovery-station')) window.abyssSmokeMusic = this;
            return play.apply(this, args);
          };
        })()""")

        register(pa, UA)
        register(pb, UB)
        ok('双方注册登录')

        goto_tab(pa, '游戏')
        pa.get_by_text('深渊回声', exact=True).first.click()
        pa.get_by_text('创建房间', exact=True).click()
        pa.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        match = re.search(r'\b[A-Z0-9]{6}\b', body(pa))
        assert match, '未找到6位房间号'
        code = match.group(0)
        ok(f'A 创建深渊回声房间 {code}')
        guide = pa.get_by_test_id('game-intro')
        guide.get_by_text('拖动移动，武器自动开火', exact=True).wait_for()
        assert guide.get_by_text('打一局人机练习', exact=False).count() == 0
        pa.get_by_test_id('guide-next').click()
        guide.get_by_text('捡信标，选强化', exact=True).wait_for()
        pa.get_by_test_id('guide-next').click()
        guide.get_by_text('躲开警示圈，打倒守卫', exact=True).wait_for()
        pa.get_by_test_id('guide-next').click()
        guide.wait_for(state='hidden')
        pa.get_by_text('新手指引', exact=True).click()
        guide.get_by_text('拖动移动，武器自动开火', exact=True).wait_for()
        pa.get_by_test_id('guide-skip').click()
        ok('首进房间展示三步指引，完成后可主动回看')

        goto_tab(pb, '房间')
        pb.get_by_text('加入', exact=True).first.click()
        pb.get_by_placeholder('输入6位房间号').fill(code)
        pb.get_by_text('加入', exact=True).last.click()
        pb.get_by_text('房间号', exact=True).wait_for(timeout=15000)
        pb.get_by_test_id('guide-skip').click()
        pb.get_by_text('准 备', exact=True).click()
        pa.wait_for_timeout(600)
        assert '2/4' in body(pa), f'房间人数应显示 2/4：{body(pa)[:200]}'

        pa.locator('[data-testid="room-settings-row"]').click()
        pa.locator('[data-testid="room-settings-sheet"]').wait_for(timeout=8000)
        assert '对局时长' in body(pa) and '经验节奏' in body(pa), '深渊回声房规未下发'
        pa.locator('[data-testid="room-settings-duration-1200"]').click()
        pa.wait_for_timeout(600)
        assert '20 分钟' in body(pa), '切到 20 分钟后摘要没变'
        pa.locator('[data-testid="room-settings-xp_pace-fast"]').click()
        pa.wait_for_timeout(600)
        assert '快速' in body(pa), '切到快速经验后摘要没变'
        pa.locator('[data-testid="room-settings-close"]').click()
        pa.wait_for_timeout(400)
        ok('房规：20 分钟 + 快速经验')

        pa.get_by_test_id('talent-tree-row').click()
        sheet = pa.get_by_test_id('talent-sheet')
        sheet.wait_for(timeout=8000)
        sheet.get_by_text('深潜体魄', exact=True).wait_for(timeout=8000)
        assert '潜水员天赋' in body(pa), '天赋树未下发'
        assert '免费重置' in body(pa), '缺少免费重置入口'
        pa.get_by_test_id('talent-close').click()
        sheet.wait_for(state='hidden')
        ok('房间可查看局外天赋树')

        pa.get_by_text('开始游戏', exact=True).click()
        for pg in (pa, pb):
            pg.locator('[data-testid="abyss-arena"]').wait_for(timeout=20000)
        ok('双方进入战场')

        clock = pa.locator('[data-testid="abyss-clock"]').inner_text()
        assert clock.startswith('20:') or clock.startswith('19:'), f'对局时长不是 20 分钟：{clock}'
        assert '武器 1/6' in body(pa), f'初始武器槽显示不对：{body(pa)[:300]}'
        assert pa.locator('[data-testid="abyss-inventory"]').count() == 1
        ok(f'HUD 正常（倒计时 {clock}，初始 1 把武器）')

        # Web 返回键：必须命中 backGuard 的 gh:confirm-leave（Abyss 在 PROTECTED_SCREENS 里），
        # 只弹确认框，不能直接导航回房间页。
        pa.go_back()
        pa.locator('[data-testid="abyss-confirm-modal"]').wait_for(timeout=8000)
        assert '结束本次试潜？' in body(pa)
        pa.get_by_text('取消', exact=True).click()
        pa.wait_for_timeout(400)
        assert pa.locator('[data-testid="abyss-confirm-modal"]').count() == 0
        ok('对局中浏览器返回：弹出确认框并可取消')

        if in_match:
            from app.game import abyss
            lead = in_match(code, lambda gs: next(iter(gs['players'])))
            diver = pb.get_by_test_id(f'abyss-diver-{lead}')
            before = diver.evaluate('node => node.transform.baseVal.getItem(0).matrix.e')
            pa.keyboard.down('d')
            try:
                pb.wait_for_function("""([id, x]) => {
                  const node = document.querySelector('[data-testid="abyss-diver-' + id + '"]');
                  return node?.transform.baseVal.getItem(0).matrix.e > x + 20;
                }""", arg=[lead, before])
            finally:
                pa.keyboard.up('d')
            ok('队友视角看到了实时移动')
            in_match(code, lambda gs: gs.update(xp=abyss.threshold(gs)))

        pa.get_by_text('选择强化 ·', exact=False).wait_for(timeout=90000)
        choices = pa.locator('[data-testid^="abyss-choice-"]')
        assert choices.count() >= 3, f'升级候选不足 3 个：{choices.count()}'
        art = choices.get_by_test_id('abyss-item-art')
        assert art.count() == choices.count()
        for choice in choices.all():
            assert choice.get_by_test_id('abyss-item-kind').inner_text() in ('武器', '道具')
        pa.wait_for_function("""count => {
          const images = [...document.querySelectorAll('[data-testid="abyss-item-art"] img')];
          return images.length === count && images.every(img => img.complete && img.naturalWidth > 0);
        }""", arg=choices.count(), timeout=5000)
        text = body(pa)
        assert '等待队友选择' not in text, '自己的升级候选没有下发'
        pa.keyboard.press('1')
        pb.get_by_text('选择强化 ·', exact=False).wait_for()
        pb.keyboard.press('1')
        pa.get_by_test_id('abyss-overlay-scroll').wait_for(state='hidden')
        ok('双方用数字键选完强化，战斗恢复')

        pa.get_by_test_id('abyss-audio').click()
        pa.get_by_test_id('abyss-music-level-loud').click()
        pa.get_by_test_id('abyss-audio-close').click()
        pa.wait_for_function("""() => {
          const music = window.abyssSmokeMusic;
          return music && !music.paused && music.currentTime > .05 && Math.abs(music.volume - .8) < .0001;
        }""")
        ok('配乐按选定档位实际播放')

        pa.locator('[data-testid="abyss-inventory"]').click()
        pa.locator('[data-testid="abyss-inventory-modal"]').wait_for(timeout=8000)
        text = body(pa)
        assert '装备与属性' in text and '伤害' in text and '冷却' in text, '装备面板缺少属性区'
        assert '武器' in text and '被动' in text, '装备面板缺少武器/被动区'
        pa.locator('[data-testid="abyss-inventory-close"]').click()
        ok('装备面板显示武器 / 被动 / 属性')

        pa.locator('[data-testid="abyss-leave"]').click()
        pa.locator('[data-testid="abyss-confirm-modal"]').wait_for(timeout=8000)
        assert '结束本次试潜？' in body(pa)
        pa.get_by_text('取消', exact=True).click()
        pa.wait_for_timeout(400)
        ok('离开确认弹窗可打开并取消')

        browser.close()
    print(f'=== 通过：{STEP} 步 ===')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', choices=('chromium', 'firefox', 'webkit'), default='chromium')
    parser.add_argument('--executable-path')
    parser.add_argument('--isolated', action='store_true')
    args = parser.parse_args()
    main(args.browser, args.executable_path, args.isolated)
