"""Buildo bot scene images — programmatic SVG generator + PNG converter.

Generates scene images in unified Buildo brand style (midnight + cyan + amber).
No LLM dependency, no API calls, instant.

SVG is converted to PNG via cairosvg for Telegram compatibility.
"""

from __future__ import annotations

import math
import random
from typing import Sequence, cast

# Buildo brand palette (from 12-brand-book.md)
COLORS = {
    "midnight": "#0A1628",
    "cyan": "#06B6D4",
    "amber": "#F59E0B",
    "paper": "#FDFCF8",
    "ink": "#0F172A",
    "muted": "#64748B",
}


def _gradient_def(id_: str, c1: str, c2: str) -> str:
    return (
        f'<defs><linearGradient id="{id_}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{c1}"/><stop offset="100%" stop-color="{c2}"/>'
        f"</linearGradient></defs>"
    )


def _wrap(content: str, w: int = 800, h: int = 600) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<rect width="100%" height="100%" fill="{COLORS["midnight"]}"/>{content}</svg>'
    )


def welcome() -> bytes:
    """Hero scene: hammer hitting a webpage block."""
    parts: list[str] = [_gradient_def("g1", COLORS["midnight"], "#0F1F3A")]
    # Floating geometric shapes
    random.seed(42)
    for _ in range(8):
        x = random.randint(50, 750)
        y = random.randint(50, 550)
        s = random.randint(20, 60)
        c = random.choice([COLORS["cyan"], COLORS["amber"], COLORS["paper"]])
        op = random.uniform(0.1, 0.4)
        shape = random.choice(["circle", "rect", "tri"])
        if shape == "circle":
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="{s//2}" fill="{c}" opacity="{op:.2f}"/>'
            )
        elif shape == "rect":
            parts.append(
                f'<rect x="{x}" y="{y}" width="{s}" height="{s}" fill="{c}" opacity="{op:.2f}"/>'
            )
        else:
            parts.append(
                f'<polygon points="{x},{y-s} {x-s},{y+s} {x+s},{y+s}" fill="{c}" opacity="{op:.2f}"/>'
            )
    # Central webpage block
    parts.append(
        f'<rect x="280" y="180" width="240" height="320" rx="8" fill="{COLORS["paper"]}" opacity="0.95"/>'
    )
    # Page lines
    for i, w_ in enumerate([200, 160, 180, 140]):
        parts.append(
            f'<rect x="300" y="{220 + i*40}" width="{w_}" height="8" rx="4" fill="{COLORS["ink"]}" opacity="0.3"/>'
        )
    # Cyan CTA
    parts.append(
        f'<rect x="300" y="420" width="120" height="36" rx="18" fill="{COLORS["cyan"]}"/>'
    )
    # Hammer
    parts.append(
        f'<g transform="translate(540,140) rotate(-30)">'
        f'<rect x="0" y="0" width="120" height="20" fill="{COLORS["amber"]}"/>'
        f'<rect x="110" y="-15" width="30" height="50" fill="{COLORS["amber"]}"/>'
        f'<rect x="55" y="20" width="10" height="80" fill="{COLORS["paper"]}"/>'
        f"</g>"
    )
    # Spark particles
    for i in range(12):
        angle = (i / 12) * math.tau
        x = 400 + 60 * math.cos(angle)
        y = 200 + 60 * math.sin(angle)
        parts.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3" fill="{COLORS["cyan"]}"/>'
        )
    return _wrap("".join(parts)).encode("utf-8")


def no_sites() -> bytes:
    """Empty state: cardboard box with amber light."""
    parts: list[str] = []
    # Glow behind box
    parts.append(
        f'<circle cx="400" cy="350" r="120" fill="{COLORS["amber"]}" opacity="0.15"/>'
    )
    # Box (open)
    parts.append(
        f'<polygon points="280,300 520,300 540,400 260,400" fill="{COLORS["paper"]}" opacity="0.9"/>'
    )
    parts.append(
        f'<polygon points="280,300 400,260 520,300 400,340" fill="{COLORS["paper"]}" stroke="{COLORS["ink"]}" stroke-width="2" opacity="0.95"/>'
    )
    # Light inside box
    parts.append(f'<circle cx="400" cy="320" r="15" fill="{COLORS["amber"]}"/>')
    parts.append(
        f'<circle cx="400" cy="320" r="25" fill="{COLORS["amber"]}" opacity="0.4"/>'
    )
    # Floating dots
    random.seed(7)
    for _ in range(15):
        x = random.randint(50, 750)
        y = random.randint(50, 550)
        if 260 < x < 540 and 280 < y < 400:  # not in box
            continue
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{random.randint(2,4)}" fill="{COLORS["cyan"]}" opacity="{random.uniform(0.3,0.7):.2f}"/>'
        )
    return _wrap("".join(parts)).encode("utf-8")


