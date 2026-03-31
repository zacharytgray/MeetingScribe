#!/usr/bin/env python3
"""Generate MeetingScribe app icon (.icns) programmatically.

Draws a microphone on a dark gradient rounded-rect background.
No external image assets required. Run:  python scripts/generate_icon.py
Output: assets/AppIcon.icns
"""

import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow required: pip install Pillow")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONSET = ASSETS / "AppIcon.iconset"


def _rounded_rect_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _draw_mic(draw: ImageDraw.Draw, s: int) -> None:
    """Draw a simple microphone shape centered in the image."""
    cx = s // 2

    # mic body (rounded capsule)
    body_w = int(s * 0.22)
    body_h = int(s * 0.36)
    body_top = int(s * 0.16)
    body_left = cx - body_w // 2
    body_right = cx + body_w // 2
    body_bottom = body_top + body_h
    cap_r = body_w // 2

    # capsule = rounded rect
    draw.rounded_rectangle(
        [body_left, body_top, body_right, body_bottom],
        radius=cap_r, fill=(255, 255, 255, 230),
    )

    # grille lines on mic body
    for i in range(3):
        gy = body_top + int(body_h * (0.35 + i * 0.15))
        draw.line(
            [(body_left + 6, gy), (body_right - 6, gy)],
            fill=(180, 180, 220, 120), width=max(1, s // 256),
        )

    # arc cradle below mic body
    arc_w = int(s * 0.32)
    arc_left = cx - arc_w // 2
    arc_right = cx + arc_w // 2
    arc_top = body_bottom - int(s * 0.06)
    arc_bottom = body_bottom + int(s * 0.16)
    line_w = max(2, int(s * 0.025))

    draw.arc(
        [arc_left, arc_top, arc_right, arc_bottom],
        start=0, end=180, fill=(255, 255, 255, 200), width=line_w,
    )

    # vertical stem
    stem_top = arc_bottom - line_w
    stem_bottom = stem_top + int(s * 0.12)
    draw.line(
        [(cx, stem_top), (cx, stem_bottom)],
        fill=(255, 255, 255, 200), width=line_w,
    )

    # horizontal base
    base_w = int(s * 0.18)
    draw.line(
        [(cx - base_w // 2, stem_bottom), (cx + base_w // 2, stem_bottom)],
        fill=(255, 255, 255, 200), width=line_w,
    )


def generate_icon(size: int) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    corner_r = int(s * 0.22)  # macOS icon corner radius
    mask = _rounded_rect_mask(s, corner_r)

    # gradient background: dark navy -> rich purple
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    for y in range(s):
        t = y / max(s - 1, 1)
        r, g, b = _lerp((20, 20, 50), (55, 30, 100), t)
        for x in range(s):
            if mask.getpixel((x, y)) > 0:
                bg.putpixel((x, y), (r, g, b, 255))

    # soft top highlight
    highlight_h = int(s * 0.35)
    for y in range(highlight_h):
        t = y / highlight_h
        boost = int(40 * (1.0 - t) ** 2)
        for x in range(s):
            if mask.getpixel((x, y)) > 0:
                pr, pg, pb, pa = bg.getpixel((x, y))
                bg.putpixel((x, y), (min(255, pr + boost), min(255, pg + boost), min(255, pb + boost), pa))

    img = Image.alpha_composite(img, bg)

    # draw mic directly
    _draw_mic(ImageDraw.Draw(img), s)

    # apply rounded-rect mask to final composite
    final = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    final.paste(img, (0, 0), mask)
    return final


def main() -> None:
    ICONSET.mkdir(parents=True, exist_ok=True)

    specs = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]

    master = generate_icon(1024)
    for name, px in specs:
        resized = master.resize((px, px), Image.LANCZOS) if px != 1024 else master
        resized.save(ICONSET / name, "PNG")
        print(f"  {name} ({px}x{px})")

    icns_path = ASSETS / "AppIcon.icns"
    result = subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(icns_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"iconutil error: {result.stderr}")
        sys.exit(1)

    print(f"\n  -> {icns_path} ({icns_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
