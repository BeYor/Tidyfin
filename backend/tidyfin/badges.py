"""
Poster badges: configuration, presets, and rendering.

The dashboard preview and posters uploaded to Jellyfin use the same Pillow rendering code, ensuring consistent results.
"""
from __future__ import annotations

import re
from collections import namedtuple
from dataclasses import dataclass, field
from io import BytesIO
from typing import Iterable, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right", "top-center", "bottom-center")

# Style presets. The dashboard copies a preset's values into the editable
# fields; only the resolved values are stored.
#   blur     frosted-glass effect: the poster behind the badge is blurred (% of badge height)
#   shine    soft highlight along the top edge, 0..1
#   gradient second colour: the fill fades from bg (top) to this colour (bottom); "" = flat
#   glow     halo around the badge in the border colour, 0..1
#   shadow   soft drop shadow, 0..1
PRESETS = {
    "glass": {"bg": "#000000", "bg_opacity": 0.42, "fg": "#FFFFFF", "border": "#FFFFFF", "border_opacity": 0.38, "border_width": 1,
              "blur": 45, "shine": 0.22, "gradient": "", "glow": 0, "shadow": 0.35},
    "tint": {"bg": "#269FE8", "bg_opacity": 0.42, "fg": "#FFFFFF", "border": "#B8E6FF", "border_opacity": 0.6, "border_width": 1,
             "blur": 45, "shine": 0.32, "gradient": "", "glow": 0, "shadow": 0.35},
    "frost": {"bg": "#F3F7FB", "bg_opacity": 0.5, "fg": "#0A1522", "border": "#FFFFFF", "border_opacity": 0.7, "border_width": 1,
              "blur": 50, "shine": 0.3, "gradient": "", "glow": 0, "shadow": 0.25},
    "solid": {"bg": "#FF4967", "bg_opacity": 1.0, "fg": "#FFFFFF", "border": "#FFFFFF", "border_opacity": 0.0, "border_width": 0,
              "blur": 0, "shine": 0, "gradient": "", "glow": 0, "shadow": 0.3},
    "gradient": {"bg": "#6048F5", "bg_opacity": 1.0, "fg": "#FFFFFF", "border": "#FFFFFF", "border_opacity": 0.25, "border_width": 1,
                 "blur": 0, "shine": 0.12, "gradient": "#269FE8", "glow": 0, "shadow": 0.35},
    "neon": {"bg": "#070D16", "bg_opacity": 0.72, "fg": "#F3F7FB", "border": "#12D9B0", "border_opacity": 1.0, "border_width": 2,
             "blur": 30, "shine": 0, "gradient": "", "glow": 0.75, "shadow": 0},
    "outline": {"bg": "#070D16", "bg_opacity": 0.55, "fg": "#FFFFFF", "border": "#32E5B7", "border_opacity": 1.0, "border_width": 2,
                "blur": 0, "shine": 0, "gradient": "", "glow": 0, "shadow": 0},
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT_FILES = {
    True: ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    False: ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
}


def _clamp(value, default, lo, hi):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _color(value, default):
    return value.upper() if isinstance(value, str) and _HEX_RE.match(value) else default


@dataclass
class BadgeStyle:
    preset: str = "glass"
    bg: str = "#000000"
    bg_opacity: float = 0.42
    fg: str = "#FFFFFF"
    border: str = "#FFFFFF"
    border_opacity: float = 0.38
    border_width: int = 1
    size: float = 5.5        # badge height as a percentage of the poster height
    radius: float = 20.0     # corner radius as a percentage of the badge height
    weight: str = "bold"     # "bold" | "regular"
    blur: float = 45.0       # frosted glass: blur radius of the poster behind the badge (% of badge height)
    shine: float = 0.22      # highlight along the top edge, 0..1
    gradient: str = ""       # second fill colour ("" = flat fill)
    glow: float = 0.0        # halo in the border colour, 0..1
    shadow: float = 0.35     # drop shadow, 0..1

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "BadgeStyle":
        d = d or {}
        base = cls()
        preset = d.get("preset") if d.get("preset") in (*PRESETS, "custom") else "glass"
        return cls(
            preset=preset,
            bg=_color(d.get("bg"), base.bg),
            bg_opacity=_clamp(d.get("bg_opacity"), base.bg_opacity, 0, 1),
            fg=_color(d.get("fg"), base.fg),
            border=_color(d.get("border"), base.border),
            border_opacity=_clamp(d.get("border_opacity"), base.border_opacity, 0, 1),
            border_width=int(_clamp(d.get("border_width"), base.border_width, 0, 8)),
            size=_clamp(d.get("size"), base.size, 2.5, 14),
            radius=_clamp(d.get("radius"), base.radius, 0, 50),
            weight="regular" if d.get("weight") == "regular" else "bold",
            blur=_clamp(d.get("blur"), base.blur, 0, 150),
            shine=_clamp(d.get("shine"), base.shine, 0, 1),
            gradient=_color(d.get("gradient"), "") if d.get("gradient") else "",
            glow=_clamp(d.get("glow"), base.glow, 0, 1),
            shadow=_clamp(d.get("shadow"), base.shadow, 0, 1),
        )

    @classmethod
    def from_preset(cls, name: str, **overrides) -> "BadgeStyle":
        data = {**PRESETS.get(name, PRESETS["glass"]), "preset": name, **overrides}
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass
class BadgeConfig:
    enabled: bool = True
    text: str = ""
    corner: str = "bottom-left"
    order: int = 0           # position inside a corner stack (lower = closer to the corner)
    style: BadgeStyle = field(default_factory=BadgeStyle)

    @classmethod
    def from_dict(cls, d: Optional[dict], default: Optional["BadgeConfig"] = None) -> "BadgeConfig":
        d = d or {}
        base = default or cls()
        corner = d.get("corner", base.corner)
        enabled = d.get("enabled", base.enabled)
        return cls(
            enabled=enabled if isinstance(enabled, bool) else base.enabled,
            text=str(d.get("text", base.text))[:60],
            corner=corner if corner in CORNERS else base.corner,
            order=int(_clamp(d.get("order"), base.order, -99, 99)),
            style=BadgeStyle.from_dict(d["style"]) if "style" in d else base.style,
        )

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "text": self.text, "corner": self.corner,
                "order": self.order, "style": self.style.to_dict()}