def generating() -> bytes:
    """AI generating: gears with sparks."""
    parts: list[str] = []
    # Two interlocking gears
    parts.append(
        f'<g transform="translate(300,300)">'
        f'<circle r="80" fill="{COLORS["cyan"]}"/>'
        f'<circle r="40" fill="{COLORS["midnight"]}"/>'
        f'<g>'
    )
    for i in range(8):
        angle = (i / 8) * 360
        parts.append(
            f'<rect x="-10" y="-90" width="20" height="20" fill="{COLORS["cyan"]}" transform="rotate({angle})"/>'
        )
    parts.append("</g></g>")
    # Second gear
    parts.append(
        f'<g transform="translate(500,300)">'
        f'<circle r="60" fill="{COLORS["amber"]}"/>'
        f'<circle r="30" fill="{COLORS["midnight"]}"/>'
        f"<g>"
    )
    for i in range(6):
        angle = (i / 6) * 360 + 30
        parts.append(
            f'<rect x="-8" y="-70" width="16" height="16" fill="{COLORS["amber"]}" transform="rotate({angle})"/>'
        )
    parts.append("</g></g>")
    # Sparks
    for i in range(20):
        x = 400 + (i % 5 - 2) * 100 + random.randint(-30, 30)
        y = 300 + (i // 5 - 2) * 60 + random.randint(-30, 30)
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="3" fill="{COLORS["amber"]}" opacity="{random.uniform(0.4,0.9):.2f}"/>'
        )
    # Code brackets
    parts.append(
        f'<text x="200" y="200" fill="{COLORS["cyan"]}" font-size="40" font-family="monospace">&lt;/&gt;</text>'
    )
    parts.append(
        f'<text x="600" y="450" fill="{COLORS["amber"]}" font-size="40" font-family="monospace">{{}}</text>'
    )
    return _wrap("".join(parts)).encode("utf-8")


def editing() -> bytes:
    """Editing scene: magnifying glass over document with diff lines."""
    parts: list[str] = []
    # Document
    parts.append(
        f'<rect x="200" y="150" width="400" height="320" rx="8" fill="{COLORS["paper"]}" opacity="0.95"/>'
    )
    # Title
    parts.append(
        f'<rect x="230" y="180" width="240" height="14" rx="3" fill="{COLORS["ink"]}" opacity="0.6"/>'
    )
    # Body lines
    for i in range(5):
        y = 220 + i * 30
        parts.append(
            f'<rect x="230" y="{y}" width="{320 - i*20}" height="8" rx="4" fill="{COLORS["ink"]}" opacity="0.25"/>'
        )
    # Diff lines (+/-)
    for i in range(3):
        y = 380 + i * 20
        parts.append(
            f'<text x="230" y="{y}" fill="#10B981" font-size="14" font-family="monospace">+ добавлено</text>'
        )
        parts.append(
            f'<text x="230" y="{y + 12}" fill="#EF4444" font-size="12" font-family="monospace">- удалено</text>'
        )
    # Magnifying glass (cyan)
    parts.append(
        f'<circle cx="540" cy="220" r="50" fill="none" stroke="{COLORS["cyan"]}" stroke-width="6"/>'
    )
    parts.append(
        f'<circle cx="540" cy="220" r="50" fill="{COLORS["cyan"]}" opacity="0.1"/>'
    )
    parts.append(
        f'<line x1="575" y1="255" x2="620" y2="300" stroke="{COLORS["cyan"]}" stroke-width="8" stroke-linecap="round"/>'
    )
    return _wrap("".join(parts)).encode("utf-8")


def published() -> bytes:
    """Success: rocket launch with cyan trail."""
    parts: list[str] = []
    # Ground
    parts.append(
        f'<rect x="0" y="500" width="800" height="100" fill="{COLORS["paper"]}" opacity="0.05"/>'
    )
    # Rocket body (amber + cyan)
    parts.append(
        f'<g transform="translate(380,300)">'
        # Body
        f'<rect x="0" y="0" width="40" height="100" fill="{COLORS["amber"]}" rx="4"/>'
        # Window
        f'<circle cx="20" cy="30" r="10" fill="{COLORS["cyan"]}"/>'
        # Nose
        f'<polygon points="0,0 20,-30 40,0" fill="{COLORS["cyan"]}"/>'
        # Fins
        f'<polygon points="0,80 -15,110 0,100" fill="{COLORS["cyan"]}"/>'
        f'<polygon points="40,80 55,110 40,100" fill="{COLORS["cyan"]}"/>'
        f"</g>"
    )
    # Trail
    for i in range(20):
        y = 410 + i * 6
        w = 40 + i * 2
        x = 400 - w // 2
        op = 1 - i / 20
        col = COLORS["amber"] if i < 10 else COLORS["cyan"]
        parts.append(
            f'<polygon points="{x},{y} {x+w},{y} {x+w-10},{y+8} {x+10},{y+8}" fill="{col}" opacity="{op:.2f}"/>'
        )
    # Stars
    random.seed(11)
    for _ in range(30):
        x = random.randint(50, 750)
        y = random.randint(50, 350)
        r = random.randint(1, 3)
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="{COLORS["paper"]}" opacity="{random.uniform(0.3,1):.2f}"/>'
        )
    return _wrap("".join(parts)).encode("utf-8")


