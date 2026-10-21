#!/usr/bin/env python3
"""Render the trailer's text cards as transparent 3840x2160 PNGs for ffmpeg to overlay (its drawtext crashes here).

Usage:
  cards.py DESIGN.json OUT.png TEXT [--sub TEXT] [--pos default|top|centre|lower] [--bg FRAME.png]

DESIGN is one design from the trailer editor page (its banner or title-card settings, in px at 4K): font, size,
subScale, upper, tracking (must be 0), skew, text, subColor, outline, outlineColor, shadow, band (full, fit, pill,
slant, none), bandColor, bandOpacity, padX, padY, stripe (off, below, above, left), stripeSize, pos, margin, align
(centre, left). Its anim and animDur are edit.py's.
The layout follows the page's canvas renderer, so a card here matches its preview. --bg composites onto a frame.
Fonts are read from Windows' font folder at render time; none is stored in the tree.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 3840, 2160
FONTS = Path("C:/Windows/Fonts")
FONT_FILES = {"gotham": "Gotham-Black.otf", "segoe": "seguibl.ttf", "arialblack": "ariblk.ttf"}
# P1-4 the game's orange, blue, pink and green; P5-8 SplitScreen8/Colours.cpp's purple, white, brown and teal.
PLAYERS = [(255, 140, 0), (40, 120, 255), (255, 90, 160), (60, 200, 60),
           (150, 70, 255), (255, 255, 255), (170, 100, 40), (0, 190, 170)]


def font(d: dict, size: int) -> ImageFont.FreeTypeFont:
    if d["font"] not in FONT_FILES:
        raise SystemExit(f"font {d['font']!r} is not on this PC's list: {', '.join(FONT_FILES)}")
    return ImageFont.truetype(str(FONTS / FONT_FILES[d["font"]]), size)


def rgb(hex_: str, alpha: int = 255) -> tuple:
    n = int(hex_[1:], 16)
    return (n >> 16 & 255, n >> 8 & 255, n & 255, alpha)


def text_line(d: dict, text: str, px: int, colour: str) -> tuple[Image.Image, int, int]:
    """One line on its own layer, slanted about its baseline; returns the layer and the baseline's position in it."""
    f, pad, grow = font(d, px), px // 2, int(d["skew"] * px) + 1
    width = int(f.getlength(text))
    layer = Image.new("RGBA", (width + 2 * pad + grow, int(px * 1.6) + 2 * pad), (0, 0, 0, 0))
    base = pad + int(px * 1.1)
    ImageDraw.Draw(layer).text((pad, base), text, font=f, anchor="ls", fill=rgb(colour), stroke_width=d["outline"],
                               stroke_fill=rgb(d["outlineColor"]))
    if d["skew"]:
        k = d["skew"]
        layer = layer.transform(layer.size, Image.AFFINE, (1, k, -k * base, 0, 1, 0), Image.BICUBIC)
    if d["shadow"]:
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        shadow.putalpha(layer.getchannel("A").point(lambda a: a * d["shadow"] // 100))
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        out.alpha_composite(shadow, (9, 9))
        out.alpha_composite(layer)
        layer = out
    return layer, pad, base


def render(d: dict, text: str, sub: str | None, pos: str = "default") -> tuple[Image.Image, int]:
    """The card, and how far a slide-from-the-left entrance travels (px at 4K)."""
    if d["tracking"]:
        raise SystemExit("letter spacing is not rendered here yet: set it to 0 or add it")
    pos = d["pos"] if pos == "default" else pos
    case = (lambda s: s.upper()) if d["upper"] else (lambda s: s)
    lines = [(case(text), d["size"], d["text"])] + [(case(s), round(d["size"] * d["subScale"]), d["subColor"])
                                                     for s in (sub or "").split("\n") if s]
    max_w = W * 0.88 if d["band"] == "full" or d["align"] == "centre" else W - 2 * d["margin"] - 2 * d["padX"]
    sized = []
    for line, px, colour in lines:
        # The step and the floor are the editor preview's, so a shrunk line matches it.
        while px > 16 and font(d, px).getlength(line) > max_w:
            px -= 6
        sized.append((line, px, colour, font(d, px).getlength(line)))
    bw = max(w for *_, w in sized) + d["skew"] * sized[0][1]
    bh = sum(px * 1.18 for _, px, _, _ in sized)
    m, pad_x, pad_y = d["margin"], d["padX"], d["padY"]
    top = m if pos == "top" else (H - bh) / 2 if pos == "centre" else H - m - bh
    left = m + (0 if d["band"] == "none" else pad_x) if d["align"] == "left" else (W - bw) / 2
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    bx, by = (0, top - pad_y) if d["band"] == "full" else (left - pad_x, top - pad_y)
    b_w, b_h = (W if d["band"] == "full" else bw + 2 * pad_x), bh + 2 * pad_y
    fill = rgb(d["bandColor"], round(d["bandOpacity"] * 255 / 100))
    if d["band"] == "pill":
        draw.rounded_rectangle((bx, by, bx + b_w, by + b_h), radius=b_h / 2, fill=fill)
    elif d["band"] == "slant":
        k = b_h * 0.32
        draw.polygon([(bx + k, by), (bx + b_w + k, by), (bx + b_w - k, by + b_h), (bx - k, by + b_h)], fill=fill)
    elif d["band"] != "none":
        draw.rectangle((bx, by, bx + b_w, by + b_h), fill=fill)
    st = d["stripeSize"]
    if d["stripe"] == "left":
        x, seg = (left - st * 2.2 if d["band"] == "none" else bx - st), b_h / 8
        for i, c in enumerate(PLAYERS):
            draw.rectangle((x, by + i * seg, x + st, by + (i + 1) * seg), fill=c)
    elif d["stripe"] in ("below", "above"):
        x0, w0 = (left, bw) if d["band"] == "none" else (bx, b_w)
        if d["band"] == "pill":
            x0, w0 = x0 + b_h / 2, max(0, w0 - b_h)
        if d["stripe"] == "below":
            y = top + bh + 6 if d["band"] == "none" else by + b_h
        else:
            y = top - st - 6 if d["band"] == "none" else by - st
        for i, c in enumerate(PLAYERS):
            draw.rectangle((x0 + i * w0 / 8, y, x0 + (i + 1) * w0 / 8, y + st), fill=c)
    y = top
    for line, px, colour, w in sized:
        layer, pad, base = text_line(d, line, px, colour)
        x = left if d["align"] == "left" else (W - w) / 2
        card.alpha_composite(layer, (round(x - pad), round(y + px * 0.93 - base)))
        y += px * 1.18
    return card, round(bw + 2 * pad_x + m)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("design", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("text")
    ap.add_argument("--sub")
    ap.add_argument("--pos", choices=("default", "top", "centre", "lower"), default="default")
    ap.add_argument("--bg", type=Path, help="composite onto this frame (scaled to 3840x2160)")
    args = ap.parse_args()
    card, _ = render(json.loads(args.design.read_text(encoding="utf-8")), args.text, args.sub, args.pos)
    if args.bg:
        card = Image.alpha_composite(Image.open(args.bg).convert("RGBA").resize((W, H), Image.LANCZOS), card)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    card.save(args.out)


if __name__ == "__main__":
    main()
