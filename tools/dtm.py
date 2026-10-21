#!/usr/bin/env python3
"""Play a 1-4P menu script on the plain disc, for 1-4P parity with the vanilla build.

The plain disc runs none of our code, so no menu script reads there: the script becomes a Dolphin input movie
(.dtm, Dolphin's Source/Core/Core/Movie.h), which Dolphin plays from boot (-m) on GC ports 1-4.

Usage:
  dtm.py make SCRIPT OUT.dtm [--k K] [--offset O] [--tail F]
  dtm.py play SCRIPT [--k K] [--offset O] [--shot S ...] [--total S] [--fast] [--slot N] [--keep-save]
  dtm.py parity SCRIPT [--total S] [--slot N]

make: a script frame f (channel 0's pad reads since boot, as MenuScript.cpp counts them) is pad poll O + round(K * f);
each poll holds one 8-byte ControllerState per port, ports 1-4 always connected. A press holds its buttons from its
frame for its hold, and unplug clears the port's connected bit. Only GC ports, presses, waits, mash and unplug.

play: on install_8p.py's --vanilla root, the plain disc (no Riivolution pack, dolphin --plain) with the script's
movie. The pack's save goes into the NAND for the run, since the plain disc reads its own; Dolphin copies a movie's
saves back into the NAND and backs up what it replaces into Dolphin/Backup, so Dolphin/Wii and Dolphin/Backup are put
back byte for byte afterwards. A shot at each --shot S seconds after launch goes to <root>/shots/dtm/<time>.

parity: the build's lane at real speed and the plain disc's movie, a shot two seconds after each press up to the
first mash, once the page has settled, side by side in <root>/parity/<script>-<time>/index.html.
"""
from __future__ import annotations

import argparse
import configparser
import html
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(ROOT / "tools"))
import install_8p  # noqa: E402
import lane  # noqa: E402

HEADER_SIZE = 256
# ControllerState's bit, by PAD::PAD_BUTTON_* bit: byte 0 Start A B X Y Z Up Down, byte 1 Left Right L R disc reset
# is_connected get_origin (bool bitfields, least significant first).
STATE_BITS = {0x1000: 0, 0x100: 1, 0x200: 2, 0x400: 3, 0x800: 4, 0x10: 5, 0x8: 6, 0x4: 7, 0x1: 8, 0x2: 9, 0x40: 10, 0x20: 11}
CONNECTED = 1 << 14


def sysconf(path: Path) -> dict[str, bytes]:
    """The Wii's SYSCONF items by name: "SCv0", a u16 count, u16 offsets; an item is a type (top 3 bits) and name
    length - 1 byte, the name, then a big array (u16 length - 1), small array (u8 length - 1) or 1, 2, 4, 8, 1 bytes."""
    b = path.read_bytes()
    items = {}
    for n in range(struct.unpack_from(">H", b, 4)[0]):
        at = struct.unpack_from(">H", b, 6 + 2 * n)[0]
        kind, name_len = b[at] >> 5, (b[at] & 0x1f) + 1
        name, at = b[at + 1:at + 1 + name_len].decode(), at + 1 + name_len
        size = {1: lambda: struct.unpack_from(">H", b, at)[0] + 1, 2: lambda: b[at] + 1}.get(kind, lambda: {3: 1, 4: 2, 5: 4, 6: 8, 7: 1}[kind])()
        at += {1: 2, 2: 1}.get(kind, 0)
        items[name] = b[at:at + size]
    return items


