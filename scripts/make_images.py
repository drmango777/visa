"""Make the share image and icons: static/og.png, static/favicon.png, static/apple-touch-icon.png.

    pip install pillow        (only needed to run this script, not to run the site)
    python scripts/make_images.py

The share image (og.png) is what shows up when someone posts a link to the site.
It uses the real shape of downtown Miami from data/coast.json.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
INK, BLUE, BG, WATER, LINE = (18, 48, 58), (29, 95, 209), (247, 245, 240), (220, 231, 243), (201, 214, 228)
FONT = Path("C:/Windows/Fonts/bahnschrift.ttf")      # condensed, close to the site's headline font


def font(size, style="Bold Condensed"):
    for path in (FONT, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if path.exists():
            f = ImageFont.truetype(str(path), size)
            try:
                f.set_variation_by_name(style)
            except Exception:  # noqa: BLE001  (not a variable font)
                pass
            return f
    return ImageFont.load_default(size)


def logo(draw, x, y, s):
    """The sail mark from the site header, s pixels tall."""
    k = s / 30
    pts = lambda p: [(x + a * k, y + b * k) for a, b in p]
    draw.polygon(pts([(15, 3), (15, 21), (5, 21)]), fill=BLUE)
    draw.polygon(pts([(17, 8), (17, 21), (25, 21)]), fill=(136, 152, 157))
    draw.polygon(pts([(3, 24), (27, 24), (24, 28), (6, 28)]), fill=INK)


def share_image():
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), WATER)
    d = ImageDraw.Draw(img)
    coast = json.loads((ROOT / "data" / "coast.json").read_text(encoding="utf-8"))
    # downtown Miami, same view as the landing page
    s, n, w, e = 25.745, 25.805, -80.258, -80.154
    X = lambda lon: (lon - w) / (e - w) * W
    Y = lambda lat: (n - lat) / (n - s) * H
    for poly in coast["land"]:
        d.polygon([(X(lo), Y(la)) for la, lo in poly], fill=BG, outline=LINE)
    for road in coast["causeways"]:
        d.line([(X(lo), Y(la)) for la, lo in road], fill=(178, 189, 194), width=2)
    for river in coast["river"]:
        d.line([(X(lo), Y(la)) for la, lo in river], fill=WATER, width=6)
    logo(d, 64, 54, 56)
    d.text((132, 58), "Sail-by", font=font(52, "Bold"), fill=INK)
    big = font(132)
    d.text((60, 250), "BACK IN MIAMI?", font=big, fill=INK)
    d.text((60, 376), "START HERE.", font=big, fill=BLUE)
    d.text((64, 532), "Help nearby, what changed, and getting home by curfew.", font=font(34, "SemiBold"), fill=INK)
    d.text((64, 574), "English · Español · Kreyòl", font=font(28, "SemiBold"), fill=(79, 97, 104))
    img.save(STATIC / "og.png", optimize=True)


def icon(size, name, pad):
    img = Image.new("RGB", (size * 4, size * 4), BG)              # draw big, then shrink for smooth edges
    logo(ImageDraw.Draw(img), pad * 4, pad * 4, (size - 2 * pad) * 4)
    img.resize((size, size), Image.LANCZOS).save(STATIC / name, optimize=True)


if __name__ == "__main__":
    share_image()
    icon(48, "favicon.png", 2)
    icon(180, "apple-touch-icon.png", 22)
    for f in ("og.png", "favicon.png", "apple-touch-icon.png"):
        print(f"  wrote static/{f} ({(STATIC / f).stat().st_size // 1024} KB)")
