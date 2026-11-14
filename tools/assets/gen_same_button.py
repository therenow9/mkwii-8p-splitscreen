#!/usr/bin/env python3
"""Add the Same as Last Time hint, SS8SameHint, to a menu archive.

The character page's Same as Last Time hint (rr-pulsar PulsarEngine/SplitScreen8) sits in the game's bottom
instruction bar: "Hold", P1's controller's button, then ": Same as Last Time". The game's font has no - or Z glyph,
so the hint carries two icons of ours: a - for a Wii Remote or Classic and a Z for a GameCube pad. They are original
art, drawn here from shapes (no game data, nothing traced from it), and encoded as RGBA8 TPL textures.

The hint itself is derived from the user's own archive at install time: SS8SameHint.brctr is the game's
MenuObiInstructionText control, lower by DROP, on a copy of its layout, obi_bottom_message_ss8, which adds one
picture pane per icon (ss8_minus, ss8_z) inside the text, where the code leaves a gap for it. The code shows one of
the two. Every member the archive held before is checked unchanged.

Nothing derived from game files is stored in this repository; run it against the archive of the copy you are
installing into: its UI/MenuSingle.szs (the 1P menus) and UI/MenuMulti.szs, Retro Rewind's or, without Retro Rewind,
the disc's Scene/UI ones.

Usage:
  gen_same_button.py <in .szs> [<ReplacedAssets.szs>] <out .szs>
"""
from __future__ import annotations

import argparse
import copy
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "external" / "wuj5"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from brctr import pack_brctr, unpack_brctr  # noqa: E402
from brlyt import pack_brlyt, unpack_brlyt  # noqa: E402
import u8  # noqa: E402
from yaz import pack_yaz, unpack_yaz  # noqa: E402
from gen_charselect_multi import find_dir, find_pane, snapshot  # noqa: E402

FOLDER = "bg"
SOURCE_CTRL = "MenuObiInstructionText.brctr"
SOURCE_LAYOUT = "obi_bottom_message"
CTRL = "SS8SameHint.brctr"
LAYOUT = SOURCE_LAYOUT + "_ss8"
VARIANT = "SS8SameHint"
DROP = 18.0  # at the bar's stock height its band covers the bottom of the character page's Mii row
# The text is right-aligned at its pane's right edge; the icon's right edge is this far left of it, where the English
# text ": Same as Last Time" begins (measured from renders).
ICON_RIGHT = -152.0
ICON_H = 24.0

# ---- the art -------------------------------------------------------------------------------------------------------
# Each icon is a list of layers drawn back to front; each layer a signed-distance shape and a fill. Coordinates are in
# texture pixels, origin at the centre, y down.


def sd_circle(r):
    return lambda x, y: math.hypot(x, y) - r


def sd_capsule(ax, ay, bx, by, r):
    def f(x, y):
        px, py, dx, dy = x - ax, y - ay, bx - ax, by - ay
        h = max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - dx * h, py - dy * h) - r
    return f


def sd_union(*shapes):
    return lambda x, y: min(s(x, y) for s in shapes)


def sd_minus(half, t):
    return sd_capsule(-half, 0.0, half, 0.0, t)


def sd_z(half, t):
    # A Z of three strokes: top bar, diagonal, bottom bar.
    return sd_union(sd_capsule(-half, -half, half, -half, t), sd_capsule(half, -half, -half, half, t),
                    sd_capsule(-half, half, half, half, t))


def vertical(top, bottom, h):
    # A fill shading from `top` at the shape's top edge to `bottom` at its bottom edge.
    return lambda x, y: tuple(int(round(a + (b - a) * max(0.0, min(1.0, (y + h) / (2 * h))))) for a, b in zip(top, bottom))


def flat(color):
    return lambda x, y: color


def badge(body, size, outline, fill, symbol, ink, gloss=True):
    """A button: an outline, a shaded body, a soft highlight on its upper half, and its symbol.

    `body(inset)` is the button's shape shrunk by `inset` pixels."""
    w, h = size
    layers = [(body(1.0), flat(outline)), (body(3.5), fill(h / 2 - 3.5))]
    if gloss:
        inner = body(5.0)
        layers.append((lambda x, y: max(inner(x, y), (math.hypot(x / (w * 0.36), (y + h * 0.22) / (h * 0.2)) - 1.0) * h * 0.06),
                       flat((255, 255, 255, 90))))
    layers.append((symbol, flat(ink)))
    return size, layers


