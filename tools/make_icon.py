"""Generate bacpro app icons (PNG + Windows ICO) with Pillow.

Run:  python3 tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

RED = (226, 35, 26, 255)
RED_DARK = (184, 26, 19, 255)
WHITE = (255, 255, 255, 255)
NAVY = (11, 31, 51, 255)


def draw_icon(size: int) -> Image.Image:
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded red tile with a subtle darker bottom edge
    radius = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=RED)
    # subtle inner shadow along the bottom edge
    d.rounded_rectangle([int(s * 0.02), int(s * 0.02), s - int(s * 0.02), s - int(s * 0.02)],
                        radius=int(radius * 0.94), outline=RED_DARK, width=max(1, int(s * 0.012)))

    # white building silhouette
    pad = int(s * 0.24)
    left, top, right, bottom = pad, pad, s - pad, s - pad
    body_w = int((right - left) * 0.62)
    d.rectangle([left, top + int(s * 0.06), left + body_w, bottom], fill=WHITE)
    # annex
    annex_left = left + body_w + int(s * 0.04)
    d.rectangle([annex_left, top + int(s * 0.20), right, bottom], fill=WHITE)
    # windows punched in red
    win = max(2, int(s * 0.035))
    gap_x = int(body_w * 0.28)
    gap_y = int((bottom - top) * 0.20)
    y = top + int(s * 0.15)
    while y < bottom - gap_y:
        x = left + int(body_w * 0.18)
        while x < left + body_w - win:
            d.rectangle([x, y, x + win, y + win], fill=RED)
            x += gap_x
        y += gap_y
    # annex window
    d.rectangle([annex_left + int(s * 0.04), top + int(s * 0.30),
                 annex_left + int(s * 0.04) + win, top + int(s * 0.30) + win], fill=RED)

    return img.resize((size, size), Image.LANCZOS)


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "assets")
    os.makedirs(out, exist_ok=True)

    png = draw_icon(512)
    png.save(os.path.join(out, "icon.png"))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [draw_icon(sz) for sz in sizes]
    frames[-1].save(os.path.join(out, "icon.ico"),
                    format="ICO", sizes=[(sz, sz) for sz in sizes])
    print("wrote assets/icon.png and assets/icon.ico")


if __name__ == "__main__":
    main()
