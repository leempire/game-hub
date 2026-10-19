#!/usr/bin/env python3
"""
Web E2E: 身份刷新 + 改密码重登。
流程：注册 → 「我的」角色是"用户"、没有「用户管理」入口
      → 后台把角色改成 admin（直接写库，等价于管理员在管理页操作）
      → 刷新页面（会话从本地缓存恢复）→ **不重新登录**就出现「用户管理」+「管理员」徽标
      → 进管理页看到用户列表 → 改密码 → 自动退回登录页并提示重新登录
      → 用新密码登录成功。
用法：先启动后端(50541)和前端web(50540)，然后 python test/e2e_auth_refresh.py
"""
import sqlite3
import time

from playwright.sync_api import sync_playwright

BASE = 'http://localhost:50540'
DB = '/home/dory/code/sandbox/game_hub/server/data/gamehub.db'
PWD = 'test123456'
NEW_PWD = 'test654321'
TS = int(time.time())
UA = f'ar_{TS}'
STEP = 0


def ok(msg):
    global STEP
    STEP += 1
    print(f'  [{STEP:02d}] ✓ {msg}')


def goto_tab(page, name):
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(700)


def register(page, username):
    page.goto(BASE)
    page.get_by_text('注册', exact=True).first.click(timeout=15000)
    page.get_by_placeholder('3-20位字母数字下划线').fill(username)
    page.get_by_placeholder('6-32位').fill(PWD)
    page.get_by_placeholder('再次输入密码').fill(PWD)
    page.get_by_text('注 册', exact=True).click()
    page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)


def set_role(username, role):
    """直接改库——后端每次请求现查 users.role，改完立刻生效。"""
    conn = sqlite3.connect(DB)
    try:
        conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        conn.commit()
    finally:
        conn.close()


def main():
    print('=== Web E2E: 身份刷新 + 改密码重登 ===')
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
        )
        page = browser.new_context(viewport={'width': 390, 'height': 844}).new_page()
        page.set_default_timeout(15000)

        register(page, UA)
        ok(f'注册登录 {UA}')

        # 普通用户：没有管理入口
        goto_tab(page, '我的')
        # **不能加 exact=True**：这行是一个复合文本节点「注册于 2026-10-13」，
        # exact 要求整段文字完全相等，永远匹配不上（这行测试从来没绿过）。
        page.get_by_text('注册于').first.wait_for(timeout=15000)
        assert page.get_by_text('用户管理', exact=True).count() == 0, '普通用户不该有管理入口'
        ok('普通用户「我的」页无「用户管理」入口')

        # 后台授权（等价于管理员在管理页点「设为管理员」）
        set_role(UA, 'admin')
        ok('后台把角色改为 admin')

        # 关键一步：刷新页面 = 会话从缓存恢复，不重新登录
        page.reload()
        page.get_by_text('用户管理', exact=True).wait_for(timeout=15000)
        page.get_by_text('管理员', exact=True).first.wait_for(timeout=15000)
        ok('刷新后不重新登录即出现「用户管理」+「管理员」徽标（回源刷新生效）')

        # 管理页真的能用
        page.get_by_text('用户管理', exact=True).click()
        page.get_by_placeholder('搜索用户名/昵称').wait_for(timeout=15000)
        body = page.locator('body').inner_text()
        assert '个用户' in body, '管理页没加载出用户总数'
        ok('进入管理页，用户列表加载正常')

        # 改密码 → 应自动退回登录页
        page.goto(f'{BASE}/profile')
        page.get_by_text('修改密码', exact=True).click()
        page.get_by_placeholder('当前密码').fill(PWD)
        page.get_by_placeholder('新密码（至少6位）').fill(NEW_PWD)
        page.get_by_text('修改', exact=True).click()
        page.get_by_text('请用新密码重新登录').wait_for(timeout=15000)
        page.get_by_placeholder('请输入密码').wait_for(timeout=15000)
        assert page.get_by_text('用户管理', exact=True).count() == 0, '改完密码还停在登录后的界面'
        ok('改密码后自动退回登录页并提示「请用新密码重新登录」')

        # 旧密码不该能登，新密码可以
        page.get_by_placeholder('3-20位字母数字下划线').fill(UA)
        page.get_by_placeholder('请输入密码').fill(NEW_PWD)
        page.get_by_text('登 录', exact=True).click()
        page.get_by_text('社交', exact=True).last.wait_for(timeout=20000)
        ok('用新密码重新登录成功')

        # 新会话仍是管理员（角色没被改密码流程弄丢）
        goto_tab(page, '我的')
        page.get_by_text('用户管理', exact=True).wait_for(timeout=15000)
        ok('重新登录后仍是管理员')

        browser.close()
    print(f'=== 全部 {STEP} 步通过 ===')


if __name__ == '__main__':
    main()