def disc(size):
    return lambda inset: sd_circle(size[0] / 2 - inset)


def pill(size):
    w, h = size
    return lambda inset: sd_capsule(-(w / 2 - h / 2), 0.0, w / 2 - h / 2, 0.0, h / 2 - inset)


GREY = ((236, 238, 242, 255), (170, 172, 180, 255))
GREY_INK = (96, 98, 106, 255)
GREY_EDGE = (70, 72, 80, 255)
INDIGO = ((124, 104, 238, 255), (66, 44, 170, 255))
INDIGO_EDGE = (34, 22, 96, 255)


def icons() -> dict[str, tuple[tuple[int, int], list]]:
    """The icons by texture name, each the real button's shape and colour: a Wii Remote's round - and a GameCube pad's
    indigo Z, wide as its shoulder button."""
    return {
        "ss8_minus": badge(disc((64, 64)), (64, 64), GREY_EDGE, lambda h: vertical(*GREY, h), sd_minus(11, 4.5), GREY_INK),
        "ss8_z": badge(pill((96, 56)), (96, 56), INDIGO_EDGE, lambda h: vertical(*INDIGO, h), sd_z(11.5, 4.2), (255, 255, 255, 255)),
    }


def render(size: tuple[int, int], layers: list) -> list[tuple[int, int, int, int]]:
    """Straight-alpha RGBA pixels, each layer composited over the last with one pixel of antialiasing."""
    w, h = size
    out = []
    for j in range(h):
        for i in range(w):
            x, y = i + 0.5 - w / 2, j + 0.5 - h / 2
            r = g = b = a = 0.0
            for shape, fill in layers:
                cover = max(0.0, min(1.0, 0.5 - shape(x, y)))
                if cover == 0.0:
                    continue
                fr, fg, fb, fa = fill(x, y)
                sa = cover * fa / 255.0
                na = sa + a * (1 - sa)
                if na > 0:
                    r = (fr * sa + r * a * (1 - sa)) / na
                    g = (fg * sa + g * a * (1 - sa)) / na
                    b = (fb * sa + b * a * (1 - sa)) / na
                a = na
            out.append((int(round(r)), int(round(g)), int(round(b)), int(round(a * 255))))
    return out


def encode_tpl(size: tuple[int, int], pixels: list[tuple[int, int, int, int]]) -> bytes:
    """One RGBA8 (format 6) image in a TPL: 4x4 tiles, each 32 bytes of AR pairs then 32 of GB pairs."""
    w, h = size
    if w % 4 or h % 4:
        raise SystemExit(f"texture size {w}x{h} is not a multiple of 4")
    data = bytearray()
    for ty in range(0, h, 4):
        for tx in range(0, w, 4):
            tile = [pixels[(ty + j) * w + tx + i] for j in range(4) for i in range(4)]
            data += bytes(c for r, g, b, a in tile for c in (a, r))
            data += bytes(c for r, g, b, a in tile for c in (g, b))
    header = struct.pack(">IIIII", 0x0020AF30, 1, 0x0C, 0x14, 0)
    image = struct.pack(">HHIIIIIIfBBBB", h, w, 6, 0x40, 0, 0, 1, 1, 0.0, 0, 0, 0, 0)
    head = header + image
    return head + bytes(0x40 - len(head)) + bytes(data)


