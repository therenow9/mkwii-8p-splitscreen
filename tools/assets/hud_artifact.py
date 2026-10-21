#!/usr/bin/env python3
"""Print the geometry of the 4P race HUD, for M3 phase B's layout decision page.

Reads the user's own Scene/UI/Race.szs (controls) and Race_<lang>.szs (the localized game_image_*
layouts) in memory and writes JSON: for each HUD control, its 4P variants' 16:9 and 4:3 translation
and scale, and the rectangle its visible panes cover at scale 1 in layout units (y up, centred).

The rectangle is an estimate: it unions picture and text panes through their parents' translation
and scale and their base position, and ignores rotation and animation. Panes named *null* are
frames, used only when nothing else is visible (the map draws inside one); a pane stored above 4x
is an animation's start and is skipped. Text boxes count whole, so a short text over-reports.
Nothing read here is stored in the repository; write the output outside it (D41).

Usage:
  hud_artifact.py <files/Scene/UI> [--lang E] [--out geometry.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "external" / "wuj5"))

from brctr import unpack_brctr  # noqa: E402
from brlyt import unpack_brlyt  # noqa: E402
from u8 import unpack_u8  # noqa: E402
from yaz import unpack_yaz  # noqa: E402

# The VS race HUD controls of a 2-4P race, by BRCTR; balloons follow their kart at run time.
CONTROLS = {
    "position_multi": "rank",
    "lap_number": "lap",
    "item_window_new": "item window",
    "count": "countdown",
    "map": "map",
    "balloon": "name tag",
}


MAX_REST_SCALE = 4.0

# wuj5's names for a pane's base position, in the file's order (0-8: column + 3 * row, row 0 the top).
BASE_POSITIONS = ["top left", "top center", "top right", "center left", "center", "center right",
                  "bottom left", "bottom center", "bottom right"]


def archive_files(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    if data[:4] == b"Yaz0":
        data = unpack_yaz(data)
    files: dict[str, bytes] = {}

    def walk(node: dict) -> None:
        for child in node.get("children", []):
            if child["is_dir"]:
                walk(child)
            else:
                files[child["name"]] = child["content"]

    walk(unpack_u8(data))
    return files


def pane_rect(pane: dict, ox: float, oy: float, sx: float, sy: float, out: list[float], frames: bool) -> None:
    """Union the visible picture and text panes under pane into out = [x0, y0, x1, y1]; with frames,
    the *null* panes instead (a map draws inside its frame pane at run time)."""
    if not pane.get("flags", {}).get("visible", True):
        return
    x = ox + pane.get("translation x", 0.0) * sx
    y = oy + pane.get("translation y", 0.0) * sy
    psx = sx * pane.get("scale x", 1.0)
    psy = sy * pane.get("scale y", 1.0)
    # A pane stored far above 1x is an animation's start (the countdown digits zoom in from 11.8x).
    if max(abs(psx), abs(psy)) > MAX_REST_SCALE:
        return
    is_frame = "null" in pane.get("name", "").lower()
    if pane["magic"] in ("pan1", "pic1", "txt1") and is_frame == frames and (frames or pane["magic"] != "pan1"):
        w, h = pane.get("size x", 0.0) * psx, pane.get("size y", 0.0) * psy
        base = BASE_POSITIONS.index(pane.get("base position", "center"))
        col, row = base % 3, base // 3
        x0 = x - w * (0.0, 0.5, 1.0)[col]
        y1 = y + h * (0.0, 0.5, 1.0)[row]
        out[0], out[1] = min(out[0], x0), min(out[1], y1 - h)
        out[2], out[3] = max(out[2], x0 + w), max(out[3], y1)
    for child in pane.get("children", []):
        pane_rect(child, x, y, psx, psy, out, frames)


def layout_rect(brlyt: bytes) -> tuple[list[float] | None, str]:
    """The control's rectangle and what it is: its visible panes, else its frame panes."""
    layout = unpack_brlyt(brlyt)
    for frames, source in ((False, "panes"), (True, "frame")):
        out = [1e9, 1e9, -1e9, -1e9]
        for section in layout["sections"]:
            if section["magic"] == "pan1":
                for child in section.get("children", []):
                    pane_rect(child, 0.0, 0.0, 1.0, 1.0, out, frames)
        if out[0] < out[2]:
            return [round(v, 1) for v in out], source
    return None, "none"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ui_dir", type=Path, help="the game's files/Scene/UI folder")
    ap.add_argument("--lang", default="E", help="Race_<lang>.szs for the localized layouts (default E)")
    ap.add_argument("--out", type=Path, help="write here instead of stdout")
    args = ap.parse_args()

    files = archive_files(args.ui_dir / "Race.szs")
    files.update(archive_files(args.ui_dir / f"Race_{args.lang}.szs"))
    result = {}
    for ctrl, role in CONTROLS.items():
        brctr = unpack_brctr(files[f"{ctrl}.brctr"])
        lyt = files.get(f"{brctr['main brlyt']}.brlyt")
        variants = {}
        for v in brctr["variants"]:
            if "_4" not in v["name"]:
                continue
            variants[v["name"]] = {
                "16:9": [v["translation x 16:9"], v["translation y 16:9"], v["scale x 16:9"], v["scale y 16:9"]],
                "4:3": [v["translation x 4:3"], v["translation y 4:3"], v["scale x 4:3"], v["scale y 4:3"]],
            }
        rect, source = layout_rect(lyt) if lyt else (None, "none")
        result[ctrl] = {"role": role, "layout": brctr["main brlyt"], "rect": rect, "rect_from": source,
                        "variants": variants}
    text = json.dumps(result, indent=1)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