def menu() -> bytes:
    """Main menu: three floating cards."""
    parts: list[str] = []
    # Three cards
    cards: Sequence[tuple[int, int, str, str]] = [
        (130, 180, COLORS["cyan"], "Сайт 1"),
        (350, 150, COLORS["amber"], "Сайт 2"),
        (570, 200, COLORS["paper"], "+ Новый"),
    ]
    for x, y, col, _label in cards:
        parts.append(
            f'<rect x="{x}" y="{y}" width="120" height="160" rx="12" fill="{col}" opacity="0.9"/>'
        )
        parts.append(
            f'<rect x="{x+15}" y="{y+15}" width="90" height="60" rx="4" fill="{COLORS["midnight"]}" opacity="0.3"/>'
        )
        parts.append(
            f'<rect x="{x+15}" y="{y+90}" width="{60+random.randint(0,30)}" height="6" rx="3" fill="{COLORS["midnight"]}" opacity="0.5"/>'
        )
        parts.append(
            f'<rect x="{x+15}" y="{y+105}" width="{40+random.randint(0,40)}" height="6" rx="3" fill="{COLORS["midnight"]}" opacity="0.3"/>'
        )
    # Connection lines
    parts.append(
        f'<line x1="250" y1="260" x2="350" y2="230" stroke="{COLORS["cyan"]}" stroke-width="2" opacity="0.5" stroke-dasharray="5,5"/>'
    )
    parts.append(
        f'<line x1="470" y1="240" x2="570" y2="280" stroke="{COLORS["cyan"]}" stroke-width="2" opacity="0.5" stroke-dasharray="5,5"/>'
    )
    return _wrap("".join(parts)).encode("utf-8")


def error() -> bytes:
    """Error scene: broken puzzle piece."""
    parts: list[str] = []
    # Large puzzle piece (mostly intact)
    parts.append(
        f'<g transform="translate(250,200)">'
        f'<rect x="0" y="0" width="200" height="200" rx="10" fill="{COLORS["cyan"]}" opacity="0.7"/>'
        f"</g>"
    )
    # Detached piece (amber, falling)
    parts.append(
        f'<g transform="translate(450,330) rotate(15)">'
        f'<rect x="0" y="0" width="80" height="80" rx="6" fill="{COLORS["amber"]}" opacity="0.8"/>'
        f"</g>"
    )
    # Warning triangle
    parts.append(
        f'<g transform="translate(380,100)">'
        f'<polygon points="0,-30 30,20 -30,20" fill="{COLORS["amber"]}" stroke="{COLORS["midnight"]}" stroke-width="3"/>'
        f'<rect x="-3" y="-15" width="6" height="20" fill="{COLORS["midnight"]}"/>'
        f'<circle cx="0" cy="12" r="3" fill="{COLORS["midnight"]}"/>'
        f"</g>"
    )
    return _wrap("".join(parts)).encode("utf-8")


def referral() -> bytes:
    """3-level referral: three connected nodes."""
    parts: list[str] = []
    # Triangle of nodes
    nodes = [(400, 150), (250, 400), (550, 400)]
    # Connection lines (glowing)
    for i in range(3):
        x1, y1 = nodes[i]
        x2, y2 = nodes[(i + 1) % 3]
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS["cyan"]}" stroke-width="3" opacity="0.6"/>'
        )
        # Glow
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS["cyan"]}" stroke-width="8" opacity="0.2"/>'
        )
    # Nodes
    for i, (x, y) in enumerate(nodes):
        size = 50 - i * 5  # biggest = top
        col = [COLORS["amber"], COLORS["cyan"], COLORS["paper"]][i]
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{size}" fill="{col}" opacity="0.9"/>'
        )
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{size + 15}" fill="{col}" opacity="0.2"/>'
        )
    # Central star/coin
    parts.append(f'<circle cx="400" cy="320" r="25" fill="{COLORS["amber"]}"/>')
    parts.append(
        f'<text x="400" y="328" text-anchor="middle" fill="{COLORS["midnight"]}" font-size="24" font-weight="bold">★</text>'
    )
    return _wrap("".join(parts)).encode("utf-8")


