#!/usr/bin/env python3
"""MKWii 8P Splitscreen setup: 5-8 player splitscreen for Mario Kart Wii, on Retro Rewind and on the game alone.

Each build installs as its own copy under one folder (default Documents/MKWii 8 Player), made from this PC's own files,
which are only read: the disc, Retro Rewind and Dolphin's pad settings, from Wheel Wizard or picked by hand.
  Retro Rewind/     a copy of Retro Rewind with SplitScreen8, offline only; your own Retro Rewind is left alone
  Mario Kart Wii/   Mario Kart Wii with SplitScreen8 and nothing else
Each copy gets a "Play (Dolphin).cmd", a "Play (WiiCompiled).cmd" with its own WiiCompiled (about 5 GB), or both;
with wii, also a Wii image for USB Loader GX in its Wii/wbfs/, to copy into the drive's wbfs folder.

With none of --builds, --targets, --root and --yes the setup opens its window; with any of them it installs in the
console, a flag left out taking its default.

Usage:
  "MKWii 8P Splitscreen Setup.exe" [--builds rr,mkw] [--targets dolphin,wiicompiled,wii] [--root DIR] [--rr DIR] [--fetch-rr] [--disc FILE] [--dolphin EXE] [--yes]
  ss8_setup.py --payload DIR ...        from the source tree, with a release's payload/ (tools/release.py)
"""
from __future__ import annotations

import argparse
import ctypes
import functools
import http.client
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# name, folder, vanilla, install_8p.py variant (which picks the menu archive's generator)
BUILDS = {"rr": ("Retro Rewind", False, "ss8"), "mkw": ("Mario Kart Wii", True, "vanilla")}
TARGETS = ("dolphin", "wiicompiled", "wii")
WC_COPY = 5 << 30  # a WiiCompiled copy kept: the RR one measured 4.98 GB, Mario Kart Wii's 4.85 GB
WC_WORK = 23 << 30  # while one builds: its staged payload (2 GiB), then its own check for 5 + 14 + 2 GiB more
WC_REPAIR = 16 << 30  # while one is repaired: its own check for 14 + 2 GiB
WII_IMAGE = {"rr": 5 << 30, "mkw": 3 << 30}  # a Wii image kept: the RR one measured 5.1 GB, Mario Kart Wii's 2.6 GB
WII_WORK = 10 << 30  # one image's work at a time: the disc's files (2.6 GB) beside its ISO (4.7 GB) or the image
RR_COPY = 5 << 29  # Retro Rewind kept, as downloaded or as copied into the build: 2.2 GB
RR_ZIP = 2 << 30  # Retro Rewind's full zip (1.9 GB) beside what it unpacks, until version.txt is written
RR_SERVER = "https://update.rwfc.net/RetroRewind/"  # Wheel Wizard's; the version and delete lists live here
RR_ZIPS = "https://cdn.update.rwfc.net/RetroRewind/zip/"
TITLE = "MKWii 8P Splitscreen Setup"
# WiiCompiled's own build files sit 200 characters below the folder, near Windows' 260, so the name stays short.
DEFAULT_ROOT = Path.home() / "Documents" / "MKWii 8 Player"
WC_MARK = "ss8-installer.sha256"  # in a copy's WiiCompiled Install: the installer it came from, which WiiCompiled does not record
MODS_NOTE = ("Custom characters or other mods were found in this Retro Rewind ({}). 5-8 players don't have the memory "
             "for them, so they won't be copied to the 8-player copy. Your own Retro Rewind keeps them.")


def picks(picked: str, allowed, flag: str) -> list[str]:
    chosen = [p.strip() for p in picked.split(",") if p.strip()]
    if not chosen or any(p not in allowed for p in chosen):
        sys.exit(f"{flag} takes {', '.join(allowed)}, comma-separated, not {picked!r}")
    return chosen


def load(args: argparse.Namespace):
    payload = args.payload or (Path(sys.executable).resolve().parent.parent / "payload" if getattr(sys, "frozen", False) else None)
    if payload is None or not (payload / "release.json").exists():
        sys.exit(f"no release payload at {payload}; from the source tree pass --payload")
    release = json.loads((payload / "release.json").read_text(encoding="utf-8"))
    try:
        import install_8p as i8  # it reads Wheel Wizard's settings, where there are any, as it loads
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read Wheel Wizard's settings ({e!r}): repair Wheel Wizard, or remove its config.json")
    i8.WIT = payload / "wit" / "wit.exe"
    if args.disc:
        i8.DISC = args.disc.resolve()
    if args.rr:
        use_rr(i8, args.rr)
    if args.dolphin:
        use_dolphin(i8, args.dolphin)
    return payload, release, i8