RenderBadge = namedtuple("RenderBadge", "text corner order style")


def render_text(template: str, date: str = "", days: Optional[int] = None) -> str:
    """Expand the ``{date}`` and ``{days}`` placeholders (plain replace, so user-provided braces can never raise)."""
    out = template.replace("{date}", date)
    out = out.replace("{days}", "" if days is None else str(days))
    return out.strip()


def _font(size: int, bold: bool):
    for name in _FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgba(hex_color: str, opacity: float):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(round(255 * opacity)))


_SS = 3          # supersampling of the badge shape (smooth rounded corners)


def _layout(img_size, text, corner, offset, style: BadgeStyle, measure):
    """Geometry of one badge: (x1, y1, w, h, font, text size/offset, vertical space it uses)."""
    width, height = img_size
    badge_h = max(int(height * style.size / 100), 14)
    margin = int(width * 0.04)
    pad_x = int(badge_h * 0.4)
    gap = max(int(badge_h * 0.25), 3)
    bold = style.weight == "bold"
    font_size = max(int(badge_h * 0.6), 8)
    font = _font(font_size, bold)
    bbox = measure.textbbox((0, 0), text, font=font)
    # Shrink the font if the label would not fit inside the poster.
    while bbox[2] - bbox[0] + pad_x * 2 > width - margin * 2 and font_size > 8:
        font_size -= 1
        font = _font(font_size, bold)
        bbox = measure.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    badge_w = text_w + pad_x * 2
    if corner.endswith("left"):
        x1 = margin
    elif corner.endswith("right"):
        x1 = width - margin - badge_w
    else:
        x1 = (width - badge_w) // 2
    y1 = margin + offset if corner.startswith("top") else height - margin - badge_h - offset
    return x1, y1, badge_w, badge_h, font, (text_w, text_h, bbox[1], pad_x), badge_h + gap