def payment() -> bytes:
    """Payment scene: credit card with checkmark."""
    parts: list[str] = []
    # Card
    parts.append(
        '<g transform="translate(200,200)">'
        '<rect x="0" y="0" width="400" height="240" rx="16" fill="url(#g1)" />'
        "</g>"
    )
    parts.insert(0, _gradient_def("g1", "#06B6D4", "#0E7490"))
    # Chip
    parts.append(
        f'<rect x="240" y="260" width="50" height="40" rx="4" fill="{COLORS["amber"]}"/>'
    )
    # Card number
    for i, x in enumerate([240, 320, 400]):
        parts.append(
            f'<text x="{x}" y="350" fill="{COLORS["paper"]}" font-size="22" font-family="monospace">**** {i+1}</text>'
        )
    # Floating coins
    for i, (x, y) in enumerate([(680, 150), (650, 350), (120, 380)]):
        parts.append(f'<circle cx="{x}" cy="{y}" r="25" fill="{COLORS["amber"]}"/>')
        parts.append(
            f'<text x="{x}" y="{y+8}" text-anchor="middle" fill="{COLORS["midnight"]}" font-size="20" font-weight="bold">₽</text>'
        )
    # Checkmark
    parts.append(
        f'<g transform="translate(560,260)">'
        f'<circle r="35" fill="{COLORS["cyan"]}"/>'
        f'<polyline points="-15,0 -5,12 15,-12" fill="none" stroke="{COLORS["midnight"]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        f"</g>"
    )
    return _wrap("".join(parts)).encode("utf-8")


def admin() -> bytes:
    """Admin panel: switches, sliders, gauges."""
    parts: list[str] = []
    # Panel frame
    parts.append(
        f'<rect x="100" y="100" width="600" height="400" rx="12" fill="{COLORS["paper"]}" opacity="0.05" stroke="{COLORS["cyan"]}" stroke-width="2"/>'
    )
    # Switches
    for i, on in enumerate([True, False, True, True]):
        y = 150 + i * 50
        parts.append(
            f'<rect x="140" y="{y}" width="80" height="30" rx="15" fill="{COLORS["cyan"] if on else COLORS["muted"]}" opacity="0.8"/>'
        )
        parts.append(
            f'<circle cx="{185 if on else 175}" cy="{y+15}" r="12" fill="{COLORS["paper"]}"/>'
        )
    # Sliders
    for i, val in enumerate([0.7, 0.4, 0.85]):
        y = 370 + i * 40
        parts.append(
            f'<line x1="280" y1="{y}" x2="680" y2="{y}" stroke="{COLORS["muted"]}" stroke-width="4" opacity="0.3"/>'
        )
        parts.append(
            f'<line x1="280" y1="{y}" x2="{int(280 + 400*val)}" y2="{y}" stroke="{COLORS["amber"]}" stroke-width="4"/>'
        )
        parts.append(
            f'<circle cx="{int(280 + 400*val)}" cy="{y}" r="10" fill="{COLORS["amber"]}"/>'
        )
    # Gauge
    parts.append(
        f'<g transform="translate(620,150)">'
        f'<path d="M -40 0 A 40 40 0 0 1 40 0" fill="none" stroke="{COLORS["muted"]}" stroke-width="6" opacity="0.3"/>'
        f'<path d="M -40 0 A 40 40 0 0 1 30 -27" fill="none" stroke="{COLORS["cyan"]}" stroke-width="6"/>'
        f"</g>"
    )
    return _wrap("".join(parts)).encode("utf-8")


# Scene registry
SCENES = {
    "welcome": welcome,
    "no_sites": no_sites,
    "generating": generating,
    "editing": editing,
    "published": published,
    "menu": menu,
    "error": error,
    "referral": referral,
    "payment": payment,
    "admin": admin,
}


def get_scene(name: str) -> bytes:
    """Return PNG bytes for a named scene. Defaults to 'welcome'.

    Generates at 800x600 (Telegram-friendly size, < 30KB) and converts
    to RGB-mode PNG. Smaller images = better Telegram compatibility.
    """
    fn = SCENES.get(name, welcome)
    svg_bytes = fn()
    try:
        import io

        import cairosvg
        from PIL import Image

        # Generate at moderate size — 800x600 is well-supported by Telegram
        png_raw_raw: object = cairosvg.svg2png(
            bytestring=svg_bytes, output_width=800, output_height=600
        )
        png_raw = cast(bytes, png_raw_raw)
        # Open with PIL, convert to RGB (Telegram sometimes rejects RGBA),
        # and save as optimized PNG
        img = Image.open(io.BytesIO(png_raw))
        if img.mode == "RGBA":
            # Flatten on midnight background (matches brand)
            bg = Image.new("RGB", img.size, (10, 22, 40))  # #0A1628
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return svg_bytes