def header(layout, polls: int, frames: int) -> bytes:
    """A movie that carries the root's own settings: without bSaveConfig, Dolphin decides at load time, before the game's
    title is known, that the movie starts from a blank save, and the license page would take the script's presses."""
    core = configparser.ConfigParser()
    core.read(layout.dolphin / "Config" / "Dolphin.ini", encoding="utf-8")
    gfx = configparser.ConfigParser()
    gfx.read(layout.dolphin / "Config" / "GFX.ini", encoding="utf-8")
    sys_ = sysconf(layout.dolphin / "Wii" / "shared2" / "sys" / "SYSCONF")

    def on(cfg, section, key, default):
        return cfg.getboolean(section, key, fallback=default)

    h = bytearray(HEADER_SIZE)
    h[0x00:0x04] = b"DTM\x1a"
    h[0x04:0x0a] = b"RMCP01"
    h[0x0a] = 1  # bWii
    h[0x0b] = 0x0f  # GC ports 1-4, no Wii Remotes
    struct.pack_into("<QQ", h, 0x0d, frames, polls * 4)  # frameCount (shown only), inputCount
    h[0x31:0x31 + 6] = b"dtm.py"  # author
    h[0x51:0x51 + 16] = core.get("Core", "GFXBackend", fallback="Vulkan").encode()[:15].ljust(16, b"\0")
    struct.pack_into("<Q", h, 0x81, int(time.time()))  # recordingStartTime: the RTC
    h[0x89] = 1  # bSaveConfig; bClearSave (0x98) stays 0, so the NAND's save is copied in
    h[0x8b] = on(core, "Core", "CPUThread", True)
    h[0x8c] = sys_["IPL.PGS"][0]
    h[0x8d] = on(core, "Core", "DSPHLE", True)
    h[0x8e] = on(core, "Core", "FastDiscSpeed", False)
    h[0x8f] = core.getint("Core", "CPUCore", fallback=1)
    h[0x90] = on(gfx, "Hacks", "EFBAccessEnable", True)
    h[0x92] = on(gfx, "Hacks", "EFBToTextureEnable", True)
    h[0x94] = on(gfx, "Hacks", "EFBEmulateFormatChanges", False)
    h[0x95] = on(gfx, "Hacks", "ImmediateXFBEnable", False)
    h[0x96] = on(gfx, "Hacks", "XFBToTextureEnable", True)
    h[0x9a] = on(core, "Core", "SyncGPU", False)
    h[0x9c] = sys_["IPL.E60"][0]
    h[0x9d] = sys_["IPL.LNG"][0]
    h[0x9f] = on(core, "Core", "JITFollowBranch", True)
    h[0xa0] = on(core, "Core", "FMA", True)
    h[0xa2] = sys_["IPL.AR"][0]
    h[0xa3] = sys_["IPL.SADR"][0]
    struct.pack_into("<Q", h, 0xed, 2**64 - 1)  # tickCount: playback ends past it, so never
    return bytes(h)


def make(layout, script: Path, k: float, offset: int, tail: int) -> bytes:
    parsed = install_8p.menu_script(script)
    if (parsed["picks"] or parsed["settings"] or parsed["flags"] & ~install_8p.SCRIPT_AUTODRIVE
            or any(port > 3 for _, port, _, _ in parsed["steps"])):
        sys.exit(f"[dtm] {script}: only GC ports 1-4 and presses play on the plain disc")
    polls = offset + round(k * (parsed["end"] + tail))
    state = [[CONNECTED] * 4 for _ in range(polls)]
    for frame, port, buttons, hold in parsed["steps"]:
        for poll in range(offset + round(k * frame), min(polls, offset + round(k * (frame + hold)))):
            if buttons == 0:
                state[poll][port] &= ~CONNECTED
            for bit, at in STATE_BITS.items():
                if buttons & bit:
                    state[poll][port] |= 1 << at
    body = b"".join(struct.pack("<HBBBBBB", s, 0, 0, 128, 128, 128, 128) for poll in state for s in poll)
    return header(layout, polls, parsed["end"] + tail) + body


# The PAD library's boot line: the movie's polls begin about here, so step shots count from it.
PAD_INIT = re.compile(r"RVL_SDK - PAD")


def remove(layout, path: Path) -> None:
    # Windows can hold an ended Dolphin's files a moment longer.
    for _ in range(40):
        shutil.rmtree(install_8p.guard(layout, path), ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.25)