def use_rr(i8, folder: Path) -> None:
    i8.RR_SRC = folder.resolve()
    i8.RR_XML_SRC = i8.RR_SRC.parent / "riivolution" / "RetroRewind6.xml"


def use_dolphin(i8, exe: Path) -> None:
    i8.DOLPHIN = exe.resolve()
    if (i8.DOLPHIN.parent / "portable.txt").exists():  # a portable Dolphin keeps its settings in User beside it
        i8.DOLPHIN_USER_SRC = i8.DOLPHIN.parent / "User"


def dolphin_problem(dolphin: Path | None) -> str | None:
    if dolphin is None or not dolphin.is_file():
        return f"no Dolphin.exe{f' at {dolphin}' if dolphin else ''}: pick yours, or set Dolphin's location in Wheel Wizard"
    return None


def rr_problem(i8, release: dict) -> str | None:
    if not (i8.RR_SRC / "version.txt").exists() or not i8.RR_XML_SRC.exists():
        return (f"no Retro Rewind at {i8.RR_SRC} (and {i8.RR_XML_SRC}): pick a Retro Rewind "
                f"{release['rr_version']} RetroRewind6 folder, or install it in Wheel Wizard")
    version = (i8.RR_SRC / "version.txt").read_text(encoding="utf-8").strip()
    commit = i8.code_pul_commit(i8.RR_SRC / "Binaries" / "Code.pul")
    if (version, commit) != (release["rr_version"], release["rr_commit"]):
        return (f"this SplitScreen8 is built for Retro Rewind {release['rr_version']} ({release['rr_commit']}); "
                f"that folder has {version} ({commit})")
    return None


def rr_mods(i8) -> list[str]:
    return [name for name in i8.RR_MODS if any(f.is_file() for f in (i8.RR_SRC / name).rglob("*"))]


