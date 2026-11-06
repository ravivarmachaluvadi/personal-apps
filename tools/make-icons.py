"""Draw the launcher's app icons into docs/icons/ (needs Pillow).

  python tools/make-icons.py

  icon-192.png, icon-512.png   "any": a rounded orange tile with four white squares (the apps)
  maskable-512.png             "maskable": the same mark on a full-bleed tile, kept inside the central
                               80% safe zone, because Android crops these to a circle or squircle

Each icon is drawn at 4x and scaled down, so the edges are smooth. Re-run after changing the colours;
the manifest (docs/manifest.webmanifest) names these three files.
"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'docs', 'icons')
ACCENT = (224, 86, 43, 255)   # --accent in light mode (#E0562B)
INK = (255, 255, 255, 255)
SS = 4                        # supersampling factor


def mark(d, size, box):
    """four rounded squares in a 2x2 grid, filling `box` (x0, y0, x1, y1)"""
    x0, y0, x1, y1 = box
    gap = (x1 - x0) * 0.14
    cell = ((x1 - x0) - gap) / 2
    r = cell * 0.26
    for row in range(2):
        for col in range(2):
            cx = x0 + col * (cell + gap)
            cy = y0 + row * (cell + gap)
            d.rounded_rectangle((cx, cy, cx + cell, cy + cell), radius=r, fill=INK)


def tile(size, maskable):
    big = size * SS
    im = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if maskable:
        d.rectangle((0, 0, big, big), fill=ACCENT)
        inner = big * 0.40          # the mark spans 40% of the tile, well inside the 80% safe circle
    else:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=ACCENT)
        inner = big * 0.52
    pad = (big - inner) / 2
    mark(d, big, (pad, pad, pad + inner, pad + inner))
    return im.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, size, maskable in (('icon-192.png', 192, False), ('icon-512.png', 512, False), ('maskable-512.png', 512, True)):
        p = os.path.join(OUT, name)
        tile(size, maskable).save(p, optimize=True)
        print('wrote', os.path.relpath(p, ROOT), '%dx%d' % (size, size))


if __name__ == '__main__':
    main()