def decode_tpl(data: bytes) -> tuple[tuple[int, int], list[tuple[int, int, int, int]]]:
    """The inverse of encode_tpl, for the self-check."""
    h, w, fmt, off = struct.unpack_from(">HHII", data, 0x14)
    if fmt != 6:
        raise SystemExit(f"format {fmt}, not RGBA8")
    px = [None] * (w * h)
    pos = off
    for ty in range(0, h, 4):
        for tx in range(0, w, 4):
            ar, gb = data[pos:pos + 32], data[pos + 32:pos + 64]
            pos += 64
            for k in range(16):
                px[(ty + k // 4) * w + tx + k % 4] = (ar[2 * k + 1], gb[2 * k], gb[2 * k + 1], ar[2 * k])
    return (w, h), px


# ---- the layout ----------------------------------------------------------------------------------------------------






def material(name: str, texture: int) -> dict:
    """A material that draws its texture as it is: TEV colour 0 black, the rest white, one texture map."""
    mat = {"name": name}
    for c, v in (("tev color 0", 0), ("tev color 1", 255), ("tev color 2", 255)):
        for ch in "rgba":
            mat[f"{c} {ch}"] = v
    for k in range(4):
        for ch in "rgba":
            mat[f"tev k color {k} {ch}"] = 255
    mat["attributes"] = {
        "texture maps": [{"texture index": texture, "s": 0, "t": 0}],
        "texture srts": [{"translate x": 0.0, "translate y": 0.0, "rotate": 0.0, "scale x": 1.0, "scale y": 1.0}],
        "texture uv gens": [{"type": 1, "source": 4, "matrix": 30}],
    }
    return mat


def picture(name: str, mat: int, x: float, z: float, size: tuple[float, float], visible: bool) -> dict:
    pane = {
        "magic": "pic1",
        "flags": {"visible": visible, "influenced alpha": False, "location adjust": False},
        "base position": "center", "opacity": 255, "name": name, "user data": "",
        "translation x": x, "translation y": 0.0, "translation z": z,
        "rotation x": 0.0, "rotation y": 0.0, "rotation z": 0.0, "scale x": 1.0, "scale y": 1.0,
        "size x": size[0], "size y": size[1],
    }
    for corner in ("top left", "top right", "bottom left", "bottom right"):
        for ch in "rgba":
            pane[f"vertex color {corner} {ch}"] = 255
    pane["material"] = mat
    pane["uv sets"] = [{"top left u": 0.0, "top left v": 0.0, "top right u": 1.0, "top right v": 0.0,
                        "bottom left u": 0.0, "bottom left v": 1.0, "bottom right u": 1.0, "bottom right v": 1.0}]
    return pane


def same_layout(content: bytes, textures: dict[str, tuple[int, int]]) -> bytes:
    """The bar's layout with a picture pane per texture in the text's line, the first one visible."""
    lyt = unpack_brlyt(content)
    sections = {s["magic"]: s for s in lyt["sections"]}
    tpls, materials, root = sections["txl1"]["tpls"], sections["mat1"]["materials"], sections["pan1"]
    parent, text = find_pane(root, "message_nul"), find_pane(root, "text")
    if parent is None or text is None or text not in parent["children"]:
        raise SystemExit(f"{SOURCE_LAYOUT}: message_nul or its text missing")
    for k, (name, (w, h)) in enumerate(textures.items()):
        tpls.append({"name": f"{name}.tpl"})
        materials.append(material(name, len(tpls) - 1))
        pane = picture(name, len(materials) - 1, text["translation x"] + ICON_RIGHT, text["translation z"],
                       (ICON_H * w / h, ICON_H), k == 0)
        # Anchored as the text is (its right edge, moved with the screen's edge at 16:9), and drawn after it.
        pane["base position"] = text["base position"]
        pane["flags"]["location adjust"] = text["flags"]["location adjust"]
        pane["translation y"] = text["translation y"]
        parent["children"].insert(parent["children"].index(text) + 1 + k, pane)
    return pack_brlyt(lyt)


def same_control(content: bytes) -> bytes:
    ctrl = unpack_brctr(content)
    ctrl["main brlyt"] = LAYOUT
    variant = copy.deepcopy(ctrl["variants"][0])
    variant["name"] = VARIANT
    for aspect in ("4:3", "16:9"):
        variant[f"translation y {aspect}"] -= DROP
        # In front of the page's own bar, whose band is drawn later at the same depth (the 1P pages')
        variant[f"translation z {aspect}"] += 1.0
    ctrl["variants"] = [variant]
    return pack_brctr(ctrl)




def load_szs(path: Path) -> tuple[dict, bool]:
    """The archive's whole tree and whether it was Yaz0. The disc's archives keep everything in a "." folder; Retro
    Rewind's MenuSingle.szs has its folders at the root, which wuj5's unpack_u8 (it keeps only the first) would drop."""
    data = path.read_bytes()
    compressed = data[:4] == b"Yaz0"
    raw = unpack_yaz(data) if compressed else data
    nodes = u8.unpack_u32(raw, 0x4)
    names = nodes + u8.unpack_u32(raw, nodes + 0x8) * 0xC
    return u8.unpack_node(raw, nodes, names, 0)[0], compressed


def top_of(root: dict) -> dict:
    kids = root["children"]
    return kids[0] if len(kids) == 1 and kids[0]["is_dir"] and kids[0]["name"] == "." else root


def pack_szs(root: dict) -> bytes:
    """wuj5's pack_u8, but for the whole tree as loaded, so a root without "." keeps that shape."""
    names = u8.Strings("ascii", b"\0")
    contents = u8.Buffer(0x0)
    count = u8.process_node(root, 0x0, names, contents)
    names_offset = 0x20 + count * 0xC
    contents_offset = (names_offset + len(names.buffer) + 0x1F) & ~0x1F
    names.buffer = names.buffer.ljust(contents_offset - names_offset, b"\0")
    nodes = u8.pack_node(root, contents_offset, 0x0)
    head = b"U\xaa8-" + struct.pack(">III", 0x20, contents_offset - 0x20, contents_offset) + bytes(16)
    return head + nodes + names.buffer + contents.buffer


def add(directory: dict, name: str, content: bytes, where: str) -> None:
    if any(c["name"] == name for c in directory["children"]):
        raise SystemExit(f"{name} already in {where}")
    directory["children"].append({"is_dir": False, "name": name, "content": content})
    directory["children"].sort(key=lambda c: c["name"].lower())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("replaced_assets", type=Path, nargs="?", help="RR's overlay; left out for the disc's archives")
    ap.add_argument("output", type=Path)
    args = ap.parse_args(argv)

    root, compressed = load_szs(args.input)
    top = top_of(root)
    ctrl_dir, blyt_dir, timg_dir = (find_dir(top, [FOLDER, d]) for d in ("ctrl", "blyt", "timg"))
    if args.replaced_assets is not None:
        overlay = top_of(load_szs(args.replaced_assets)[0])
        shadowed = [c["name"] for d in overlay["children"] if d["is_dir"] and d["name"] in ("ctrl", "blyt")
                    for c in d["children"] if c["name"] in (SOURCE_CTRL, SOURCE_LAYOUT + ".brlyt")]
        if shadowed:
            raise SystemExit(f"{args.replaced_assets.name} shadows {', '.join(shadowed)}; derive from the overlay's instead")
    ctrl = next((c for c in ctrl_dir["children"] if c["name"] == SOURCE_CTRL), None)
    lyt = next((c for c in blyt_dir["children"] if c["name"] == SOURCE_LAYOUT + ".brlyt"), None)
    if ctrl is None or lyt is None:
        raise SystemExit(f"{SOURCE_CTRL} or {SOURCE_LAYOUT}.brlyt missing from {args.input}")

    textures = {}
    for name, (size, layers) in icons().items():
        pixels = render(size, layers)
        tpl = encode_tpl(size, pixels)
        if decode_tpl(tpl) != (size, pixels):
            raise SystemExit(f"{name}: the TPL does not decode to its pixels")
        textures[name] = (size, tpl)

    before = snapshot(root)
    add(ctrl_dir, CTRL, same_control(ctrl["content"]), str(args.input))
    add(blyt_dir, LAYOUT + ".brlyt", same_layout(lyt["content"], {n: s for n, (s, _) in textures.items()}), str(args.input))
    for name, (_, tpl) in textures.items():
        add(timg_dir, f"{name}.tpl", tpl, str(args.input))
    after = snapshot(root)
    changed = [path for path, content in before.items() if after.get(path) != content]
    if changed:
        raise SystemExit(f"existing members changed: {', '.join(changed)}")

    out = pack_szs(root)
    args.output.write_bytes(pack_yaz(out) if compressed else out)
    print(f"wrote {args.output}: {CTRL}, {LAYOUT}.brlyt, {', '.join(f'{n}.tpl' for n in textures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