def download(url: str, dest: Path | None = None) -> bytes | None:
    # The server refuses Python's own user agent, and a 2 GB download can drop part-way, so it resumes.
    if dest and dest.exists():
        return None
    part = dest and dest.with_name(dest.name + ".part")
    for _ in range(5):
        have = part.stat().st_size if part and part.exists() else 0
        headers = {"User-Agent": TITLE.replace(" ", "")} | ({"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                if dest is None:
                    return r.read()
                total = r.headers.get("Content-Range", "").rpartition("/")[2] or r.headers.get("Content-Length")
                with part.open("ab" if r.status == 206 else "wb") as f:
                    while block := r.read(1 << 20):
                        if (f.tell() + len(block)) >> 28 != f.tell() >> 28:
                            print(f"[setup]   {(f.tell() + len(block)) >> 20} MB")
                        f.write(block)
            # A connection the server closes early ends the read without an error.
            if total and total.isdigit() and part.stat().st_size != int(total):
                print(f"[setup] {url}: {part.stat().st_size} of {total} bytes, trying again")
                continue
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code not in (408, 416, 429):  # 416: the part is already whole
                sys.exit(f"could not download {url}: the server answered {e.code} {e.reason}")
            if e.code != 416:
                print(f"[setup] {url}: {e}, trying again")
                continue
        except (OSError, http.client.HTTPException) as e:
            print(f"[setup] {url}: {e!r}, trying again")
            continue
        part.replace(dest)
        return None
    sys.exit(f"could not download {url}: check the internet connection, then press Install again")


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in v.split("."))


def fetch_rr(folder: Path, version: str) -> None:
    # As Wheel Wizard installs it: a full zip, then each update up to `version`, deleting the files those updates drop.
    # The zips stay until version.txt is written, so a stopped run carries on without downloading them again.
    def unpack(url: str) -> None:
        zips.append(folder / url.rpartition("/")[2])
        download(url, zips[-1])
        try:
            with zipfile.ZipFile(zips[-1]) as z:
                z.extractall(folder, [n for n in z.namelist() if n.startswith(("RetroRewind6/", "riivolution/"))])
        except zipfile.BadZipFile:
            zips[-1].unlink()
            sys.exit(f"{url} arrived damaged and was deleted: press Install again to download it again")

    zips: list[Path] = []
    folder.mkdir(parents=True, exist_ok=True)
    print(f"[setup] Retro Rewind {version} from {RR_ZIPS} into {folder}")
    unpack(f"{RR_ZIPS}{version}-full.zip")
    base = version_key((folder / "RetroRewind6" / "version.txt").read_text(encoding="utf-8").strip())
    wanted = lambda v: base < version_key(v) <= version_key(version)
    for line in download(RR_SERVER + "RetroRewindDelete.txt").decode().splitlines():
        v, _, path = line.strip().partition(" ")
        target = (folder / path.lstrip("/")).resolve()
        if path and wanted(v) and target.is_relative_to(folder.resolve()) and path.startswith(("/RetroRewind6/", "/riivolution/")):
            target.unlink(missing_ok=True)
    for line in download(RR_SERVER + "RetroRewindVersion.txt").decode().splitlines():
        v, url, *_ = line.split() + ["", ""]
        if url and wanted(v):
            print(f"[setup] Retro Rewind update {v}")
            unpack(url)
    (folder / "RetroRewind6" / "version.txt").write_text(version, encoding="utf-8")
    for z in zips:
        z.unlink()


def wc_current(layout, release: dict) -> bool:
    mark = layout.wc_install / WC_MARK
    return mark.exists() and mark.read_text(encoding="utf-8").strip() == release["files"]["WiiCompiled-Setup.exe"]


def drop_old_installs(i8, layout) -> None:
    for old in layout.wc_install.parent.glob("Install-v*"):
        if old.name.removeprefix("Install-v").replace(".", "").isdigit():  # upgrade_wiicompiled's, not a renamed one
            try:  # the old one sits 7 characters deeper than WiiCompiled wrote it, past 260 on a long root
                shutil.rmtree("\\\\?\\" + str(i8.guard(layout, old)))
            except OSError as e:
                print(f"[setup] {old} was not deleted ({e}); delete it to free about 5 GB")


@functools.cache
def rr_size(src: Path, mods: tuple) -> int:
    return sum(f.stat().st_size for f in src.rglob("*")
               if f.is_file() and f.relative_to(src).parts[0].casefold() not in map(str.casefold, mods))


def space(i8, release: dict, layouts: dict, targets, fetch: bool) -> tuple[int, int]:
    """The free space an install needs at its fullest moment, in install()'s order, and what its copies take when done."""
    held = peak = done = 0  # what this install has added so far, its most during a step, the copies' size

    def step(work: int, kept: int, size: int) -> None:  # work: a step's most at once, what it keeps included
        nonlocal held, peak, done
        peak = max(peak, held + work)
        held += kept
        done += size

    if fetch:
        step(RR_ZIP + RR_COPY, RR_COPY, RR_COPY)
    for b, layout in layouts.items():
        if b == "rr":  # the Mario Kart Wii copy outside WiiCompiled is a few MB
            copy = RR_COPY if fetch else rr_size(i8.RR_SRC, i8.RR_MODS)
            new = 0 if layout.dolphin.exists() else copy
            step(new, new, copy)
        if "wiicompiled" in targets:
            fresh = not (layout.wc_install / "install-state.json").exists()
            upgrade = not fresh and not wc_current(layout, release)
            step(WC_WORK if fresh or upgrade else WC_REPAIR, WC_COPY * (fresh or upgrade), WC_COPY)
            held -= WC_COPY * upgrade  # the old install, dropped once the new one runs
        if "wii" in targets:
            step(WII_WORK, WII_IMAGE[b], WII_IMAGE[b])
    return (300 << 20) + peak, done


def install(args: argparse.Namespace) -> int:
    payload, release, i8 = load(args)
    dolphin = i8.dolphin_exe()
    print(f"SplitScreen8 {release['version']}")
    builds = picks(args.builds or "rr,mkw", BUILDS, "--builds")
    has_dolphin = dolphin is not None and dolphin.is_file()
    has_dolphin_tool = has_dolphin and dolphin.with_name("DolphinTool.exe").exists()
    targets = picks(args.targets or ("dolphin,wiicompiled" if has_dolphin else "wiicompiled"), TARGETS, "--targets")
    root = (args.root or DEFAULT_ROOT).resolve()
    fetched = root / f"Retro Rewind {release['rr_version']} (downloaded)"
    mine = i8.RR_SRC

    def needs_rr() -> bool:
        try:
            return bool(rr_problem(i8, release))
        except SystemExit:  # a Code.pul naming no single commit: a modded RR, or a run stopped while unpacking it
            return True

    fetch = "rr" in builds and args.fetch_rr and needs_rr()
    if fetch:
        use_rr(i8, fetched / "RetroRewind6")
        fetch = needs_rr()  # an earlier run may have downloaded it

    # Everything is checked before anything is written.
    print(f"disc: {i8.game_disc()}")
    if "dolphin" in targets and (problem := dolphin_problem(dolphin)):
        sys.exit(problem)
    if "rr" in builds and mine.parent != fetched and (root.is_relative_to(mine) or mine.is_relative_to(root)):
        sys.exit(f"{root} overlaps your Retro Rewind {mine}: install somewhere else")
    if "rr" in builds and not fetch and (problem := rr_problem(i8, release)):
        sys.exit(problem)
    if "rr" in builds and (mods := rr_mods(i8)):
        print(f"[setup] {MODS_NOTE.format(' and '.join(mods))}")
    if "mkw" in builds and "wiicompiled" not in targets and not has_dolphin_tool:
        sys.exit("the Mario Kart Wii build without WiiCompiled reads the disc with DolphinTool.exe, which comes with "
                 "Dolphin: pick your Dolphin.exe")
    if "wiicompiled" in targets and "WiiCompiled-Setup.exe" not in release["files"]:
        sys.exit("this release has no WiiCompiled-Setup.exe: install for Dolphin, or download a release that has it")
    if "wii" in targets:
        if "wit/wit.exe" not in release["files"]:
            sys.exit("this release has no wit, which makes the Wii images: download a release that has it")
        with i8.game_disc().open("rb") as f:
            rvz = f.read(4) == i8.RVZ_MAGIC
        if rvz and not has_dolphin_tool:
            sys.exit(f"{i8.game_disc()} is RVZ, which only DolphinTool reads: pick your Dolphin.exe, or an ISO or WBFS disc image")
    for name, digest in release["files"].items():
        if i8.sha256(payload / name) != digest:
            sys.exit(f"{payload / name} is not the file this release shipped: download the release again")
    layouts = {b: i8.Layout(root / BUILDS[b][0], BUILDS[b][1]) for b in builds}
    need, _ = space(i8, release, layouts, targets, fetch)
    existing = next(p for p in (root, *root.parents) if p.exists())
    if shutil.disk_usage(existing).free < need:
        sys.exit(f"{existing} has {shutil.disk_usage(existing).free >> 20} MB free; the setup needs {need >> 20} MB")
    if fetch:
        print(f"[step] Retro Rewind {release['rr_version']}, download (about 2 GB)")
        fetch_rr(fetched, release["rr_version"])
        if problem := rr_problem(i8, release):
            sys.exit(f"Retro Rewind's download is not the version this setup needs: {problem}")

    for b in builds:
        folder, vanilla, variant = BUILDS[b]
        layout = layouts[b]
        print(f"\n== {folder}: {layout.root}")
        print(f"[step] {folder}, copy")
        step = argparse.Namespace(slot=0, variant=variant, src=payload / b / "Code.pul")
        i8.cmd_prepare(layout, step)
        plays = {}
        if "wiicompiled" in targets:
            print(f"[step] {folder}, WiiCompiled (it can take a while: hang tight)")
            # WiiCompiled translates the pack's Code.pul; code then takes the vanilla MenuMulti.szs from the disc it extracted.
            for pul in ["Code.pul"] + (["Loader.pul"] if vanilla else []):
                i8.copy_file(layout, step.src.with_name(pul), layout.rr / "Binaries" / pul)
            if (layout.backup / "shortcuts.json").exists():
                i8.restore_shortcuts(layout)  # a setup stopped before it put your shortcuts back
            drop_old_installs(i8, layout)  # an upgrade stopped before it dropped the old one would block this one
            installer = payload / "WiiCompiled-Setup.exe"
            if not (layout.wc_install / "install-state.json").exists():
                log, rc = "setup", i8.install_wiicompiled(layout, installer)
            elif wc_current(layout, release):
                log, rc = "repair", i8.repair_wiicompiled(layout)
            else:  # installed by another release's installer
                log, rc = "setup", i8.upgrade_wiicompiled(layout, installer)
            if rc:
                sys.exit(f"WiiCompiled's setup failed for {folder}; its log is %TEMP%\\WiiCompiled-{log}.log")
            i8.guard(layout, layout.wc_install / WC_MARK).write_text(release["files"][installer.name], encoding="utf-8")
            drop_old_installs(i8, layout)
            i8.guard(layout, layout.wc_userdata / "window-icon.rgba").unlink(missing_ok=True)  # an earlier release's window icon
            i8.guard(layout, layout.wc_userdata / "window-title.txt").write_text(f"{folder} - 8 Player Splitscreen", encoding="utf-8")
            plays["Play (WiiCompiled).cmd"] = f'"{layout.wc_install / "WiiCompiled-Setup.exe"}" --launch-retro'
        print(f"[step] {folder}, menus and code")
        i8.cmd_code(layout, step)
        if "dolphin" in targets:
            plays["Play (Dolphin).cmd"] = f'"{dolphin}" -u "{layout.dolphin}" -e "{layout.launch}"'
        if "wii" in targets:
            print(f"[step] {folder}, Wii image")
            i8.cmd_wii(layout, step)
        for name, command in plays.items():
            play = i8.guard(layout, layout.root / name)
            # cmd.exe reads a batch file in the console's code page and expands %, so UTF-8 is switched on and % doubled.
            line = f'@start "" {command}'.replace("%", "%%")
            play.write_bytes(f"@chcp 65001>nul\r\n{line}\r\n".encode("utf-8"))
            print(f"[setup] wrote {play}")
    print(f"\nDone in {root}.")
    if {"dolphin", "wiicompiled"} & set(targets):
        print("Play from each copy's \"Play (...).cmd\".")
    if "wii" in targets:
        print("On a Wii: copy each copy's Wii/wbfs/ folders into the wbfs folder on USB Loader GX's drive.")
    return 0


def window(args: argparse.Namespace) -> int:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    win = tk.Tk()
    win.title(TITLE)
    win.resizable(False, False)
    win.report_callback_exception = lambda *exc: messagebox.showerror(TITLE, "".join(traceback.format_exception(*exc)), parent=win)
    try:
        payload, release, i8 = load(args)
    except SystemExit as e:
        win.withdraw()
        messagebox.showerror(TITLE, str(e))
        return 1
    win.iconbitmap(default=str(i8.ROOT / "tools" / "release_docs" / "wii-wheel.ico"))

    def problem(what: str, check) -> str | None:
        try:
            return check()
        except SystemExit as e:
            return str(e).removeprefix("[install_8p] ")
        except Exception as e:
            return f"{what} could not be read ({e})"

    style = ttk.Style(win)
    style.configure("Head.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("Sub.TLabel", foreground="#555555")
    style.configure("Ok.TLabel", foreground="#1a7f37", font=("Segoe UI", 10, "bold"))
    style.configure("Bad.TLabel", foreground="#cf222e", font=("Segoe UI", 10, "bold"))
    style.configure("Error.TLabel", foreground="#cf222e")
    style.configure("Warn.TLabel", foreground="#bc4c00")
    f = ttk.Frame(win, padding=(20, 16, 20, 16))
    f.grid(sticky="nsew")
    ttk.Label(f, text=f"MKWii 8P Splitscreen {release['version']}", style="Head.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(f, text="5 to 8 player local splitscreen for Mario Kart Wii", style="Sub.TLabel").grid(
        row=1, column=0, sticky="w", pady=(0, 10))

    checks, boxes, controls = {}, {}, []
    paths, notes, probs = {}, {}, {}
    dead = set()  # the build or place to play whose file is missing, unticked and greyed
    files = ttk.LabelFrame(f, text="Your files", padding=(10, 6))
    files.grid(row=2, column=0, sticky="ew", pady=(0, 8))
    ttk.Label(files, style="Sub.TLabel", text="Filled in from Wheel Wizard. Change any of them with Browse."
              if i8.ww_config() else "Wheel Wizard was not found. Pick your files with Browse.").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
    discs = [("Wii disc images", "*.iso *.wbfs *.rvz *.wia *.ciso *.gcz"), ("All files", "*.*")]
    # key: the label, what it is for, its value now, how a value is used, its check, how to pick one
    fields = {
        "disc": ("Mario Kart Wii disc image (PAL, USA or Japan)", "Needed for every build.", i8.DISC or i8.ww_config().get("GameLocation"),
                 lambda p: setattr(i8, "DISC", p), lambda: i8.game_disc() and None,
                 lambda: filedialog.askopenfilename(parent=win, filetypes=discs)),
        "rr": (f"Retro Rewind {release['rr_version']} folder", "Only for the Retro Rewind build: its RetroRewind6 folder.",
               i8.RR_SRC if (i8.RR_SRC / "version.txt").exists() else None, lambda p: use_rr(i8, p),
               lambda: rr_problem(i8, release), lambda: filedialog.askdirectory(parent=win)),
        "dolphin": ("Dolphin (Dolphin.exe)", "Only to play in Dolphin.", i8.dolphin_exe(), lambda p: use_dolphin(i8, p),
                    lambda: dolphin_problem(i8.dolphin_exe()),
                    lambda: filedialog.askopenfilename(parent=win, filetypes=[("Dolphin", "Dolphin.exe"), ("All files", "*.*")])),
    }

    def field(key: str) -> str:
        return paths[key].get().strip().strip('"')

    def check(key: str) -> None:
        label, need, _, use, test, _ = fields[key]
        value = field(key)
        if value:
            use(Path(value))
        probs[key] = problem(label, test) if value else need
        mark, note = notes[key]
        mark.configure(text="✗" if probs[key] else "✓", style="Bad.TLabel" if probs[key] else "Ok.TLabel")
        note.configure(text=probs[key] or "")
        for widget in [note] + ([fetch_box] if key == "rr" else []):
            if probs[key]:
                widget.grid()
            else:
                widget.grid_remove()
        if key == "rr":
            # In fetch_box's row, which shows only when this folder cannot be used.
            mods = [] if probs[key] else rr_mods(i8)
            mods_note.configure(text=f"⚠ {MODS_NOTE.format(' and '.join(mods))}")
            if mods:
                mods_note.grid()
            else:
                mods_note.grid_remove()
        bad = probs[key] and not (key == "rr" and fetch_rr.get())
        if key in boxes:
            if bad and key not in dead:
                dead.add(key)
                checks[key].set(False)
                boxes[key].state(["disabled"])
            elif not bad and key in dead:
                dead.discard(key)
                checks[key].set(True)
                boxes[key].state(["!disabled"])
        refresh()

    for i, (key, (label, _, value, _, _, pick)) in enumerate(fields.items()):
        mark = ttk.Label(files)
        mark.grid(row=4 * i + 1, column=0, sticky="w", padx=(0, 6))
        ttk.Label(files, text=label).grid(row=4 * i + 1, column=1, columnspan=2, sticky="w")
        paths[key] = tk.StringVar(win, value=str(value or ""))
        entry = ttk.Entry(files, textvariable=paths[key], width=54)
        entry.grid(row=4 * i + 2, column=1, sticky="ew", padx=(0, 6))
        browse = ttk.Button(files, text="Browse…", command=lambda key=key, pick=pick: (p := pick()) and paths[key].set(
            os.path.normpath(p)))
        browse.grid(row=4 * i + 2, column=2)
        note = ttk.Label(files, style="Error.TLabel", wraplength=480)
        note.grid(row=4 * i + 3, column=1, columnspan=2, sticky="w")
        notes[key] = (mark, note)
        controls += [entry, browse]
    fetch_rr = tk.BooleanVar(win, value=True)
    fetch_box = ttk.Checkbutton(files, text=f"Download Retro Rewind {release['rr_version']} for me (about 2 GB)",
                                variable=fetch_rr, command=lambda: check("rr"))
    fetch_box.grid(row=4 * list(fields).index("rr") + 4, column=1, columnspan=2, sticky="w")
    controls.append(fetch_box)
    mods_note = ttk.Label(files, style="Warn.TLabel", wraplength=480)
    mods_note.grid(row=4 * list(fields).index("rr") + 4, column=1, columnspan=2, sticky="w")

    def group(row: int, title: str, items) -> None:
        frame = ttk.LabelFrame(f, text=title, padding=(10, 6))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        for i, (key, name, note, on) in enumerate(items):
            checks[key] = tk.BooleanVar(win, value=on)
            boxes[key] = ttk.Checkbutton(frame, text=name, variable=checks[key], width=16, command=refresh)
            boxes[key].grid(row=i, column=0, sticky="w")
            ttk.Label(frame, text=note, style="Sub.TLabel").grid(row=i, column=1, sticky="w")
            controls.append(boxes[key])

    def fetching(builds) -> bool:
        return "rr" in builds and bool(probs.get("rr")) and fetch_rr.get()

    def refresh() -> None:
        builds = [b for b in BUILDS if checks[b].get()]
        targets = [t for t in TARGETS if checks[t].get()]
        ready = not probs.get("disc") and builds and targets and root_var.get().strip()
        go.state(["!disabled"] if ready else ["disabled"])
        space_note.configure(text="")
        if not ready:
            return
        try:
            root = Path(root_var.get().strip()).resolve()
            need, done = space(i8, release, {b: i8.Layout(root / BUILDS[b][0], BUILDS[b][1]) for b in builds}, targets,
                               fetching(builds))
            drive = next(p for p in (root, *root.parents) if p.exists())
            free = shutil.disk_usage(drive).free
        except (OSError, SystemExit, StopIteration):
            return
        space_note.configure(text=f"Needs about {need / (1 << 30):.0f} GB free on {drive.drive or drive} while installing "
                                  f"({free / (1 << 30):.0f} GB free now); the copies take about {done / (1 << 30):.0f} GB "
                                  "when done.", style="Error.TLabel" if free < need else "Sub.TLabel")

    group(3, "Builds", [("rr", "Retro Rewind", f"Retro Rewind {release['rr_version']} with SplitScreen8, offline", True),
                        ("mkw", "Mario Kart Wii", "the original game, everything unlocked", True)])
    group(4, "Play in", [("dolphin", "Dolphin", "", True),
                         ("wiicompiled", "WiiCompiled", "the PC port, about 5 GB a copy", True),
                         ("wii", "A real Wii", "images for USB Loader GX", False)])
    dest = ttk.LabelFrame(f, text="Install to", padding=(10, 6))
    dest.grid(row=5, column=0, sticky="ew", pady=(0, 8))
    root_var = tk.StringVar(win, value=str(DEFAULT_ROOT))
    entry = ttk.Entry(dest, textvariable=root_var, width=58)
    entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    browse = ttk.Button(dest, text="Browse…", command=lambda: (p := filedialog.askdirectory(
        parent=win, initialdir=root_var.get())) and root_var.set(os.path.normpath(p)))
    browse.grid(row=0, column=1)
    space_note = ttk.Label(dest, style="Sub.TLabel", wraplength=520)
    space_note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
    controls += [entry, browse]
    root_var.trace_add("write", lambda *_: refresh())

    bar = ttk.Frame(f)
    bar.grid(row=7, column=0, sticky="e", pady=(6, 0))
    go = ttk.Button(bar, text="Install", default="active")
    go.grid(row=0, column=0, padx=(0, 6))
    close = ttk.Button(bar, text="Close", command=win.destroy)
    close.grid(row=0, column=1)
    for key in fields:
        check(key)
        paths[key].trace_add("write", lambda *_, key=key: check(key))

    lines: queue.Queue = queue.Queue()
    state = {"proc": None}

    def start() -> None:
        builds = [b for b in BUILDS if checks[b].get()]
        targets = [t for t in TARGETS if checks[t].get()]
        root = Path(root_var.get().strip()).resolve()
        # A console run of this setup: wit, DolphinTool and WiiCompiled's installer print to the process's console.
        cmd = [sys.executable, *([] if getattr(sys, "frozen", False) else [__file__]), "--payload", str(payload)]
        cmd += ["--builds", ",".join(builds), "--targets", ",".join(targets), "--root", str(root), "--yes"]
        cmd += [a for key in fields if field(key) for a in (f"--{key}", field(key))]
        fetch = fetching(builds)
        cmd += ["--fetch-rr"] * fetch
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        for c in [*controls, go]:
            c.state(["disabled"])
        close.state(["disabled"])
        state.update(proc=proc, root=root, targets=targets, last="", step=0, what="")
        if "frame" in state:
            state["frame"].destroy()
        prog = state["frame"] = ttk.LabelFrame(f, text="Installing", padding=(10, 6))
        prog.grid(row=6, column=0, sticky="ew")
        state["status"] = ttk.Label(prog, text="Checking your files and the release", wraplength=520)
        state["status"].grid(row=0, column=0, sticky="w")
        state["bar"] = ttk.Progressbar(prog, length=520, maximum=len(builds) * (2 + len({"wiicompiled", "wii"} & set(targets))) + fetch)
        state["bar"].grid(row=1, column=0, sticky="ew", pady=(4, 4))
        text = tk.Text(prog, width=72, height=9, font=("Consolas", 9), relief="solid", borderwidth=1, wrap="char",
                       state="disabled")
        state["text"] = text
        toggle = ttk.Button(prog, text="▸ Show details", style="Toolbutton")

        def show(open_: bool) -> None:
            if open_:
                text.grid(row=3, column=0, sticky="ew", pady=(4, 0))
            else:
                text.grid_remove()
            toggle.configure(text="▾ Hide details" if open_ else "▸ Show details", command=lambda: show(not open_))
        state["show"] = show
        toggle.grid(row=2, column=0, sticky="w")
        show(False)
        threading.Thread(target=lambda: [lines.put(l.rstrip()) for l in proc.stdout] + [lines.put(None)], daemon=True).start()
        win.after(100, poll)

    def poll() -> None:
        for _ in range(200):  # lines a tick at most, so WiiCompiled's compile output cannot stall the window
            try:
                line = lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                return finish(state["proc"].wait())
            state["last"] = line.strip().removeprefix("[install_8p] ") or state["last"]
            state["text"].configure(state="normal")
            state["text"].insert("end", line + "\n")
            state["text"].configure(state="disabled")
            if line.startswith("[step] "):
                state["bar"].configure(value=state["step"])
                state["step"] += 1
                state["what"] = f"Step {state['step']} of {int(state['bar']['maximum'])}: {line[7:]}"
                state["status"].configure(text=state["what"])
            elif line.startswith('{"type":"progress"'):  # WiiCompiled's installer, run with --progress-json
                try:
                    progress = json.loads(line)
                    percent = min(max(float(progress["percent"]), 0), 100)
                except (ValueError, KeyError, TypeError):
                    continue
                state["bar"].configure(value=state["step"] - 1 + percent / 100)
                state["status"].configure(text=f"{state['what']}\n{progress.get('message', '')} ({percent:.0f}%)")
        state["text"].see("end")
        win.after(100, poll)

    def finish(code: int) -> None:
        state["proc"] = None
        close.state(["!disabled"])
        if code:
            state["status"].configure(text=f"{state['last'] or f'exit {code}'}\nChange what it says, then press Install again to carry on.",
                                      style="Error.TLabel")
            state["frame"].configure(text="Stopped")
            state["show"](True)
            for c in controls:
                c.state(["!disabled"])
            for key in dead:
                boxes[key].state(["disabled"])
            refresh()
            return
        state["bar"].configure(value=state["bar"]["maximum"])
        plays = " or ".join(f'"Play ({n})"' for t, n in (("dolphin", "Dolphin"), ("wiicompiled", "WiiCompiled"))
                            if t in state["targets"])
        done = [f"Your copies are in {state['root']}."]
        if plays:
            done.append(f"Open a copy's folder and double-click {plays}.")
        if "wii" in state["targets"]:
            done.append("On a Wii: copy each copy's Wii\\wbfs folders into the wbfs folder on your USB Loader GX drive.")
        state["status"].configure(text="\n".join(done))
        state["frame"].configure(text="Done")
        ttk.Button(bar, text="Open folder", default="active", command=lambda: os.startfile(state["root"])).grid(
            row=0, column=0, padx=(0, 6))
        go.grid_remove()

    def on_close() -> None:
        proc = state["proc"]
        if proc is None:
            win.destroy()
        elif messagebox.askyesno(TITLE, "Stop the install? The copy it is making is left unfinished; "
                                        "run this setup again to finish it.", parent=win):
            if proc.poll() is None:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            win.destroy()

    go.configure(command=start)
    win.protocol("WM_DELETE_WINDOW", on_close)
    win.mainloop()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--builds", help="rr, mkw or rr,mkw (default: rr,mkw)")
    ap.add_argument("--targets", help="any of dolphin, wiicompiled, wii, comma-separated (default: dolphin,wiicompiled)")
    ap.add_argument("--root", type=Path, help="the folder both copies go in (default: Documents/MKWii 8 Player)")
    ap.add_argument("--rr", type=Path, help="your RetroRewind6 folder, in place of Wheel Wizard's")
    ap.add_argument("--fetch-rr", action="store_true",
                    help="when that Retro Rewind is missing or another version, download the right one into --root")
    ap.add_argument("--disc", type=Path, help="your Mario Kart Wii disc image (PAL, USA or Japan), in place of Wheel Wizard's")
    ap.add_argument("--dolphin", type=Path, help="your Dolphin.exe, in place of Wheel Wizard's")
    ap.add_argument("--payload", type=Path, help="the release's payload/ (default: the one beside this setup)")
    ap.add_argument("--yes", action="store_true", help="install in the console with the defaults for what is not given")
    args = ap.parse_args()
    if not (args.builds or args.targets or args.root or args.yes):
        try:
            sys.exit(window(args))
        except Exception:  # the console is hidden, so a box is the only place an error can show
            ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), TITLE, 0x10)
            sys.exit(1)
    if not sys.stdout.isatty():  # the window reads this run's log through a pipe
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    try:
        code = install(args)
    except SystemExit as e:
        code = e.code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            code = 1
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