def play(args, from_pad_init: bool = False) -> tuple[str, Path]:
    """The plain disc with the script's movie; returns why it stopped early ("" if it did not) and its shots."""
    layout = install_8p.Layout(install_8p.slot_root(args.slot, True), True)
    on = ["tools/install_8p.py", "--vanilla", "--slot", str(args.slot)]
    if why := install_8p.foreign_dolphin():
        sys.exit(f"[dtm] {why}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = install_8p.guard(layout, layout.root / "dtm" / stamp)
    work.mkdir(parents=True)
    movie = work / (Path(args.script).stem + ".dtm")
    movie.write_bytes(make(layout, Path(args.script), args.k, args.offset, args.tail))
    kept = {name: work / "before" / name for name in ("Wii", "Backup")}
    for name, copy in kept.items():
        if (layout.dolphin / name).exists():
            shutil.copytree(layout.dolphin / name, copy)
    title_data = Path("title") / "00010004" / "524d4350" / "data"
    plain_save = install_8p.slot_root(0, True) / "dtm" / "plain-save"
    shots = layout.root / "shots" / "dtm" / stamp
    stopped = ""
    try:
        for f in (plain_save if plain_save.exists() else layout.save.parent).iterdir():
            shutil.copy2(f, install_8p.guard(layout, layout.dolphin / "Wii" / title_data / f.name))
        launch = [*on, "dolphin", "--unattended", "--plain", "--movie", str(movie)] + (["--fast"] if args.fast else []) + (
            ["--osd"] if args.osd else [])
        out = subprocess.run([PY, *launch], cwd=ROOT, capture_output=True, text=True)
        print(out.stdout.strip()[-300:], out.stderr.strip()[-300:], flush=True)
        rec = install_8p.recorded_pid(layout)
        launched = time.monotonic()
        base = None if from_pad_init else launched
        log = install_8p.LogTail(layout.dolphin_log)
        pending = sorted(args.shot)
        while rec and install_8p.dolphin_alive(*rec) and time.monotonic() - launched < args.total:
            text = log.read()
            # A crash loop writes gigabytes of log a minute.
            if m := install_8p.INVALID.search(text):
                stopped = f"stopped at the first invalid access, {time.monotonic() - launched:.0f} s: PC {m.group(1)}"
                install_8p.save_shots(layout, rec[0], shots, "invalid")
                break
            if base is None and PAD_INIT.search(text):
                base = time.monotonic()
            if base is not None and pending and time.monotonic() - base >= pending[0]:
                at = pending.pop(0)
                install_8p.save_shots(layout, rec[0], shots, f"{len(args.shot) - len(pending):02d}-{at:g}s")
            time.sleep(0.1)
    finally:
        subprocess.run([PY, *on, "stop"], cwd=ROOT, capture_output=True)
        # The movie's NAND, which a forced stop leaves behind: the plain disc's own save, after its unlock pages.
        session = layout.dolphin / "WiiSession"
        if args.keep_save and (session / title_data).exists():
            remove(layout, plain_save)
            shutil.copytree(session / title_data, plain_save)
            print(f"[dtm] kept the plain disc's save in {plain_save}")
        for name in ("WiiSession", "RedirectSession", "WiiSession.backup", "RedirectSession.backup"):
            remove(layout, layout.dolphin / name)
        for name, copy in kept.items():
            remove(layout, layout.dolphin / name)
            if copy.exists():
                shutil.copytree(copy, layout.dolphin / name)
        shutil.copy2(layout.dolphin_log, work / "dolphin.log")
    print(f"[dtm] {stopped or 'ran to its end'}; shots {shots}; log {work / 'dolphin.log'}")
    return stopped, shots


def presses(script: Path) -> list[tuple[int, int, str]]:
    """(MenuScript step, frame, comment) of each press line before the script's first mash: the pages a parity run
    compares. A press or unplug line is one step."""
    steps, out, step = install_8p.menu_script(script)["steps"], [], 0
    for line in script.read_text(encoding="utf-8").splitlines():
        words, _, comment = line.partition("#")
        words = words.split()
        if words[:1] == ["mash"]:
            break
        if words[:1] == ["press"]:
            out.append((step, steps[step][0], f"{' '.join(words[1:3])}  {comment.strip()}".strip()))
        if words[:1] in (["press"], ["unplug"]):
            step += 1
    return out


def parity(args) -> int:
    """The build lane and the plain disc's movie of one script, each shot about 2 s after every press up to the first mash,
    side by side in <root>/parity/<script>-<time>/index.html."""
    script = Path(args.script)
    steps = presses(script)
    if not steps:
        sys.exit(f"[dtm] {script}: no press before its first mash, so nothing to compare")
    layout = install_8p.Layout(install_8p.slot_root(args.slot, True), True)
    out = install_8p.guard(layout, layout.root / "parity" / f"{script.stem}-{time.strftime('%Y%m%d-%H%M%S')}")
    out.mkdir(parents=True)
    if subprocess.run([PY, "tools/build_code.py", "--vanilla", "--menu-script"], cwd=ROOT, capture_output=True).returncode:
        sys.exit("[dtm] the vanilla menu build failed: tools/build_code.py --vanilla --menu-script")
    # The build: a lane at real speed, a shot 2 s after each step's log line.
    lane_args = lane.lane_parser().parse_args(["vanilla-menu", str(script), *(f"ss8 menu: step {step} frame@2" for step, _, _ in steps),
                                               "--no-build", "--total", str(args.total)])
    build_shots = layout.root / "shots" / "parity" / out.name
    built = lane.lane(args.slot, False, lane_args, out / "build", build_shots)
    # The plain disc: the same moments on the movie's clock, where a press lands on time; a log-triggered shot lands
    # about half a second after its line.
    args.shot = [frame / 60 + 2.5 for _, frame, _ in steps]
    args.total = min(args.total, args.shot[-1] + 30)
    stopped, plain = play(args, from_pad_init=True)
    rows = []
    for i, (step, frame, comment) in enumerate(steps):
        cells = []
        for kind, folder, pattern in (("build", build_shots, f"{i + 1:02d}-1.png"), ("plain", plain, f"{i + 1:02d}-*-1.png")):
            found = sorted(folder.glob(pattern))[:1]
            for f in found:
                shutil.copy2(f, out / f"{kind}-{i + 1:02d}.png")
            cells.append(f'<td><img src="{kind}-{i + 1:02d}.png"></td>' if found else "<td>no shot</td>")
        rows.append(f"<tr><td>{step}<br>frame {frame}<br>{html.escape(comment)}</td>{''.join(cells)}</tr>")
    (out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>Parity {html.escape(script.stem)}</title>
<style>body{{font:14px system-ui;background:#fafafa;color:#1b1b1b;margin:16px}} img{{width:400px}} td{{vertical-align:top;padding:4px}}
@media (prefers-color-scheme: dark){{body{{background:#161616;color:#e8e8e8}}}}</style>
<h1>{html.escape(script.stem)}: the vanilla build and the plain disc</h1>
<p>Build lane: {html.escape(built['result'])}, {built['invalid']} invalid. Plain disc: {html.escape(stopped or 'ran to its end')}.
Each shot is about 2 s after the step's press; the plain disc's on the movie's clock, from the PAD library's boot line.</p>
<table><tr><th>step</th><th>vanilla build</th><th>plain disc</th></tr>{''.join(rows)}</table>
""", encoding="utf-8", newline="\n")
    print(f"[dtm] parity: build {built['result']}, plain {stopped or 'ran to its end'}: {out / 'index.html'}")
    return 0 if built["result"] == "ok" and not stopped else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("make", "play", "parity"):
        p = sub.add_parser(name)
        p.add_argument("script", help="a tools/menu_scripts name or a path")
        p.add_argument("--k", type=float, default=2.0, help="pad polls per script frame (default 2: SI polls at 120 Hz)")
        p.add_argument("--offset", type=int, default=0, help="polls before script frame 0 (default 0)")
        p.add_argument("--tail", type=int, default=600, help="frames of idle input after the script's end (default 600)")
        if name == "make":
            p.add_argument("out", type=Path)
        else:
            if name == "play":
                p.add_argument("--shot", type=float, action="append", default=[], help="a shot this many s after launch")
                p.add_argument("--fast", action="store_true", help="unlimited emulation speed")
                p.add_argument("--keep-save", action="store_true",
                               help="keep the save the plain disc wrote as slot 0's <root>/dtm/plain-save, which later plays start from")
            p.add_argument("--total", type=float, default=300, help="stop this many s after launch (default 300)")
            p.add_argument("--slot", type=int, default=0, help="the vanilla slot (default 0)")
            p.add_argument("--osd", action="store_true", help="Dolphin's frame and input counts and the movie's pads on screen")
    args = ap.parse_args()
    if getattr(args, "keep_save", False) and args.slot:
        ap.error("--keep-save writes slot 0's plain save, so it plays on slot 0")
    args.script = str(install_8p.ROOT / "tools" / "menu_scripts" / f"{args.script}.txt") if not Path(args.script).suffix else args.script
    if args.cmd == "make":
        layout = install_8p.Layout(install_8p.slot_root(0, True), True)
        args.out.write_bytes(make(layout, Path(args.script), args.k, args.offset, args.tail))
        return 0
    if args.cmd == "parity":
        args.keep_save = args.fast = False
        return parity(args)
    return 1 if play(args)[0] else 0


if __name__ == "__main__":
    sys.exit(main())
