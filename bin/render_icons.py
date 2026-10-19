#!/usr/bin/env python3
"""
应用图标 SVG → PNG 渲染脚本。
用法：python bin/render_icons.py（源 SVG 在 app/assets/svg/，输出到 app/assets/）
"""
import pathlib

from playwright.sync_api import sync_playwright

SVG_DIR = pathlib.Path(__file__).resolve().parent.parent / 'app' / 'assets' / 'svg'
ASSETS = SVG_DIR.parent.parent / 'assets'

# (svg 文件, 输出 png, 边长, 是否透明底)
JOBS = [
    ('icon.svg', 'icon.png', 1024, True),
    ('icon.svg', 'splash-icon.png', 1024, True),
    ('adaptive-foreground.svg', 'android-icon-foreground.png', 1024, False),
    ('monochrome.svg', 'android-icon-monochrome.png', 1024, True),
    ('favicon.svg', 'favicon.png', 96, False),
]

HTML = """<!DOCTYPE html><html><head><style>
html,body{{margin:0;padding:0;width:{s}px;height:{s}px;overflow:hidden}}
svg{{display:block;width:{s}px;height:{s}px}}
</style></head><body>{svg}</body></html>"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path='/home/dory/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome')
        for svg_name, out_name, size, transparent in JOBS:
            svg = (SVG_DIR / svg_name).read_text(encoding='utf-8')
            page = browser.new_page(viewport={'width': size, 'height': size}, device_scale_factor=1)
            page.set_content(HTML.format(s=size, svg=svg))
            page.wait_for_timeout(150)
            page.screenshot(path=str(ASSETS / out_name), omit_background=transparent,
                            clip={'x': 0, 'y': 0, 'width': size, 'height': size})
            page.close()
            print(f'✓ {out_name} ({size}px, transparent={transparent})')
        browser.close()


if __name__ == '__main__':
    main()
