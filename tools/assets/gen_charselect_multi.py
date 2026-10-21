#!/usr/bin/env python3
"""Add CharacterSelect<N>_<col>_Multi controls to a MenuMulti.szs.

Same-character select (rr-pulsar PulsarEngine/UI/SameCharacterSelect.cpp, offline
only) loads every multiplayer driver button as a "CharacterSelect%d_%d_Multi" control.
Those controls don't exist in the game, so this script derives them from the
user's own archive: each _Multi control is the column's normal control with the
layout switched to common_w117_mii_suit (the Mii-button layout, which has one OK
marker per player). Button positions are kept from the original control.

Retro Rewind mounts Assets/ReplacedAssets.szs ahead of MenuMulti.szs (rr-pulsar
IO/Misc.cpp, LoadAssetsFile), and its ctrl/ copies of these controls win at runtime;
the MenuMulti.szs members they shadow are stale. So the sources are MenuMulti's
controls overlaid by ReplacedAssets'.

Each OK marker takes a share of the button by the player count (D80, D95): a button
loads "CharacterSelect%d_%d_Multi<N>", the _Multi control on a copy of its layout
named <layout>_ss8_<N>, whose markers sit in a grid of N cells filled row by row:
2 for 2 players (2 x 1), 4 for 3-4 (2 x 2), 6 for 5-6 (3 x 2), 8 for 7-8 (4 x 2).
SameCharacterSelect.cpp loads N = 2 or 4 on the game's page for 2-4 players, and the
8-player page (CharSelect.cpp) N = 6 or 8. P5-8's markers (ok_null_5p..8p) are
copies of 1p..4p with renamed panes and materials, so no animation binds them.
The sized members are added after the rest; the script checks that every member
it wrote before them is unchanged.

Nothing derived from game files is stored in this repository; run it against the
archives of the Retro Rewind copy you are installing into.

Without Retro Rewind there is no overlay, and the disc's MenuMulti.szs is the only source.

Usage:
  gen_charselect_multi.py <in MenuMulti.szs> [<ReplacedAssets.szs>] <out MenuMulti.szs>
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "external" / "wuj5"))

from brctr import pack_brctr, unpack_brctr  # noqa: E402
from brlyt import pack_brlyt, unpack_brlyt  # noqa: E402
from u8 import pack_u8, unpack_u8  # noqa: E402
from yaz import pack_yaz, unpack_yaz  # noqa: E402

# Each driver button size has a Mii twin with per-player OK markers.
MULTI_LAYOUTS = {
    "common_w006_chara": "common_w117_mii_suit",
    "common_w025_chara_middle": "common_w118_mii_suit_middle",
    "common_w026_chara_large": "common_w119_mii_suit_large",
}
CTRL_RE = re.compile(r"^CharacterSelect(\d+)_(\d+)(_Mii)?\.brctr$")
SS8_SUFFIX = "_ss8"
# The marker grids by the cells they hold, (columns, rows) (D95).
MARKER_GRIDS = {2: (2, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2)}


def find_dir(node: dict, path: list[str]) -> dict:
    for part in path:
        for child in node["children"]:
            if child["is_dir"] and child["name"] == part:
                node = child
                break
        else:
            raise SystemExit(f"directory {'/'.join(path)} not found in archive")
    return node


def to_multi(ctrl: dict) -> dict:
    layout = ctrl["main brlyt"]
    if layout in MULTI_LAYOUTS.values():
        return ctrl  # The Mii column already uses a multi layout.
    if layout not in MULTI_LAYOUTS:
        raise SystemExit(f"unexpected character button layout {layout!r}")
    multi = MULTI_LAYOUTS[layout]
    ctrl = dict(ctrl)
    ctrl["main brlyt"] = multi
    ctrl["animations"] = [
        {**anim, "brlan": anim["brlan"].replace(layout + "_", multi + "_", 1)}
        for anim in ctrl["animations"]
    ]
    return ctrl


def find_pane(pane: dict, name: str) -> dict | None:
    if pane.get("name") == name:
        return pane
    for child in pane.get("children", []):
        hit = find_pane(child, name)
        if hit is not None:
            return hit
    return None


def rename_pane(pane: dict, old: str, new: str, materials: list) -> None:
    """Renames a cloned subtree and gives it its own materials, so no animation of the original binds it."""
    pane["name"] = pane["name"].replace(old, new)
    if "material" in pane:
        material = copy.deepcopy(materials[pane["material"]])
        material["name"] = material["name"].replace(old, new)
        materials.append(material)
        pane["material"] = len(materials) - 1
    for child in pane.get("children", []):
        rename_pane(child, old, new, materials)


def sized_markers(content: bytes, name: str, cells: int) -> bytes:
    """The layout with its OK markers in a grid of `cells` over black_base, filled row by row (D95).

    The 8-cell grid is the one picked from real renders (D80): each marker at half the vanilla
    marker's width and 0.55 of its height. The other grids scale that evenly, as far as their
    cells allow, so every marker keeps its shape.
    """
    cols, rows = MARKER_GRIDS[cells]
    lyt = unpack_brlyt(content)
    materials = next(s["materials"] for s in lyt["sections"] if s["magic"] == "mat1")
    root = next(s for s in lyt["sections"] if s["magic"] == "pan1")
    panes = {n: find_pane(root, n) for n in ["black_base", "pause_w_null"] + [f"ok_null_{i}p" for i in range(1, 5)]}
    missing = [n for n, pane in panes.items() if pane is None]
    plate = find_pane(panes["ok_null_1p"], "ok_waku_1p") if panes["ok_null_1p"] else None
    if plate is None:
        missing.append("ok_waku_1p")
    if missing:
        raise SystemExit(f"{name}: panes {', '.join(missing)} missing")
    w, h = panes["black_base"]["size x"], panes["black_base"]["size y"]
    fit = min(4 / cols, (h / rows) / (plate["size y"] * panes["ok_null_1p"]["scale y"] * 0.55))
    markers = [panes[f"ok_null_{i}p"] for i in range(1, 5)]
    if cells > 4:
        for i in range(1, 5):
            clone = copy.deepcopy(markers[i - 1])
            rename_pane(clone, f"_{i}p", f"_{i + 4}p", materials)
            panes["pause_w_null"]["children"].append(clone)
            markers.append(clone)
    for k, pane in enumerate(markers[:cells]):
        col, row = k % cols, k // cols
        pane["translation x"] = w * ((col + 0.5) / cols - 0.5)
        pane["translation y"] = 0.0 if rows == 1 else (h * 0.23 if row == 0 else -h * 0.23)
        pane["scale x"] = pane["scale x"] * 0.5 * fit
        pane["scale y"] = pane["scale y"] * 0.55 * fit
    return pack_brlyt(lyt)


def snapshot(node: dict, prefix: str = "") -> dict[str, bytes]:
    out = {}
    for child in node["children"]:
        path = f"{prefix}/{child['name']}"
        if child["is_dir"]:
            out.update(snapshot(child, path))
        else:
            out[path] = child["content"]
    return out


def load_szs(path: Path) -> tuple[dict, dict, bool]:
    """Returns the archive root, its top directory (inside "." when present), and whether it was Yaz0."""
    data = path.read_bytes()
    compressed = data[:4] == b"Yaz0"
    root = unpack_u8(unpack_yaz(data) if compressed else data)
    top = root["children"][0] if root["children"] and root["children"][0]["name"] == "." else root
    return root, top, compressed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("replaced_assets", type=Path, nargs="?", help="RR's overlay; left out for the disc's own MenuMulti.szs")
    ap.add_argument("output", type=Path)
    args = ap.parse_args(argv)

    root, top, compressed = load_szs(args.input)
    ctrl_dir = find_dir(top, ["button", "ctrl"])
    blyt_dir = find_dir(top, ["button", "blyt"])
    layouts = {c["name"] for c in blyt_dir["children"]}
    for multi in MULTI_LAYOUTS.values():
        if multi + ".brlyt" not in layouts:
            raise SystemExit(f"{multi}.brlyt missing from {args.input}")

    overlay, overlay_layouts = {}, set()
    if args.replaced_assets is not None:
        _, overlay_top, _ = load_szs(args.replaced_assets)
        overlay = {c["name"]: c["content"] for c in find_dir(overlay_top, ["ctrl"])["children"]}
        overlay_layouts = {c["name"] for d in overlay_top["children"] if d["is_dir"] and d["name"] == "blyt"
                           for c in d["children"]}
    overridden = sorted(name for name in overlay if CTRL_RE.match(name))
    effective = {c["name"]: c["content"] for c in ctrl_dir["children"]}
    effective.update(overlay)

    existing = {c["name"] for c in ctrl_dir["children"]}
    sources: dict[tuple[str, str], str] = {}
    for name in effective:
        m = CTRL_RE.match(name)
        if m:
            key = (m.group(1), m.group(2))
            # Prefer _Mii: vanilla multiplayer loads it for the Mii column's Mii buttons, and only
            # that column has one. RR's ReplacedAssets also has plain controls for it.
            if key not in sources or m.group(3):
                sources[key] = name

    added = []
    multis = {}
    for (count, col), src in sorted(sources.items()):
        name = f"CharacterSelect{count}_{col}_Multi.brctr"
        multis[name] = to_multi(unpack_brctr(effective[src]))
        content = pack_brctr(multis[name])
        if name in existing:
            next(c for c in ctrl_dir["children"] if c["name"] == name)["content"] = content
        else:
            ctrl_dir["children"].append({"is_dir": False, "name": name, "content": content})
        added.append(name)

    # The 8-player members are added after a snapshot of everything 2-4 player games load.
    before = snapshot(root)
    sized_layouts = set()
    for name, ctrl in multis.items():
        for cells in MARKER_GRIDS:
            sized_layouts.add(ctrl["main brlyt"])
            sized = dict(ctrl, **{"main brlyt": f"{ctrl['main brlyt']}{SS8_SUFFIX}_{cells}"})
            sized_name = name.replace("_Multi.brctr", f"_Multi{cells}.brctr")
            if sized_name in existing:
                raise SystemExit(f"{sized_name} already in {args.input}")
            ctrl_dir["children"].append({"is_dir": False, "name": sized_name, "content": pack_brctr(sized)})
            added.append(sized_name)
    # The sized copies start from MenuMulti's layouts; one the overlay shadows would be stale (PD2's trap).
    shadowed = sorted(f"{layout}.brlyt" for layout in sized_layouts if f"{layout}.brlyt" in overlay_layouts)
    if shadowed:
        raise SystemExit(f"{args.replaced_assets.name} shadows {', '.join(shadowed)}; copy the overlay's layout instead")
    for layout in sorted(sized_layouts):
        src = next(c for c in blyt_dir["children"] if c["name"] == layout + ".brlyt")
        for cells in MARKER_GRIDS:
            name = f"{layout}{SS8_SUFFIX}_{cells}.brlyt"
            if name in layouts:
                raise SystemExit(f"{name} already in {args.input}")
            blyt_dir["children"].append({"is_dir": False, "name": name, "content": sized_markers(src["content"], name, cells)})
    after = snapshot(root)
    changed = [path for path, content in before.items() if after.get(path) != content]
    if changed:
        raise SystemExit(f"members 2-4 player games load changed: {', '.join(changed)}")
    ctrl_dir["children"].sort(key=lambda c: c["name"].lower())

    out = pack_u8(root)
    args.output.write_bytes(pack_yaz(out) if compressed else out)
    print(f"wrote {args.output} with {len(added)} controls: {', '.join(added)}")
    print(f"added sized-marker layouts for {', '.join(sorted(sized_layouts))}: {', '.join(f'{SS8_SUFFIX}_{n}' for n in MARKER_GRIDS)}")
    if args.replaced_assets is not None:
        print(f"{len(overridden)} sources taken from {args.replaced_assets.name}: {', '.join(overridden)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
