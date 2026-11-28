#!/usr/bin/env python3
"""Render baked creature vectors into transparent WebP sprites.

Usage: node bin/render_abyss_creatures.mjs && python3 bin/render_abyss_creatures.py
The React vector source stays in app/src/AbyssCreatures.tsx.
"""
import json
from pathlib import Path
from io import BytesIO

from PIL import Image
from playwright.sync_api import sync_playwright

DIRECTORY = Path(__file__).resolve().parents[1] / 'app/assets/abyss/creatures'


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 160, 'height': 160})
        bounds = {}
        for source in sorted(DIRECTORY.glob('*.svg')):
            page.set_content('<style>body{margin:0;background:transparent}</style>' + source.read_text())
            png = page.locator('svg').screenshot(omit_background=True)
            image = Image.open(BytesIO(png)).convert('RGBA')
            left, top, right, bottom = image.getchannel('A').getbbox()
            image.crop((left, top, right, bottom)).save(source.with_suffix('.webp'), lossless=True, method=6)
            bounds[source.stem] = dict(x=left / 2 - 40, y=top / 2 - 40,
                                       width=(right - left) / 2, height=(bottom - top) / 2)
        (DIRECTORY / 'bounds.json').write_text(json.dumps(bounds, indent=2) + '\n')
        browser.close()


if __name__ == '__main__':
    main()
