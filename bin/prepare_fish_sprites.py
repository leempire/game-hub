#!/usr/bin/env python3
"""Slice the original 4×3 fish atlas into transparent WebP sprites.

Usage: python3 bin/prepare_fish_sprites.py path/to/atlas.png
The atlas is kept as the editable source under docs/design/.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPECIES = ('tetra', 'goldfish', 'clownfish', 'tang', 'flounder', 'puffer',
           'lionfish', 'angelfish', 'tuna', 'swordfish', 'hammerhead', 'shark')


def main():
    source = Path(sys.argv[1])
    image = Image.open(source).convert('RGBA')
    image.save(ROOT / 'docs/design/潮汐鱼群-鱼种图集.png')
    width, height = image.size
    for index, species in enumerate(SPECIES):
        col, row = index % 4, index // 4
        sprite = image.crop((col * width // 4, row * height // 3,
                             (col + 1) * width // 4, (row + 1) * height // 3))
        # Ignore nearly transparent extraction noise when measuring the sprite bounds.
        bounds = sprite.getchannel('A').point(lambda alpha: 255 if alpha > 16 else 0).getbbox()
        sprite = sprite.crop(bounds)
        sprite.thumbnail((320, 256), Image.Resampling.LANCZOS)
        sprite.save(ROOT / f'app/assets/fish/{species}.webp', quality=88, method=6)
        print(species, sprite.size)


if __name__ == '__main__':
    main()