def _shape_mask(w, h, radius, inset=0):
    """Anti-aliased rounded rectangle mask of size w x h."""
    big = Image.new("L", (w * _SS, h * _SS), 0)
    ImageDraw.Draw(big).rounded_rectangle([inset * _SS, inset * _SS, (w - inset) * _SS - 1, (h - inset) * _SS - 1],
                                          radius=radius * _SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def _paint_badge(canvas: Image.Image, text, corner, offset, style: BadgeStyle):
    """Draw one badge onto ``canvas`` (RGBA) and return the vertical space it used."""
    probe = ImageDraw.Draw(canvas)
    x1, y1, bw, bh, font, (tw, th, toff, pad_x), used = _layout(canvas.size, text, corner, offset, style, probe)
    radius = min(bh * style.radius / 100, bh / 2)
    cw, ch = canvas.size
    spread = int(bh * 0.9) if (style.glow > 0 or style.shadow > 0) else 0      # room for glow / shadow around the badge
    px0, py0 = max(x1 - spread, 0), max(y1 - spread, 0)
    px1, py1 = min(x1 + bw + spread, cw), min(y1 + bh + spread, ch)
    ox, oy = x1 - px0, y1 - py0                                                   # badge position inside the patch
    patch = canvas.crop((px0, py0, px1, py1))
    pw, ph = patch.size
    mask = Image.new("L", (pw, ph), 0)
    mask.paste(_shape_mask(bw, bh, radius), (ox, oy))

    if style.shadow > 0:
        sh = Image.new("L", (pw, ph), 0)
        sh.paste(mask.crop((0, 0, pw, ph)), (0, max(int(bh * 0.08), 1)))
        sh = sh.filter(ImageFilter.GaussianBlur(bh * 0.18)).point(lambda v: int(v * style.shadow * 0.8))
        patch = Image.composite(Image.new("RGBA", (pw, ph), (0, 0, 0, 255)), patch, sh)
    if style.glow > 0 and style.border_opacity > 0:
        gl = mask.filter(ImageFilter.GaussianBlur(bh * 0.28)).point(lambda v: min(int(v * style.glow * 1.4), 255))
        patch = Image.composite(Image.new("RGBA", (pw, ph), _rgba(style.border, 1.0)), patch, gl)
    if style.blur > 0:                                                            # frosted glass: blur what is behind
        back = patch.filter(ImageFilter.GaussianBlur(max(bh * style.blur / 100, 0.5)))
        patch = Image.composite(back, patch, mask)

    # fill (flat or gradient) limited to the badge shape
    fill = Image.new("RGBA", (pw, ph), _rgba(style.bg, 1.0))
    if style.gradient:
        top, bottom = _rgba(style.bg, 1.0), _rgba(style.gradient, 1.0)
        grad = Image.new("RGBA", (1, bh), (0, 0, 0, 0))
        for y in range(bh):
            t = y / max(bh - 1, 1)
            grad.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,))
        fill.paste(grad.resize((bw, bh)), (ox, oy))
    alpha = mask.point(lambda v: int(v * style.bg_opacity))
    patch = Image.composite(fill, patch, alpha)
    if style.shine > 0:                                                           # highlight fading out towards the middle
        hl = Image.new("L", (bw, bh), 0)
        hd = ImageDraw.Draw(hl)
        for y in range(int(bh * 0.55)):
            hd.line([(0, y), (bw, y)], fill=int(255 * style.shine * (1 - y / (bh * 0.55))))
        hl_full = Image.new("L", (pw, ph), 0)
        hl_full.paste(hl, (ox, oy))
        patch = Image.composite(Image.new("RGBA", (pw, ph), (255, 255, 255, 255)), patch, ImageChops.multiply(hl_full, mask))
    if style.border_width > 0 and style.border_opacity > 0:
        ring = ImageChops.subtract(mask, Image.new("L", (pw, ph), 0).copy())
        inner = Image.new("L", (pw, ph), 0)
        iw, ih = bw - 2 * style.border_width, bh - 2 * style.border_width
        if iw > 0 and ih > 0:
            inner.paste(_shape_mask(iw, ih, max(radius - style.border_width, 0)), (ox + style.border_width, oy + style.border_width))
        ring = ImageChops.subtract(mask, inner).point(lambda v: int(v * style.border_opacity))
        patch = Image.composite(Image.new("RGBA", (pw, ph), _rgba(style.border, 1.0)), patch, ring)

    ImageDraw.Draw(patch).text((ox + pad_x, oy + (bh - th) // 2 - toff), text, fill=_rgba(style.fg, 1.0), font=font)
    canvas.paste(patch, (px0, py0))
    return used


def compose_badges(original_bytes: bytes, badges: Iterable[RenderBadge]) -> bytes:
    """
    Pure function: original JPEG + badges -> JPEG with the badges drawn.

    Badges sharing a corner are stacked away from the edge in ``order``.
    """
    canvas = Image.open(BytesIO(original_bytes)).convert("RGBA")
    by_corner: dict = {}
    for b in badges:
        if b.text:
            by_corner.setdefault(b.corner, []).append(b)
    for corner, items in by_corner.items():
        offset = 0
        for b in sorted(items, key=lambda x: x.order):  # sorted() is stable
            offset += _paint_badge(canvas, b.text, corner, offset, b.style)
    out = BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()


def sample_poster(width: int = 360) -> bytes:
    """A neutral generated poster for the live preview."""
    height = int(width * 1.5)
    img = Image.new("RGB", (width, height), (16, 36, 58))
    d = ImageDraw.Draw(img)
    top, bottom = (38, 159, 232), (7, 13, 22)
    for y in range(height):
        t = y / height
        d.line([(0, y), (width, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    cx, cy, r = width // 2, int(height * 0.46), int(width * 0.2)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(243, 247, 251), width=max(3, width // 70))
    d.polygon([(cx - r // 3, cy - r // 2), (cx - r // 3, cy + r // 2), (cx + r // 2, cy)], fill=(243, 247, 251))
    out = BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()
