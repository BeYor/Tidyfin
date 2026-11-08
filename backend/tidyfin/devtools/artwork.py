"""
Generated poster artwork for the demo and the screenshots.

Every poster is drawn from scratch (gradients and simple shapes) and carries an
invented title, so nothing here is taken from a real film or series. The same
title always gives the same picture.
"""
from __future__ import annotations

import hashlib
import math
import random
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_SS = 2          # supersampling

# (sky top, sky bottom, accent, dark)
PALETTES = [
    ((255, 138, 101), (255, 214, 140), (255, 244, 214), (60, 28, 48)),     # sunset
    ((38, 159, 232), (170, 226, 250), (243, 247, 251), (10, 40, 72)),      # day blue
    ((96, 72, 245), (255, 120, 150), (255, 230, 240), (24, 16, 70)),       # violet dusk
    ((18, 217, 176), (200, 250, 225), (240, 255, 250), (6, 56, 62)),       # teal
    ((255, 73, 103), (255, 170, 120), (255, 238, 220), (70, 14, 36)),      # coral
    ((16, 36, 58), (60, 100, 150), (190, 215, 245), (7, 13, 22)),          # night
    ((230, 190, 90), (250, 235, 190), (255, 250, 235), (84, 56, 22)),      # sand
    ((60, 90, 70), (170, 200, 150), (235, 245, 220), (18, 36, 28)),        # forest
]


def _mix(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=_mix(top, bottom, y / max(h - 1, 1)))
    return img


def _ridge(rng, w, h, base, amp, rough):
    """A polygon outline of hills / mountains: list of points from left to right, closed at the bottom."""
    pts, x = [], 0
    phase = rng.random() * 6.28
    while x <= w + 20:
        y = base - amp * (0.5 + 0.5 * math.sin(x / w * 5.0 + phase)) - rng.random() * rough
        pts.append((x, y))
        x += w // 22
    return pts + [(w, h), (0, h)]


def _scene(kind, rng, w, h, pal):
    top, bottom, accent, dark = pal
    img = _gradient(w, h, top, bottom)
    d = ImageDraw.Draw(img, "RGBA")
    if kind == "mountains":
        sx, sy, r = w * (0.3 + 0.4 * rng.random()), h * 0.36, w * 0.17
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=accent + (235,))
        for i in range(4):
            col = _mix(bottom, dark, 0.25 + i * 0.22)
            d.polygon(_ridge(rng, w, h, h * (0.5 + i * 0.09), h * 0.16, h * 0.05), fill=col)
    elif kind == "ocean":
        sx, sy, r = w * 0.65, h * 0.28, w * 0.13
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=accent + (240,))
        for i in range(9):
            y0 = h * (0.42 + i * 0.065)
            col = _mix(top, dark, 0.15 + i * 0.09)
            pts = [(x, y0 + math.sin(x / w * 9 + i * 1.3) * h * 0.012) for x in range(0, w + 8, 8)]
            d.polygon(pts + [(w, h), (0, h)], fill=col)
    elif kind == "city":
        for _ in range(40):
            x, y, s = rng.random() * w, rng.random() * h * 0.5, rng.random() * 2.2 + 0.6
            d.ellipse([x, y, x + s, y + s], fill=accent + (int(120 + 100 * rng.random()),))
        mx, my, r = w * 0.74, h * 0.2, w * 0.08
        d.ellipse([mx - r, my - r, mx + r, my + r], fill=accent + (245,))
        for layer in range(2):
            x = -10
            while x < w:
                bw = rng.randint(int(w * 0.07), int(w * 0.16))
                bh = int(h * (0.18 + rng.random() * 0.3 - layer * 0.08))
                col = _mix(bottom, dark, 0.55 + layer * 0.3)
                d.rectangle([x, h * 0.78 - bh, x + bw, h], fill=col)
                if layer == 1:
                    for wy in range(int(h * 0.78 - bh + 14), int(h * 0.78), 22):
                        for wx in range(int(x + 8), int(x + bw - 8), 18):
                            if rng.random() < 0.35:
                                d.rectangle([wx, wy, wx + 7, wy + 10], fill=accent + (210,))
                x += bw + rng.randint(2, 10)
    elif kind == "forest":
        for i in range(5):
            col = _mix(bottom, dark, 0.2 + i * 0.2)
            y_base = h * (0.55 + i * 0.1)
            x = -20
            while x < w + 20:
                th = h * (0.14 + rng.random() * 0.12) * (1 + i * 0.12)
                tw = th * 0.42
                d.polygon([(x, y_base), (x + tw / 2, y_base - th), (x + tw, y_base)], fill=col)
                d.rectangle([x + tw * 0.46, y_base, x + tw * 0.54, h], fill=col)
                x += tw * (0.45 + rng.random() * 0.3)
        d.rectangle([0, h * 0.62, w, h * 0.72], fill=accent + (28,))
        d.rectangle([0, h * 0.9, w, h], fill=_mix(bottom, dark, 0.9))
    elif kind == "dunes":
        sx, sy, r = w * 0.3, h * 0.3, w * 0.15
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=accent + (240,))
        for i in range(6):
            y0 = h * (0.5 + i * 0.1)
            col = _mix(bottom, dark, 0.1 + i * 0.14)
            pts = [(x, y0 - math.sin(x / w * 3.2 + i * 1.7) * h * 0.06 - (x / w) * h * 0.03 * (i % 2)) for x in range(0, w + 8, 8)]
            d.polygon(pts + [(w, h), (0, h)], fill=col)
    elif kind == "space":
        img = _gradient(w, h, dark, _mix(top, dark, 0.5))
        d = ImageDraw.Draw(img, "RGBA")
        for _ in range(110):
            x, y, s = rng.random() * w, rng.random() * h, rng.random() * 2.4 + 0.5
            d.ellipse([x, y, x + s, y + s], fill=accent + (int(90 + 150 * rng.random()),))
        px, py, r = w * (0.35 + 0.3 * rng.random()), h * 0.34, w * 0.2
        d.ellipse([px - r, py - r, px + r, py + r], fill=_mix(top, accent, 0.25))
        d.ellipse([px - r * 0.2, py - r * 0.9, px + r * 1.0, py + r * 0.3], fill=(0, 0, 0, 0))
        d.ellipse([px - r * 1.7, py - r * 0.25, px + r * 1.7, py + r * 0.25], outline=accent + (200,), width=max(w // 90, 2))
        d.ellipse([px - r, py - r, px + r, py + r], outline=accent + (90,), width=2)
        mr = r * 0.22
        mx, my = px + r * 1.25, py + r * 0.95
        d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=_mix(bottom, accent, 0.4))
    else:  # geometry
        for i in range(9):
            cx, cy = rng.random() * w, rng.random() * h * 0.8
            r = w * (0.12 + rng.random() * 0.3)
            col = _mix(top, accent, rng.random())
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (int(60 + 90 * rng.random()),))
        for i in range(3):
            cx, cy, r = w * (0.2 + i * 0.3), h * (0.3 + 0.1 * i), w * (0.18 + 0.05 * i)
            d.arc([cx - r, cy - r, cx + r, cy + r], 200, 340, fill=accent + (230,), width=max(w // 70, 2))
    return img


_KINDS = ["mountains", "ocean", "city", "forest", "dunes", "space", "geometry"]


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def poster(title: str, size=(300, 450), kind: str | None = None) -> bytes:
    """A 2:3 poster with abstract artwork and the title at the bottom (JPEG bytes)."""
    seed = int(hashlib.sha1(title.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    w, h = size[0] * _SS, size[1] * _SS
    pal = PALETTES[seed % len(PALETTES)]
    kind = kind or _KINDS[(seed >> 5) % len(_KINDS)]
    img = _scene(kind, rng, w, h, pal).convert("RGBA")

    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))                       # darkening towards the bottom for the title
    sd = ImageDraw.Draw(shade)
    for y in range(int(h * 0.45), h):
        t = (y - h * 0.45) / (h * 0.55)
        sd.line([(0, y), (w, y)], fill=(0, 0, 0, int(190 * t * t)))
    img = Image.alpha_composite(img, shade)

    d = ImageDraw.Draw(img)
    font = _font(int(w * 0.105))
    words, lines, cur = title.upper().split(), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=font) > w * 0.84 and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    lines.append(cur)
    line_h = int(w * 0.135)
    y = int(h * 0.80) - (len(lines) - 1) * line_h // 2
    for line in lines[:3]:
        tw = d.textlength(line, font=font)
        d.text(((w - tw) / 2, y), line, fill=(243, 247, 251, 255), font=font)
        y += line_h
    d.line([(w * 0.38, h * 0.74), (w * 0.62, h * 0.74)], fill=pal[2] + (220,), width=max(w // 150, 2))

    out = img.convert("RGB").resize(size, Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
