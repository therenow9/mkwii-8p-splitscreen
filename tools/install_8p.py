#!/usr/bin/env python3
"""Install and drive the separate, local-only SplitScreen8 copy of Retro Rewind.

Everything lives under one root (default: Documents/Modding/Mario Kart Wii  8 Player Splitscreen):
  Dolphin/                      a Dolphin user dir (-u): own NAND, own config, own Riivolution root
  Dolphin/Load/Riivolution/     the single RR copy (RetroRewind6/) and its XML; WiiCompiled uses it too
  SplitScreen8.json             Dolphin launch descriptor (the disc + the copy's XML)
  pads/                         the owner's 8P pad mapping, saved before an unattended launch overrides it
  dolphin.pid                   the pid of the Dolphin that `dolphin` started; `stop` and `shot` use it
  shots/                        `shot`'s PNGs
  cache/                        `code`'s generated MenuMulti.szs, by a hash of its inputs (the newest 4)
  WiiCompiled/                  a portable WiiCompiled install (portable.txt, Install/, UserData/)
  wc-runs/                      `wiicompiled run`'s stdout/stderr captures and PNGs

The owner's own Retro Rewind, Dolphin and WiiCompiled are only ever read. RR's save lives at the
XML's <savegame external="/riivolution/save/RetroWFC/..."> redirect, which Dolphin resolves against
the launch descriptor's Riivolution root (WiiCompiled: next to retro_rewind_root), so the save stays
in this root only because that root is ours. Config files that carry absolute paths (Dolphin.ini's
NANDRootPath, WiiCompiled's Config.toml) are never copied.

Usage:
  install_8p.py prepare                        copy RR, write the Dolphin user dir and launch file
  install_8p.py code release|stock|ss8[-debug6|-debug8][-boot]|ss8[-menu]  put that Code.pul (+ MenuMulti.szs) into the copy
  install_8p.py script FILE                    pack a menu script into the copy's NAND, where a -menu build reads it at boot
  install_8p.py dolphin [--debugger] [--gdb [PORT]] [--pause-on-panic] [--unattended [--wiimotes] [--res N] [--fast]]
                [--dump [--footage]]
                                               launch the copy in Dolphin (GDB stub on PORT, default 2345;
                                               --unattended keeps GC ports 1-4 plugged in with no host pad;
                                               --wiimotes also connects Wii Remotes 1-4 with a Classic Controller;
                                               --res sets its internal resolution; --fast runs it at unlimited
                                               emulation speed, for menu-script lanes, D114)
  install_8p.py stop [--timeout S]             force that Dolphin closed by pid (with S: try a graceful close for S seconds first)
  install_8p.py shot [--out DIR]               PNG of each visible window of that Dolphin (default shots/)
  install_8p.py log [--tail N] [--grep REGEX]  print Dolphin/Logs/dolphin.log (earlier runs: dolphin.<time>.log)
  install_8p.py wiicompiled setup|check|repair|launch|restore-shortcuts [--installer EXE]
  install_8p.py wiicompiled upgrade --installer EXE   install a new release, keeping the old Install as a rollback
  install_8p.py wiicompiled run [--seconds N] [--shot-every S] [--patched]
                                               launch, screenshot, answer a "Fatal Error" box, then end it (wc-runs/)
  install_8p.py wiicompiled patch [--base COMMIT]   build RetroRewind.exe from external/Wiicompiled's working tree
                                               (its runtime/ and aurora-main/ changes over the install's commit) into
                                               WiiCompiled/Patched/RetroRewind (after every repair); launch/run --patched
  install_8p.py wii                            the copy as one WBFS for USB Loader GX, in Wii/wbfs/ (dolphin --image boots it)
  install_8p.py isolation snapshot|verify      hash the owner's real files before / after a run

--vanilla works on a second root beside it (".../Mario Kart Wii  8 Player Splitscreen vanilla", and its slots):
plain Mario Kart Wii with SplitScreen8 and no Retro Rewind. Its Riivolution pack is SplitScreen8/ (Binaries/ with
Loader.pul, Code.pul and ss8-menu.bin, UI/MenuMulti.szs generated from the disc's own, and xml/RetroRewind6.xml);
its save is seeded once from the 8P copy's. It never reads the RR folder:
  install_8p.py --vanilla prepare | code vanilla[-menu|-boot|-debug6-boot|-debug8-boot] | script FILE | dolphin ... | stop | shot | log
  install_8p.py --vanilla dolphin --plain [--movie FILE.dtm] ...   the plain disc, no pack, its NAND save (tools/dtm.py)
  install_8p.py --vanilla wiicompiled setup|upgrade|repair|run|patch|launch ...   its own portable WiiCompiled, on the pack

Remove the 8P WiiCompiled by deleting this root, never with its --uninstall: that deletes the
shared desktop and Start menu shortcuts of the owner's own install.
"""
from __future__ import annotations

import argparse
import codecs
import configparser
import ctypes
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import winreg
import xml.etree.ElementTree as ET
import zlib
from ctypes import wintypes
from pathlib import Path

# A frozen setup (tools/ss8_setup.py) unpacks the generator and wuj5 under _MEIPASS, at their paths in the tree.
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
HOME = Path.home()
DEFAULT_INSTALL_ROOT = HOME / "Documents" / "Modding" / "Mario Kart Wii  8 Player Splitscreen"
CT_MKWII = Path(os.environ["APPDATA"]) / "CT-MKWII"


def ww_config() -> dict:
    config = CT_MKWII / "config.json"
    return json.loads(config.read_text(encoding="utf-8-sig")) if config.exists() else {}


# Without Wheel Wizard, Dolphin's own user folder: Documents' when it has one, else AppData's (Dolphin's order).
DOLPHIN_USER_SRC = Path(ww_config().get("UserFolderPath") or next(
    (p for p in (HOME / "Documents" / "Dolphin Emulator", Path(os.environ["APPDATA"]) / "Dolphin Emulator") if p.is_dir()),
    HOME / "Documents" / "Dolphin Emulator"))
WW_RIIVO = DOLPHIN_USER_SRC / "Load" / "Riivolution" / "WheelWizard"
RR_SRC = WW_RIIVO / "RetroRewind6"
RR_XML_SRC = WW_RIIVO / "riivolution" / "RetroRewind6.xml"
# A player's mods, never copied: Wheel Wizard fills Patches, which RR loads itself, and a few character packs leave
# 5-8 players' menus 2 MB short of MEM2. MyStuff is Riivolution's older mod folder.
RR_MODS = ("Patches", "MyStuff")
RECOMP_SRC = CT_MKWII / "Recomp"
# The installed WiiCompiled-Setup.exe has no payload; a fresh install needs the full installer that
# Wheel Wizard keeps in its cache.
SETUP_CACHE = RECOMP_SRC / "Cache"

CODE_PULS = {
    "release": RR_SRC / "Binaries" / "Code.pul",
    "stock": ROOT / "build" / "code-stock" / "Code.pul",
    "ss8": ROOT / "build" / "code" / "Code.pul",
    "ss8-debug6": ROOT / "build" / "code-debug6" / "Code.pul",
    "ss8-debug8": ROOT / "build" / "code-debug8" / "Code.pul",
    "ss8-boot": ROOT / "build" / "code-boot" / "Code.pul",
    "ss8-debug6-boot": ROOT / "build" / "code-debug6-boot" / "Code.pul",
    "ss8-debug8-boot": ROOT / "build" / "code-debug8-boot" / "Code.pul",
    "ss8-menu": ROOT / "build" / "code-menu" / "Code.pul",
    "ss8-debug8-menu": ROOT / "build" / "code-debug8-menu" / "Code.pul",
    # The upstream PR branch on stock RR, built from its worktrees.
    "stock-menu": ROOT / "build" / "code-stock-menu" / "Code.pul",
    "pr-a-menu": ROOT / "build" / "code-pr-a-menu" / "Code.pul",
    "pr-menu": ROOT / "build" / "code-pr-menu" / "Code.pul",
    "pr": ROOT / "build" / "code-pr" / "Code.pul",
    "markers-menu": ROOT / "build" / "code-markers-menu" / "Code.pul",
    # Mario Kart Wii without Retro Rewind (build_code.py --vanilla), on the --vanilla root only.
    "vanilla": ROOT / "build" / "code-vanilla" / "Code.pul",
    "vanilla-menu": ROOT / "build" / "code-vanilla-menu" / "Code.pul",
    "vanilla-boot": ROOT / "build" / "code-vanilla-boot" / "Code.pul",
    "vanilla-debug6-boot": ROOT / "build" / "code-vanilla-debug6-boot" / "Code.pul",
    "vanilla-debug8-boot": ROOT / "build" / "code-vanilla-debug8-boot" / "Code.pul",
}
# A PR variant's MenuMulti comes from the branch's own generator, as RR would build it.
PR_TREES = {"pr-a-menu": ROOT / "build" / "rr-pr-a", "pr-menu": ROOT / "build" / "rr-pr", "pr": ROOT / "build" / "rr-pr", "markers-menu": ROOT / "build" / "rr-markers"}
# Pad, graphics and controller-profile settings only: these hold no paths.
KEEP_LOGS = 10
DOLPHIN_CONFIG_COPIES = ["GCPadNew.ini", "WiimoteNew.ini", "GFX.ini", "Profiles"]
# The owner maps the 8P copy's pads apart from their own Dolphin (owner, 2026-10-07): once the copy
# has these, prepare seeds only what is missing and keeps the copy's values.
PAD_COPIES = frozenset({"GCPadNew.ini", "WiimoteNew.ini", "Profiles"})
DOLPHIN_INI_KEEP = {
    "Core": ["SIDevice0", "SIDevice1", "SIDevice2", "SIDevice3", "GFXBackend", "CPUCore", "CPUThread", "DSPHLE"],
    "DSP": ["Backend"],
    "Hotkeys": None,  # the whole section: key codes only
}
DOLPHIN_INI_PADS = {
    "Core": ["SIDevice0", "SIDevice1", "SIDevice2", "SIDevice3",
             "WiimoteContinuousScanning", "WiimoteControllerInterface", "WiimoteEnableSpeaker"],
    "BluetoothPassthrough": None,
    "Input": ["BackgroundInput"],
    "SDL_Hints": None,  # which host-pad backends list the owner's controllers
}
SHORTCUT_NAME = "wiicompiled (base) (beta).lnk"
INVALID = re.compile(r"Invalid (?:read|write) .*?PC = (0x[0-9a-f]+)")
# A command Dolphin skips, which stops a Wii's GPU; Dolphin logs only a run's first.
GPU_FIFO = re.compile(r"GFX FIFO: Unknown Opcode \((0x[0-9a-f]+)")


def slot_root(slot: int, vanilla: bool = False) -> Path:
    """Slot 0 is the owner's 8P copy; slot K a full copy beside it for parallel unattended lanes, made by prepare."""
    base = DEFAULT_INSTALL_ROOT.with_name(DEFAULT_INSTALL_ROOT.name + (" vanilla" if vanilla else ""))
    return base if slot == 0 else base.with_name(f"{base.name} slot {slot}")


class Layout:
    def __init__(self, root: Path, vanilla: bool = False):
        self.root = root.resolve()
        self.vanilla = vanilla
        # guard() trusts the root, so a root inside or around the owner's folders would let every
        # write through.
        for owner in (DOLPHIN_USER_SRC.resolve(), CT_MKWII.resolve()):
            if self.root.is_relative_to(owner) or owner.is_relative_to(self.root):
                fail(f"--root {self.root} overlaps the owner's {owner}")
        self.dolphin = self.root / "Dolphin"
        self.riivo = self.dolphin / "Load" / "Riivolution"
        # The Riivolution pack: the RR copy, or the vanilla build's own.
        pack = "SplitScreen8" if vanilla else "RetroRewind6"
        self.rr = self.riivo / pack
        # WiiCompiled's one Pulsar profile reads only the pack's xml/RetroRewind6.xml and section "Retro Rewind"'s Pack
        # (projects/mkwii/recomp.yml), so the vanilla pack's XML is there, which Dolphin's descriptor names too.
        self.xml = self.rr / "xml" / "RetroRewind6.xml" if vanilla else self.riivo / "riivolution" / f"{pack}.xml"
        self.save_root = self.riivo / "riivolution" / "save" / ("SplitScreen8" if vanilla else "RetroWFC")
        self.launch = self.root / "SplitScreen8.json"
        self.pid_file = self.root / "dolphin.pid"
        self.dolphin_log = self.dolphin / "Logs" / "dolphin.log"
        # Pulsar's IO in Dolphin is the NAND's /shared2/Pulsar plus RR's mod folder: RRGameSettings.pul, ss8-menu.bin.
        self.nand_mod = self.dolphin / "Wii" / "shared2" / "Pulsar" / "RetroRewind6"
        self.wc_install = self.root / "WiiCompiled" / "Install"
        self.wc_userdata = self.root / "WiiCompiled" / "UserData"
        self.wc_runs = self.root / "wc-runs"
        self.backup = self.root / "backup"
        self.snapshot = self.root / "isolation.json"

    @property
    def disc(self) -> Path:
        # The disc the copy launches (prepare's), else the chosen one.
        return game_disc(json.loads(self.launch.read_text(encoding="utf-8"))["base-file"] if self.launch.exists() else None)

    @property
    def region(self) -> str:
        return REGIONS[game_id(self.disc)]

    @property
    def save(self) -> Path:
        # Riivolution's {$__gameid}{$__region}.
        return self.save_root / f"RMC{self.region}" / "rksys.dat"

    @property
    def title(self) -> str:
        # The disc's NAND title folder (00010004/524d43xx), where the game keeps rksys.dat.
        return title_id(self.region)


def fail(msg: str) -> None:
    sys.exit(f"[install_8p] {msg}")


def guard(layout: Layout, path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(layout.root):
        fail(f"refusing to write outside the 8P root: {path}")
    return path


def copy_file(layout: Layout, src: Path, dst: Path) -> None:
    dst = guard(layout, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def sync_tree(layout: Layout, src: Path, dst: Path, owned_elsewhere: frozenset[str] = frozenset(),
              left_out: tuple[str, ...] = ()) -> int:
    copied = 0
    for s in src.rglob("*"):
        rel = s.relative_to(src)
        if s.is_dir() or rel.parts[0].casefold() in map(str.casefold, left_out):
            continue
        d = dst / rel
        if rel.as_posix() in owned_elsewhere and d.exists():
            continue
        st = s.stat()
        if d.exists() and d.stat().st_size == st.st_size and int(d.stat().st_mtime) == int(st.st_mtime):
            continue
        copy_file(layout, s, d)
        copied += 1
    return copied


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def code_pul_commit(path: Path) -> str:
    """The commit a Code.pul names (Version.cpp's GIT_COMMIT, UTF-16BE): 52f23582 for RR's release, <sha>-ss8 for ours."""
    found = {m.group().decode("utf-16-be").strip("\0")
             for m in re.finditer(rb"(?:\x00[0-9a-f]){8}(?:\x00[-a-z0-9])*\x00\x00", path.read_bytes())}
    if len(found) != 1:
        fail(f"{path} names {len(found)} commits, not one: {sorted(found)}")
    return found.pop()


def game_id(disc: Path) -> str:
    with disc.open("rb") as f:
        head = f.read(0x206)
    # RVZ/WIA keep the disc header uncompressed at 0x58, WBFS at 0x200; ISO has it at 0.
    off = {b"RVZ\x01": 0x58, b"WIA\x01": 0x58, b"WBFS": 0x200}.get(head[:4], 0)
    return head[off:off + 6].decode("ascii", "replace")


DISC: Path | None = None  # the setup's --disc, in place of Wheel Wizard's GameLocation
DOLPHIN: Path | None = None  # the setup's --dolphin, in place of Wheel Wizard's DolphinLocation


def dolphin_exe() -> Path | None:
    location = DOLPHIN or ww_config().get("DolphinLocation")
    return Path(location) if location else None


# The discs SplitScreen8 runs on, by game ID: Code.pul and WiiCompiled have no Korean (RMCK01) build.
REGIONS = {"RMCP01": "P", "RMCE01": "E", "RMCJ01": "J"}


def title_id(region: str) -> str:
    return f"524d43{ord(region):02x}"


def game_disc(location: str | None = None) -> Path:
    location = location or DISC or ww_config().get("GameLocation")
    if not location:
        fail("no disc image: pick your Mario Kart Wii (PAL, USA or Japan), or set it in Wheel Wizard")
    disc = Path(location)
    if not disc.is_file():
        fail(f"the disc does not exist: {disc}")
    if game_id(disc) not in REGIONS:
        fail(f"{disc} is {game_id(disc)}, not a Mario Kart Wii this project runs on: PAL RMCP01, USA RMCE01 or "
             f"Japan RMCJ01 (Retro Rewind and WiiCompiled have no Korean RMCK01 build)")
    return disc


def paths_outside(layout: Layout, text: str) -> list[str]:
    bad = [m.group(0) for m in re.finditer(r"\\\\[^\r\n]*|%[A-Za-z_]+%[^\r\n]*", text)]  # UNC, %VAR%
    for m in re.finditer(r"[A-Za-z]:[\\/][^\r\n]*", text):
        p = m.group(0).strip()
        if not Path(p).resolve().is_relative_to(layout.root):
            bad.append(p)
    return bad


def write_dolphin_ini(layout: Layout) -> None:
    src = configparser.ConfigParser(interpolation=None, strict=False)
    src.optionxform = str
    src.read(DOLPHIN_USER_SRC / "Config" / "Dolphin.ini", encoding="utf-8")

    out = configparser.ConfigParser(interpolation=None)
    out.optionxform = str
    out["General"] = {"NANDRootPath": str(layout.dolphin / "Wii")}
    out["Core"] = {"WiiSDCard": "False"}
    # A panic is logged instead of blocking on a dialog. ConfirmStop off keeps a close from asking
    # for a click.
    out["Interface"] = {"UsePanicHandlers": "False", "ConfirmStop": "False"}
    # Without PermissionAsked, Dolphin opens a modal analytics question at every start.
    out["Analytics"] = {"Enabled": "False", "PermissionAsked": "True"}
    dst = guard(layout, layout.dolphin / "Config" / "Dolphin.ini")
    mine = configparser.ConfigParser(interpolation=None, strict=False)
    mine.optionxform = str
    mine.read(dst, encoding="utf-8")
    for source, keep in ((src, DOLPHIN_INI_KEEP), (mine, DOLPHIN_INI_PADS)):
        for section, keys in keep.items():
            if not source.has_section(section):
                continue
            out.setdefault(section, {})
            for key in keys if keys is not None else source[section]:
                if key in source[section]:
                    out[section][key] = source[section][key]

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        out.write(f)


def write_logger_ini(layout: Layout) -> None:
    # Dolphin fills every key left out with its default. MASTER carries panic alerts at error level,
    # OSREPORT(_HLE) the game's and RR's prints; verbosity 4 is info.
    out = configparser.ConfigParser(interpolation=None)
    out.optionxform = str
    out["Logs"] = {"MASTER": "True", "OSREPORT": "True", "OSREPORT_HLE": "True", "GDB_STUB": "True"}
    out["Options"] = {"Verbosity": "4", "WriteToConsole": "False", "WriteToFile": "True", "WriteToWindow": "False"}
    dst = guard(layout, layout.dolphin / "Config" / "Logger.ini")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as f:
        out.write(f)


# The vanilla pack's Riivolution file, from Pulsar's template (PulsarPackCreator/Resources/Base.xml): each region's DOL and
# REL hooks into Loader.pul, which loads /Binaries/Code.pul (another region's original bytes differ, so only the disc's
# own apply); the save redirected into this root.
VANILLA_XML = """<wiidisc version="1">
	<id game="RMC"/>
	<options>
		<section name="Retro Rewind">
			<option name="Pack">
				<choice name="Enabled">
					<patch id="SplitScreen8"/>
				</choice>
			</option>
		</section>
	</options>
	<patch id="SplitScreen8">
		<memory offset="0x80242698" value="4BDC1968" original="4e800020"/>
		<memory offset="0x8000A3F4" value="4bff9c0c" original="4e800020"/>
		<memory offset="0x802417dc" value="4BDC2824" original="4e800020"/>
		<memory offset="0x8000A3B4" value="4BFF9C4c" original="4e800020"/>
		<memory offset="0x802425b8" value="4BDC1A48" original="4e800020"/>
		<memory offset="0x8000A350" value="4BFF9CB0" original="4e800020"/>
		<memory valuefile="/SplitScreen8/Binaries/Loader.pul" offset="0x80004000"/>
		<savegame clone="true" external="/riivolution/save/SplitScreen8/{$__gameid}{$__region}"/>
		<folder external="/SplitScreen8/Binaries" disc="/Binaries" create="true"/>
		<file external="/SplitScreen8/UI/MenuMulti.szs" disc="/Scene/UI/MenuMulti.szs"/>
	</patch>
</wiidisc>
"""


def cmd_prepare(layout: Layout, args) -> None:
    disc = game_disc()
    if layout.vanilla:
        guard(layout, layout.xml).parent.mkdir(parents=True, exist_ok=True)
        layout.xml.write_text(VANILLA_XML, encoding="utf-8")
    else:
        print(f"[prepare] RR copy {RR_SRC} -> {layout.rr}")
        # `code` owns these two once it has run; re-copying them would put release code under its label.
        owned = frozenset({"Binaries/Code.pul", "UI/MenuMulti.szs"})
        print(f"[prepare]   {sync_tree(layout, RR_SRC, layout.rr, owned, RR_MODS)} files copied")
        for name in RR_MODS:
            if (layout.rr / name).exists():
                # A mod file copied read-only would stop rmtree on Windows.
                shutil.rmtree(guard(layout, layout.rr / name), onexc=lambda f, p, _: (os.chmod(p, 0o666), f(p)))
                print(f"[prepare]   removed {layout.rr / name}")
        copy_file(layout, RR_XML_SRC, layout.xml)

    config = layout.dolphin / "Config"
    seed = slot_root(0, layout.vanilla and args.slot != 0)
    # Seeds come from the dev roots only: a player's setup (tools/ss8_setup.py) takes Wheel Wizard's Dolphin's pads.
    dev_vanilla = layout.vanilla and layout.root == slot_root(0, True).resolve()
    if args.slot or dev_vanilla:
        # A slot's ports are slot 0's (SIDevice*, its pad files), never the main Dolphin's, whose GC port 4 is unplugged;
        # the vanilla root's slot 0 takes the 8P copy's.
        for name in ("Dolphin.ini", "GCPadNew.ini", "WiimoteNew.ini"):
            copy_file(layout, seed / "Dolphin" / "Config" / name, config / name)
    for name in DOLPHIN_CONFIG_COPIES:
        src = DOLPHIN_USER_SRC / "Config" / name
        dst = config / name
        if name in PAD_COPIES and src.is_dir():
            for s in src.rglob("*"):
                if s.is_file() and not (dst / s.relative_to(src)).exists():
                    copy_file(layout, s, dst / s.relative_to(src))
        elif name in PAD_COPIES and dst.exists():
            continue
        elif src.is_dir():
            sync_tree(layout, src, dst)
        elif src.exists():
            copy_file(layout, src, dst)
    write_dolphin_ini(layout)
    write_logger_ini(layout)
    for ini in config.rglob("*.ini"):
        # Dolphin writes Qt.ini itself: window state as escaped byte arrays, which read as UNC paths.
        if ini.name == "Qt.ini":
            continue
        if bad := paths_outside(layout, ini.read_text(encoding="utf-8", errors="replace")):
            guard(layout, ini).unlink()  # so Dolphin never loads it
            fail(f"{ini} pointed outside the 8P root and was deleted: {bad}")

    options = [{"choice": 1, "option-name": "Pack", "section-name": "Retro Rewind"}]
    if not layout.vanilla:
        options.append({"choice": 0, "option-name": "My Stuff", "section-name": "Retro Rewind"})
    descriptor = {
        "base-file": str(disc),
        "display-name": "MKW SplitScreen8" if layout.vanilla else "RR SplitScreen8",
        "riivolution": {"patches": [{
            "options": options,
            "root": str(layout.riivo),
            "xml": str(layout.xml),
        }]},
        "type": "dolphin-game-mod-descriptor",
        "version": 1,
    }
    guard(layout, layout.launch).write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
    print(f"[prepare] wrote {layout.launch}")
    save = guard(layout, layout.save)
    if dev_vanilla and not save.exists():
        # The licenses and Miis of the 8P copy's save, once: the vanilla game reads the same rksys.dat.
        copy_file(layout, Layout(seed).save, save)
        print(f"[prepare] seeded {save} from the 8P copy's save")
    if args.slot:
        # A slot runs slot 0's game state: the console NAND (RR's settings with it) and RR's save (the license).
        main = Layout(slot_root(0, layout.vanilla), layout.vanilla)
        save = Path("riivolution") / "save"
        print(f"[prepare]   {sync_tree(layout, main.dolphin / 'Wii', layout.dolphin / 'Wii')} NAND files and "
              f"{sync_tree(layout, main.riivo / save, layout.riivo / save)} save files from slot 0")
        if layout.save.parent.name != main.save.parent.name:  # a slot on another region's disc: slot 0's license there
            print(f"[prepare]   {sync_tree(layout, main.save.parent, layout.save.parent)} save files from slot 0's "
                  f"{main.save.parent.name} into {layout.save.parent.name}")


GENERATOR = ROOT / "tools" / "assets" / "gen_charselect_multi.py"


def generate(gen: Path, argv: list[str]) -> None:
    # Ours runs in process, since a frozen setup's sys.executable is the setup; a PR tree's in this Python, as RR runs it.
    if gen != GENERATOR:
        subprocess.run([sys.executable, str(gen), *argv], check=True)
        return
    spec = importlib.util.spec_from_file_location(gen.stem, gen)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.main(argv):
        fail(f"{gen.name} {' '.join(argv)} failed")


def vanilla_menu_multi(layout: Layout) -> Path:
    """The disc's own Scene/UI/MenuMulti.szs with the generator's controls, cached by the disc's and the tools' identity."""
    disc = layout.disc
    key = hashlib.sha256(f"{disc}|{disc.stat().st_size}|{disc.stat().st_mtime_ns}".encode())
    for f in [GENERATOR, *sorted((ROOT / "external" / "wuj5").glob("*.py"))]:
        key.update(sha256(f).encode())
    cached = guard(layout, layout.root / "cache" / f"MenuMulti-{key.hexdigest()[:16]}.szs")
    if not cached.exists():
        # WiiCompiled's setup extracts the whole disc into its install; without it, DolphinTool takes the one file.
        menu = layout.wc_install / "GameAssets" / "DATA" / "files" / "Scene" / "UI" / "MenuMulti.szs"
        work = guard(layout, layout.root / "cache" / "disc")
        shutil.rmtree(work, ignore_errors=True)
        cached.parent.mkdir(exist_ok=True)
        if not menu.exists():
            tool = dolphin_exe().with_name("DolphinTool.exe")
            subprocess.run([str(tool), "extract", "-i", str(disc), "-g", "-s", "Scene/UI/MenuMulti.szs", "-o", str(work), "-q"], check=True)
            menu = work / "DATA" / "files" / "Scene" / "UI" / "MenuMulti.szs"
        partial = cached.with_name(cached.stem + ".partial.szs")
        generate(GENERATOR, [str(menu), str(partial)])
        shutil.rmtree(work, ignore_errors=True)
        os.replace(partial, cached)
    return cached


def cmd_code(layout: Layout, args) -> None:
    src = args.src or CODE_PULS[args.variant]
    if not src.exists():
        fail(f"{src} does not exist; build it first (tools/build_code.py)")
    if layout.vanilla != args.variant.startswith("vanilla"):
        fail(f"{args.variant} is for the {'RR' if layout.vanilla else '--vanilla'} root")
    if not layout.xml.exists():
        fail("no copy yet; run prepare first")
    copy_file(layout, src, layout.rr / "Binaries" / "Code.pul")
    if layout.vanilla:
        copy_file(layout, src.with_name("Loader.pul"), layout.rr / "Binaries" / "Loader.pul")
        cached = vanilla_menu_multi(layout)
        print(f"[code] MenuMulti.szs from {cached.name}")
        copy_file(layout, cached, layout.rr / "UI" / "MenuMulti.szs")
        state = {"variant": args.variant, "Code.pul": sha256(layout.rr / "Binaries" / "Code.pul"),
                 "MenuMulti.szs": sha256(cached)}
        guard(layout, layout.root / "code.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        for k, v in state.items():
            print(f"[code] {k}: {v}")
        return

    menu_src = RR_SRC / "UI" / "MenuMulti.szs"
    menu_dst = guard(layout, layout.rr / "UI" / "MenuMulti.szs")
    replaced = layout.rr / "Assets" / "ReplacedAssets.szs"  # the overlay the 8P game mounts
    wuj5 = ROOT / "external" / "wuj5"
    if args.variant.startswith("ss8"):
        gen, extra = GENERATOR, []
    elif args.variant in PR_TREES:
        gen, extra = PR_TREES[args.variant] / "scripts" / "gen_charselect_multi.py", ["--wuj5", str(wuj5)]
    else:
        gen = None
    if gen is None:
        copy_file(layout, menu_src, menu_dst)
    else:
        # The generator is most of a lane's install, and its output depends only on these files.
        key = hashlib.sha256()
        for f in [gen, menu_src, replaced, *sorted(wuj5.glob("*.py"))]:
            key.update(sha256(f).encode())
        cached = guard(layout, layout.root / "cache" / f"MenuMulti-{key.hexdigest()[:16]}.szs")
        if not cached.exists():
            cached.parent.mkdir(exist_ok=True)
            partial = cached.with_name(cached.stem + ".partial.szs")
            generate(gen, [*extra, str(menu_src), str(replaced), str(partial)])
            os.replace(partial, cached)
            for old in sorted(cached.parent.glob("MenuMulti-*.szs"), key=lambda p: p.stat().st_mtime)[:-4]:
                guard(layout, old).unlink()
        print(f"[code] MenuMulti.szs from {cached.name}")
        copy_file(layout, cached, menu_dst)

    state = {"variant": args.variant, "Code.pul": sha256(layout.rr / "Binaries" / "Code.pul"),
             "MenuMulti.szs": sha256(menu_dst)}
    guard(layout, layout.root / "code.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    for k, v in state.items():
        print(f"[code] {k}: {v}")


WIT = ROOT / "build" / "wit" / "wit.exe"  # the setup's is its payload's (tools/ss8_setup.py)
# Each build's Wii image: its game ID in USB Loader GX after "RMC" and the disc's region letter, its save's title, its name.
WII_IMAGES = {False: ("S8", "RMS8", "Mario Kart Wii RR - 8 Player Splitscreen"),
              True: ("V8", "RMV8", "Mario Kart Wii - 8 Player Splitscreen")}
RIIVO_ATTRS = {"folder": {"external", "disc", "create", "recursive"}, "file": {"external", "disc", "create"},
               "memory": {"offset", "value", "valuefile", "original"}, "savegame": {"external", "clone"}}
RIIVO_TRUE = ("true", "1", "yes")  # pugixml's as_bool, which Dolphin's Riivolution parser uses
RVZ_MAGIC = b"RVZ\x01"  # wit reads ISO, WBFS, WIA, CISO and GCZ, but not Dolphin's RVZ


def wii_image(layout: Layout) -> Path:
    suffix, _, name = WII_IMAGES[layout.vanilla]
    game = f"RMC{layout.region}{suffix}"
    folder = re.sub(r'[<>:"/\\|?*]', "", name)  # USB Loader GX's wbfs/<title> [<ID>]/<ID>.wbfs, minus what Windows forbids
    return layout.root / "Wii" / "wbfs" / f"{folder} [{game}]" / f"{game}.wbfs"


def dol_offset(dol: bytearray, addr: int, size: int) -> int | None:
    offsets, addrs, sizes = (struct.unpack_from(">18I", dol, base) for base in (0, 0x48, 0x90))
    for off, start, length in zip(offsets, addrs, sizes):
        if length and start <= addr < start + length:
            if addr + size > start + length:
                fail(f"a {size}-byte write at {addr:#x} runs past main.dol's section at {start:#x}")
            return off + addr - start
    return None


def bake_riivolution(layout: Layout, work: Path) -> None:
    """The patches the copy's launch file enables, applied as Dolphin's Riivolution applies them, into an extracted disc."""
    root = ET.parse(layout.xml).getroot()
    if set(root.attrib) - {"version"} or any(set(p.attrib) - {"id"} for p in root.findall("patch")):
        fail(f"{layout.xml}: a <wiidisc> or <patch> attribute the Wii image cannot bake")
    patches = []
    for opt in json.loads(layout.launch.read_text(encoding="utf-8"))["riivolution"]["patches"][0]["options"]:
        if opt["choice"]:
            option = root.find(f"./options/section[@name='{opt['section-name']}']/option[@name='{opt['option-name']}']")
            patches += [p.get("id") for p in option.findall("choice")[opt["choice"] - 1].findall("patch")]
    files, dol_path = work / "files", work / "sys" / "main.dol"
    dol = bytearray(dol_path.read_bytes())
    entry = struct.unpack_from(">I", dol, 0xE0)[0]
    linked = missing = 0

    def place(src: Path, disc: Path, create: bool) -> None:
        nonlocal linked
        disc = guard(layout, disc)
        if not create and not disc.exists():
            return
        # The disc's files are hard links into the copy from here on, so a later write never goes through one.
        if disc.exists():
            disc.unlink()
        disc.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, disc)
        except OSError:  # a FAT32 or exFAT root has no hard links
            shutil.copy2(src, disc)
        linked += 1

    for patch_id in patches:
        for el in root.find(f"./patch[@id='{patch_id}']"):
            if el.tag not in RIIVO_ATTRS or set(el.attrib) - RIIVO_ATTRS[el.tag]:
                fail(f"{layout.xml}: <{el.tag} {el.attrib}> is not something the Wii image can bake")
            if el.tag == "savegame":
                continue  # the image's own save title stands in for the redirect
            if el.tag == "memory":
                addr = int(el.get("offset"), 16)
                data = (layout.riivo / el.get("valuefile").lstrip("/")).read_bytes() if el.get("valuefile") \
                    else bytes.fromhex(el.get("value"))
                off = dol_offset(dol, addr, len(data))
                if off is None:
                    print(f"[wii] skipped {addr:#x}: no main.dol section holds it")
                    continue
                if el.get("original") and dol[off:off + len(data)] != bytes.fromhex(el.get("original")):
                    continue  # another region's hook, as Riivolution skips it
                if addr <= entry < addr + len(data):
                    fail(f"the write at {addr:#x} covers main.dol's entry point {entry:#x}")
                dol[off:off + len(data)] = data
                print(f"[wii] main.dol {addr:#x}: {len(data)} bytes")
                continue
            # Dolphin matches a disc path with no leading / against any folder; RR's never does.
            if not el.get("external") or not (el.get("disc") or "").startswith("/"):
                fail(f"{layout.xml}: <{el.tag} {el.attrib}> needs an external and a disc path from the disc's root")
            src = layout.riivo / el.get("external").lstrip("/")
            disc = files / el.get("disc").lstrip("/")
            create = el.get("create", "").lower() in RIIVO_TRUE
            if el.tag == "file":
                if src.is_file():
                    place(src, disc, create)
                else:
                    missing += 1
            elif src.is_dir():
                walk = src.rglob("*") if el.get("recursive", "true").lower() in RIIVO_TRUE else src.glob("*")
                for s in walk:
                    if s.is_file():
                        place(s, disc / s.relative_to(src), create)
            else:
                missing += 1
    if not layout.vanilla:
        # Pulsar keeps its settings on the NAND when this word is 1 (ShouldForceNandIoSaves), as RR's own image sets it;
        # the DOL loads it through an unused text section.
        slot = struct.unpack_from(">7I", dol, 0x90).index(0)
        struct.pack_into(">I", dol, 4 * slot, len(dol))
        struct.pack_into(">I", dol, 0x48 + 4 * slot, 0x800017D8)
        struct.pack_into(">I", dol, 0x90 + 4 * slot, 4)
        dol += struct.pack(">I", 1)
        # ConfigRT.pul's header names the NAND folder Pulsar keeps those settings in, apart from RR's image's.
        conf = files / "Binaries" / "ConfigRT.pul"
        data = bytearray(conf.read_bytes())
        if data[0x14:0x22].rstrip(b"\0") != b"/RetroRewind6":
            fail(f"{conf}'s mod folder is {data[0x14:0x22]!r}, not /RetroRewind6")
        data[0x14:0x22] = b"/SS8RR".ljust(14, b"\0")
        conf.unlink()
        conf.write_bytes(data)
    dol_path.write_bytes(dol)
    print(f"[wii] {linked} files placed, {missing} of the XML's paths absent from the copy")


def cmd_wii(layout: Layout, args) -> None:
    if not (layout.root / "code.json").exists():
        fail("no SplitScreen8 Code.pul in the copy yet; run code first")
    disc = layout.disc
    suffix, title, name = WII_IMAGES[layout.vanilla]
    game = f"RMC{layout.region}{suffix}"
    cache = guard(layout, layout.root / "cache" / "wii")
    shutil.rmtree(cache, ignore_errors=True)
    cache.mkdir(parents=True)
    with disc.open("rb") as f:
        magic = f.read(4)
    if magic == RVZ_MAGIC:
        tool = dolphin_exe().with_name("DolphinTool.exe")
        subprocess.run([str(tool), "convert", "-i", str(disc), "-o", str(cache / "disc.iso"), "-f", "iso"], check=True)
        disc = cache / "disc.iso"
    # wit's --DEST collapses a double space (this root's own name has one) and writes elsewhere, so its paths are
    # relative to the cache.
    subprocess.run([str(WIT), "extract", str(disc), "--DEST", "work", "--psel", "data", "-q"], cwd=cache, check=True)
    if disc.parent == cache:
        disc.unlink()
    bake_riivolution(layout, cache / "work")
    # RR's own iso-patcher's form: its own disc, ticket and TMD IDs (its own save), the disc's own boot ID.
    subprocess.run([str(WIT), "copy", "work", "--DEST", f"image/{game}.wbfs", "--wbfs", "--split", "-q",
                    "--id", game, "--ticket-id", title, "--tmd-id", title, "--boot-id", game[:4], "--name", name],
                   cwd=cache, check=True)
    out = guard(layout, wii_image(layout)).parent
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for part in sorted((cache / "image").iterdir()):
        os.replace(part, out / part.name)
    shutil.rmtree(cache)
    print(f"[wii] wrote {out}: {', '.join(p.name for p in sorted(out.iterdir()))}")


# PAD::PAD_BUTTON_* (core/rvl/PAD.hpp), by the names a menu script uses.
PAD_BUTTONS = {"left": 0x1, "right": 0x2, "down": 0x4, "up": 0x8, "z": 0x10, "r": 0x20, "l": 0x40,
               "a": 0x100, "b": 0x200, "x": 0x400, "y": 0x800, "start": 0x1000}
# WPAD_CL_BUTTON_* (the RVL SDK's Classic Controller bits, as WiiController::UpdateStatesClassic tests
# them), for ports 5-8; "start" is the plus button.
CL_BUTTONS = {"up": 0x1, "left": 0x2, "zr": 0x4, "x": 0x8, "a": 0x10, "y": 0x20, "b": 0x40, "zl": 0x80,
              "r": 0x200, "plus": 0x400, "start": 0x400, "home": 0x800, "minus": 0x1000, "l": 0x2000,
              "down": 0x4000, "right": 0x8000}
# MenuScript.cpp's MenuScript: its array sizes and flag bits.
SCRIPT_STEPS, SCRIPT_PICKS, SCRIPT_SETTINGS = 1024, 4, 8
SCRIPT_OPEN_MII_C, SCRIPT_AUTODRIVE = 1, 2


def rr_enums() -> tuple[dict[str, int], dict[str, int]]:
    """RR's SettingsParam.hpp by enumerator name: SettingId's explicit values, then every other enum's (the values a
    setting takes), counted from 0 where not given."""
    text = (ROOT / "external" / "rr-pulsar" / "PulsarEngine" / "Settings" / "SettingsParam.hpp").read_text()
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    ids, values = {}, {}
    for enum, body in re.findall(r"\benum\s+(\w+)\s*\{(.*?)\}", text, re.S):
        value = -1
        for item in filter(None, (i.strip() for i in body.split(","))):
            name, _, expr = item.partition("=")
            value = int(expr, 0) if expr else value + 1
            (ids if enum == "SettingId" else values)[name.strip()] = value
    return ids, values


def menu_script(path: Path, region: str = "P") -> dict:
    """A menu script (tools/menu_scripts/) as the presses, picks, settings and flags MenuScript.cpp reads, and the
    frame its clock ends on. Lines, after '#' comments: "wait FRAMES" moves the clock; "press PORT
    BUTTON[+BUTTON...] [HOLD]" presses on GC port 1-4, or on the Classic Controller of Wii channel 1-4 as port 5-8,
    for HOLD frames (default 5) and moves the clock past the hold. "unplug PORT FRAMES" makes that port read as no
    controller for FRAMES frames (a step with no buttons), then plugs it back; the clock stays, so other ports can
    press meanwhile. Frames are 1/60 s. "pick PLAYER CHARACTER KART" (player 5-8, the game's ids) puts that player's
    pick in SplitScreen8's extPicks at every section load of a 5-8 player game. "open-mii-c" opens Mii C on the
    8-player character page whatever the license's VR (the 8P copy's is under 300). "mash PORT BUTTON[+BUTTON]
    FRAMES" presses for 5 frames once a second for FRAMES frames and moves the clock past them. "autodrive" gives
    every local's kart to the AI a second into each race, so a lane reaches the results. "setting ID VALUE" sets an
    RR setting at each section load (RR's names: "setting SETTING_KOENABLED KOSETTING_ENABLED"); RR saves its
    settings whole, so the value reaches the copy's RRGameSettings.pul at its next save. A line led by region letters
    and a colon ("EJ: press 1 a") runs only on those discs (REGIONS' P, E, J), "P" when none is given."""
    steps, picks, settings, frame, flags, enums = [], [], [], 0, 0, None
    for n, line in enumerate(path.read_text().splitlines(), 1):
        words = line.split("#", 1)[0].split()
        if words and words[0].endswith(":") and set(words[0][:-1]) <= set(REGIONS.values()):
            if region not in words[0]:
                continue
            words = words[1:]
        if not words:
            continue
        try:
            if words[0] == "wait" and len(words) == 2:
                frame += int(words[1])
                continue
            if (words[0], len(words)) in (("press", 3), ("press", 4), ("mash", 4)):
                port = int(words[1])
                names = CL_BUTTONS if port > 4 else PAD_BUTTONS
                buttons = 0
                for name in words[2].lower().split("+"):
                    buttons |= names[name]
                frames = int(words[3]) if len(words) == 4 else 5
                if not 1 <= port <= 8 or frames < 1:
                    raise ValueError
                presses = [(0, frames)] if words[0] == "press" else [(t, 5) for t in range(0, frames, 60)]
                steps += [(frame + t, port - 1, buttons, hold) for t, hold in presses]
                frame += frames
                continue
            if words[0] == "unplug" and len(words) == 3:
                port, frames = int(words[1]), int(words[2])
                if not 1 <= port <= 8 or frames < 1:
                    raise ValueError
                steps.append((frame, port - 1, 0, frames))
                continue
            if words == ["open-mii-c"]:
                flags |= SCRIPT_OPEN_MII_C
                continue
            if words == ["autodrive"]:
                flags |= SCRIPT_AUTODRIVE
                continue
            if words[0] == "setting" and len(words) == 3:
                ids, values = enums = enums or rr_enums()
                settings.append((ids[words[1]], values[words[2]]))
                continue
            if words[0] == "pick" and len(words) == 4:
                player, character, kart = (int(w, 0) for w in words[1:])
                if not 5 <= player <= 8:
                    raise ValueError
                picks.append((player - 5, character, kart))
                continue
        except (ValueError, KeyError):
            pass
        sys.exit(f"{path}:{n}: expected 'wait FRAMES', 'press PORT BUTTON[+BUTTON] [HOLD]', 'unplug PORT FRAMES', "
                 f"'mash PORT BUTTON FRAMES', 'pick PLAYER CHARACTER KART', 'open-mii-c', 'autodrive' or "
                 f"'setting ID VALUE' (RR's names): {line!r}")
    if not steps:
        sys.exit(f"{path}: no press lines")
    if len(steps) > SCRIPT_STEPS or len(picks) > SCRIPT_PICKS or len(settings) > SCRIPT_SETTINGS:
        sys.exit(f"{path}: over MenuScript.cpp's {SCRIPT_STEPS} steps, {SCRIPT_PICKS} picks or {SCRIPT_SETTINGS} settings")
    return {"steps": steps, "picks": picks, "settings": settings, "flags": flags, "end": frame}


def cmd_script(layout: Layout, args) -> None:
    s = menu_script(args.file, layout.region)
    data = struct.pack(">4I", len(s["steps"]), len(s["picks"]), len(s["settings"]), s["flags"])
    for rows, size, width in ((s["steps"], SCRIPT_STEPS, 4), (s["picks"], SCRIPT_PICKS, 3), (s["settings"], SCRIPT_SETTINGS, 2)):
        data += b"".join(struct.pack(f">{width}I", *row) for row in rows) + bytes(4 * width * (size - len(rows)))
    # The vanilla build has no Pulsar IO: its script is a disc file in the pack's /Binaries.
    dst = guard(layout, (layout.rr / "Binaries" if layout.vanilla else layout.nand_mod) / "ss8-menu.bin")
    dsts = [dst]
    # WiiCompiled's Pulsar IO reads its own NAND's mod folder.
    if not layout.vanilla and (layout.wc_userdata / "NAND").is_dir():
        dsts.append(guard(layout, layout.wc_userdata / "NAND" / layout.nand_mod.relative_to(layout.dolphin / "Wii") / "ss8-menu.bin"))
    for dst in dsts:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    print(f"[script] {args.file.name}: {len(s['steps'])} steps, end frame {s['end']} -> {', '.join(map(str, dsts))}")


def _win(dll, name: str, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = restype, argtypes
    return fn


# Own WinDLL instances, so these prototypes never leak into ctypes.windll's shared ones. Handles
# need explicit restypes: the default c_int truncates a 64-bit HANDLE.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi = ctypes.WinDLL("gdi32", use_last_error=True)
OpenProcess = _win(_k32, "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
GetExitCodeProcess = _win(_k32, "GetExitCodeProcess", wintypes.BOOL, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
QueryFullProcessImageNameW = _win(_k32, "QueryFullProcessImageNameW", wintypes.BOOL, wintypes.HANDLE,
                                  wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
CloseHandle = _win(_k32, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)
GetProcessTimes = _win(_k32, "GetProcessTimes", wintypes.BOOL, wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EnumWindows = _win(_u32, "EnumWindows", wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
IsWindowVisible = _win(_u32, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
GetWindowThreadProcessId = _win(_u32, "GetWindowThreadProcessId", wintypes.DWORD, wintypes.HWND,
                                ctypes.POINTER(wintypes.DWORD))
GetClientRect = _win(_u32, "GetClientRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
ClientToScreen = _win(_u32, "ClientToScreen", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
GetWindowTextLengthW = _win(_u32, "GetWindowTextLengthW", ctypes.c_int, wintypes.HWND)
GetWindowTextW = _win(_u32, "GetWindowTextW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
PostMessageW = _win(_u32, "PostMessageW", wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
SendMessageTimeoutW = _win(_u32, "SendMessageTimeoutW", wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                           wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
EnumChildWindows = _win(_u32, "EnumChildWindows", wintypes.BOOL, wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
GetClassNameW = _win(_u32, "GetClassNameW", ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
IsWindow = _win(_u32, "IsWindow", wintypes.BOOL, wintypes.HWND)
GetDC = _win(_u32, "GetDC", wintypes.HDC, wintypes.HWND)
ReleaseDC = _win(_u32, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
PrintWindow = _win(_u32, "PrintWindow", wintypes.BOOL, wintypes.HWND, wintypes.HDC, wintypes.UINT)
CreateCompatibleDC = _win(_gdi, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
DeleteDC = _win(_gdi, "DeleteDC", wintypes.BOOL, wintypes.HDC)
SelectObject = _win(_gdi, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
DeleteObject = _win(_gdi, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
GdiFlush = _win(_gdi, "GdiFlush", wintypes.BOOL)
BitBlt = _win(_gdi, "BitBlt", wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
              wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
GetWindow = _win(_u32, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT)
GetWindowRect = _win(_u32, "GetWindowRect", wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
GetWindowLongW = _win(_u32, "GetWindowLongW", wintypes.LONG, wintypes.HWND, ctypes.c_int)
IsIconic = _win(_u32, "IsIconic", wintypes.BOOL, wintypes.HWND)
GetSystemMetrics = _win(_u32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
SetWindowPos = _win(_u32, "SetWindowPos", wintypes.BOOL, wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT)
DwmGetWindowAttribute = _win(ctypes.WinDLL("dwmapi", use_last_error=True), "DwmGetWindowAttribute", ctypes.c_long,
                             wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020
GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SWP_NOZORDER, SWP_NOACTIVATE = 0x4, 0x10
TILES = 4  # slot windows side by side on the primary display
WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
SMTO_ABORTIFHUNG = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


CreateDIBSection = _win(_gdi, "CreateDIBSection", wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(BITMAPINFO),
                        wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)


def process_created(pid: int) -> int | None:
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not GetProcessTimes(h, *map(ctypes.byref, times)):
            return None
        return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    finally:
        CloseHandle(h)


def dolphin_alive(pid: int, created: int) -> bool:
    """True only for the very process `dolphin` started: the owner's own Dolphin is the same exe,
    and Windows reuses pids, so the creation time is what tells them apart."""
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not GetExitCodeProcess(h, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return False
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return False
        return Path(buf.value).name.lower() == "dolphin.exe" and process_created(pid) == created
    finally:
        CloseHandle(h)


def recorded_pid(layout: Layout) -> tuple[int, int] | None:
    """(pid, creation time) from dolphin.pid."""
    try:
        pid, created = layout.pid_file.read_text(encoding="utf-8").split()
        return int(pid), int(created)
    except (FileNotFoundError, ValueError):
        return None


def unattended_ini(layout: Layout, name: str, marker_name: str, active: bool, values: dict) -> None:
    """Puts the owner's 8P pad config back from pads/ if a marker says an earlier launch changed it,
    else saves the copy's file there as the owner's; with `active`, writes `values` over it for this
    launch and leaves the marker."""
    path = layout.dolphin / "Config" / name
    saved = layout.root / "pads" / name
    marker = layout.root / marker_name
    if marker.exists():
        # The main Dolphin's file is never a fallback: it is a different mapping (D45).
        if not saved.exists():
            fail(f"{marker} says {path} holds an unattended override, but {saved} is missing; "
                 f"put the owner's 8P {name} back, then delete the marker")
        copy_file(layout, saved, path)
        guard(layout, marker).unlink()
    elif path.exists():
        copy_file(layout, path, saved)
    if not active:
        return
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read(path, encoding="utf-8")
    for section, keys in values.items():
        ini.setdefault(section, {})
        ini[section].update(keys)
    # The marker goes first: a launch killed mid-write must restore, never save the override as the owner's.
    guard(layout, marker).write_text(f"{name} has {sorted(values)} set by an unattended dolphin launch\n", encoding="utf-8")
    with guard(layout, path).open("w", encoding="utf-8") as f:
        ini.write(f)


def cmd_dolphin(layout: Layout, args) -> None:
    rec = recorded_pid(layout)
    if rec is not None and dolphin_alive(*rec):
        fail(f"Dolphin is already running, pid {rec[0]}; install_8p.py stop")
    if args.wiimotes and not args.unattended:
        fail("--wiimotes is for unattended launches; the owner's launches keep the owner's Wii Remote mapping")
    if args.fast and not args.unattended:
        fail("--fast is for unattended lanes; the owner plays at the game's own speed")
    if args.plain and not layout.vanilla:
        fail("--plain launches the plain disc from the --vanilla root")
    # Each run's log is kept under its last-write time, so a relaunch never loses the owner's
    # session; the newest KEEP_LOGS stay (one 8-view session logged 43 MB).
    if layout.dolphin_log.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(layout.dolphin_log.stat().st_mtime))
        try:
            os.replace(guard(layout, layout.dolphin_log), guard(layout, layout.dolphin_log.with_name(f"dolphin.{stamp}.log")))
        except OSError as e:
            fail(f"cannot move {layout.dolphin_log} aside (is another Dolphin using this user dir?): {e}")
        for old in sorted(layout.dolphin_log.parent.glob("dolphin.*-*.log"))[:-KEEP_LOGS]:
            guard(layout, old).unlink()

    # Dolphin reports a GC port as unplugged while its mapped host pad is absent, and the race then
    # waits on "controller interrupted". An unattended launch keeps ports 1-4 plugged in, one per
    # local player a debug boot can race, and --wiimotes connects Wii Remotes 1-4 with a Classic
    # Controller for players 5-8 (D24); the launch after it puts the owner's 8P mapping back from
    # pads/; a launch with no marker saves the mapping there first.
    unattended_ini(layout, "GCPadNew.ini", "pads.unattended", args.unattended,
                   {f"GCPad{n}": {"Options/Always Connected": "True"} for n in range(1, 5)})
    unattended_ini(layout, "WiimoteNew.ini", "wiimotes.unattended", args.wiimotes,
                   {f"Wiimote{n}": {"Source": "1", "Extension": "Classic"} for n in range(1, 5)})

    exe = dolphin_exe()
    cmd = [str(exe), "-u", str(layout.dolphin)]
    # The JIT checks breakpoints only in debug mode: without -d a GDB breakpoint never fires.
    if args.debugger or args.gdb is not None:
        cmd.append("-d")
    # -C overrides the setting for this launch only; nothing is written to Dolphin.ini.
    if args.gdb is not None:
        cmd += ["-C", f"Dolphin.General.GDBPort={args.gdb}"]
    if args.pause_on_panic:
        cmd += ["-C", "Dolphin.Core.PauseOnPanic=True"]
    # Unattended runs render at native resolution with no anisotropy, so 8 views keep a smoke case
    # inside its timeouts; the owner's launches keep the owner's GFX.ini (owner, 2026-10-05).
    if args.unattended:
        cmd += ["-C", f"GFX.Settings.InternalResolution={args.res}", "-C", "GFX.Enhancements.MaxAnisotropy=0"]
    # D114: a menu script counts game frames, so unlimited speed changes no lane's timing. Not for a lane that
    # checks sound or shoots at a wall-clock time.
    if args.fast:
        cmd += ["-C", "Dolphin.Core.EmulationSpeed=0"]
    # A raw video and audio record of the launch for a PR's evidence: Dolphin writes Dump/Frames/*.avi and
    # Dump/Audio/*.wav from boot, at real speed only (never with --fast: the sound would run fast too).
    if args.dump:
        if args.fast:
            fail("--dump records at real speed; drop --fast")
        cmd += ["-C", "Dolphin.Movie.DumpFrames=True", "-C", "Dolphin.Movie.DumpFramesSilent=True",
                "-C", "Dolphin.DSP.DumpAudio=True", "-C", "Dolphin.DSP.DumpAudioSilent=True"]
        # GFX.ini's -C system is Graphics. Footage: the raw frame at the internal resolution (2432x1824 at --res 4 in
        # 60 Hz, 4:3 pixels to stretch to 16:9), lossless (Ut Video), with 16x anisotropy.
        if args.footage:
            cmd += ["-C", f"Graphics.Settings.InternalResolution={args.res}", "-C", "Graphics.Settings.FrameDumpsResolutionType=2",
                    "-C", "Graphics.Settings.UseLossless=True", "-C", "Graphics.Enhancements.MaxAnisotropy=4"]
    elif args.footage:
        fail("--footage sets --dump's frame and codec; add --dump")
    cmd += ["-m", str(args.movie)] if args.movie else []
    if args.osd:
        cmd += ["-C", "Dolphin.General.ShowFrameCount=True", "-C", "Dolphin.Movie.ShowInputDisplay=True"]
    cmd += ["-e", str(layout.disc if args.plain else wii_image(layout) if args.image else layout.launch)]
    proc = subprocess.Popen(cmd)
    created = process_created(proc.pid)
    if created is None:
        fail(f"cannot read the creation time of pid {proc.pid}; nothing recorded, so stop/shot will not touch it")
    guard(layout, layout.pid_file).write_text(f"{proc.pid} {created}\n", encoding="utf-8")
    print(f"[dolphin] pid {proc.pid}: {subprocess.list2cmdline(cmd)}")


def cmd_stop(layout: Layout, args) -> None:
    rec = recorded_pid(layout)
    if rec is None:
        print(f"[stop] no pid recorded in {layout.pid_file}")
        return
    pid = rec[0]
    if not dolphin_alive(*rec):
        print(f"[stop] pid {pid} is not the Dolphin `dolphin` started; removing {layout.pid_file}")
        guard(layout, layout.pid_file).unlink()
        return

    # WM_CLOSE does not close Dolphin, so the plain taskkill is tried only with --timeout.
    how = "closed"
    if args.timeout > 0:
        subprocess.run(["taskkill", "/PID", str(pid)])
        deadline = time.monotonic() + args.timeout
        while dolphin_alive(*rec) and time.monotonic() < deadline:
            time.sleep(0.25)
    if dolphin_alive(*rec):
        subprocess.run(["taskkill", "/F", "/PID", str(pid)])
        how = f"forced after {args.timeout}s" if args.timeout > 0 else "forced"
        deadline = time.monotonic() + 5
        while dolphin_alive(*rec) and time.monotonic() < deadline:
            time.sleep(0.25)
        if dolphin_alive(*rec):
            fail(f"pid {pid} is still alive after taskkill /F; {layout.pid_file} kept")
    guard(layout, layout.pid_file).unlink()
    print(f"[stop] Dolphin pid {pid} {how}")


def process_windows(pid: int) -> list[int]:
    found = []

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and IsWindowVisible(hwnd):
            rc = wintypes.RECT()
            if GetClientRect(hwnd, ctypes.byref(rc)) and rc.right > rc.left and rc.bottom > rc.top:
                found.append(hwnd)
        return True

    EnumWindows(WNDENUMPROC(visit), 0)
    return found


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(GetWindowTextLengthW(hwnd) + 1)
    GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def all_black(bgra: bytes) -> bool:
    return not any(bgra[c::4].strip(b"\0") for c in range(3))


def frame_rect(hwnd: int) -> wintypes.RECT:
    # The visible frame: GetWindowRect also counts the invisible resize borders, which overlap neighbours.
    rc = wintypes.RECT()
    if DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rc), ctypes.sizeof(rc)) != 0:
        GetWindowRect(hwnd, ctypes.byref(rc))
    return rc


def screen_copy_blocker(hwnd: int, x: int, y: int, w: int, h: int) -> str | None:
    """Why a copy of the screen at the client area would not show this window, or None if it would."""
    if IsIconic(hwnd):
        return "minimised"
    vx, vy = GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if x < vx or y < vy or x + w > vx + vw or y + h > vy + vh:
        return "partly off screen"
    above = GetWindow(hwnd, GW_HWNDPREV)
    while above:
        cloaked = wintypes.DWORD()
        DwmGetWindowAttribute(above, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        ex = GetWindowLongW(above, GWL_EXSTYLE)
        # A click-through layered window is an overlay that draws nothing over other windows' content.
        overlay = ex & WS_EX_LAYERED and ex & WS_EX_TRANSPARENT
        if IsWindowVisible(above) and not cloaked.value and not overlay and not IsIconic(above):
            rc = frame_rect(above)
            if rc.left < x + w and x < rc.right and rc.top < y + h and y < rc.bottom:
                return f"covered by {window_title(above)!a}"
        above = GetWindow(above, GW_HWNDPREV)
    return None


def capture_window(hwnd: int) -> tuple[int, int, bytes | None, str]:
    """Returns width, height, top-down BGRA pixels and the method that produced them. The pixels are
    None, and the method says why, when the window could not be captured."""
    # The client area only: the black test must see the render surface, not the title bar.
    rc = wintypes.RECT()
    GetClientRect(hwnd, ctypes.byref(rc))
    w, h = rc.right, rc.bottom
    origin = wintypes.POINT(0, 0)
    ClientToScreen(hwnd, ctypes.byref(origin))
    screen = GetDC(None)
    mem = CreateCompatibleDC(screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h  # negative height: top-down rows
    bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    bmp = CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not bmp:
        DeleteDC(mem)
        ReleaseDC(None, screen)
        fail(f"CreateDIBSection {w}x{h} failed (error {ctypes.get_last_error()})")
    old = SelectObject(mem, bmp)
    try:
        # A Vulkan or D3D swapchain can come back black from PrintWindow, as can a scene change's black
        # frame, so it is tried again before the screen copy, which shows whatever is on top of the window.
        for attempt in range(3):
            if attempt:
                time.sleep(0.1)
            PrintWindow(hwnd, mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
            GdiFlush()
            pixels, method = ctypes.string_at(bits, w * h * 4), "printwindow"
            if not all_black(pixels):
                break
        else:
            blocker = screen_copy_blocker(hwnd, origin.x, origin.y, w, h)
            if blocker is not None:
                pixels, method = None, f"PrintWindow black, and the screen copy would not show it: {blocker}"
            else:
                BitBlt(mem, 0, 0, w, h, screen, origin.x, origin.y, SRCCOPY)
                GdiFlush()
                pixels, method = ctypes.string_at(bits, w * h * 4), "screen"
    finally:
        SelectObject(mem, old)
        DeleteObject(bmp)
        DeleteDC(mem)
        ReleaseDC(None, screen)
    return w, h, pixels, method


def png_bytes(w: int, h: int, rgb: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    stride = w * 3
    rows = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def dpi_aware() -> None:
    # Window rects in physical pixels, so a scaled display neither crops a capture nor misplaces a window.
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (OSError, AttributeError):
        ctypes.WinDLL("user32").SetProcessDPIAware()


def place_windows(pid: int, tile: int) -> None:
    """Puts Dolphin's render window in column `tile` of the primary display's top half and its main window below
    it, so parallel slots never cover each other's render window, which a shot's screen copy needs."""
    dpi_aware()
    w, h = GetSystemMetrics(SM_CXSCREEN) // TILES, GetSystemMetrics(SM_CYSCREEN) // 2
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        windows = process_windows(pid)
        # The render window's title carries the core and backend ("Dolphin 2609a | JIT64 SC | Vulkan | ...").
        render = [hwnd for hwnd in windows if " | " in window_title(hwnd)]
        if render:
            break
        time.sleep(0.5)
    else:
        print(f"[dolphin] no render window within 60 s; windows left where Dolphin put them")
        return
    for hwnd in windows:
        SetWindowPos(hwnd, None, tile * w, 0 if hwnd in render else h, w, h, SWP_NOZORDER | SWP_NOACTIVATE)


def save_shots(layout: Layout, pid: int, out: Path, stamp: str, windows: list[int] | None = None) -> tuple[int, int]:
    """PNG of each visible window of pid (or of windows) into out; returns how many were written and how many refused.
    A window that cannot be captured gets a "[shot] refused" line with its title and no file, never a
    copy of something else."""
    dpi_aware()
    out = guard(layout, out)
    out.mkdir(parents=True, exist_ok=True)
    written = refused = 0
    for n, hwnd in enumerate(windows or process_windows(pid), 1):
        w, h, bgra, method = capture_window(hwnd)
        if bgra is None:
            print(f"[shot] refused window {n}  {w}x{h}  {method}  {window_title(hwnd)!a}")
            refused += 1
            continue
        rgb = bytearray(w * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        path = out / f"{stamp}-{n}.png"
        path.write_bytes(png_bytes(w, h, bytes(rgb)))
        print(f"[shot] {path}  {w}x{h}  {method}  {window_title(hwnd)!a}")
        written += 1
    return written, refused


def cmd_shot(layout: Layout, args) -> None:
    rec = recorded_pid(layout)
    if rec is None or not dolphin_alive(*rec):
        fail(f"no running Dolphin recorded in {layout.pid_file}; start one with install_8p.py dolphin")
    out = args.out if args.out is not None else layout.root / "shots"
    written, refused = save_shots(layout, rec[0], out, time.strftime("%Y%m%d-%H%M%S"))
    if refused:
        fail(f"{refused} window(s) of Dolphin pid {rec[0]} refused; uncover the render window and shoot again")
    if not written:
        fail(f"Dolphin pid {rec[0]} has no visible window with a non-zero client area")


class LogTail:
    """dolphin.log as it grows, read from where the last read stopped. The text matches read_text's, newlines
    translated. A launch moves the old log aside: make one after it."""

    def __init__(self, path: Path):
        self.path, self.offset, self.text = path, 0, ""
        self.decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")(errors="replace"), translate=True)

    def read(self) -> str:
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                new = f.read()
        except OSError:
            return self.text
        self.offset += len(new)
        self.text += self.decoder.decode(new)
        return self.text


def dolphin_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Dolphin.exe", "/FO", "CSV", "/NH"], capture_output=True,
                         text=True).stdout
    return {int(m) for m in re.findall(r'^"Dolphin\.exe","(\d+)"', out, re.M | re.I)}


# The Dolphins this process launched (lane.py's and smoke's slot threads), and the lock that makes "no other Dolphin
# runs, then launch and record it" one step across them.
launched_here: set[int] = set()
launching = threading.Lock()


def _foreign() -> str | None:
    others = dolphin_pids() - launched_here
    return f"refused: a Dolphin this run did not start is running (pid {min(others)})" if others else None


def foreign_dolphin() -> str | None:
    """Why nothing may be installed or launched now: a Dolphin this process did not start runs (the owner's own, or
    their 8P copy's), or None. Under the launch lock, so another slot's launch, running but not yet recorded, never
    counts."""
    with launching:
        return _foreign()


def guarded_launch(layout: Layout, launch) -> tuple[int, int] | str:
    """Calls launch(), which starts this layout's Dolphin and returns True on success, only while foreign_dolphin()
    is None. Returns the (pid, creation time) the launch recorded, or why there is none."""
    with launching:
        if why := _foreign():
            return why
        before = recorded_pid(layout)
        # Only a pid this launch recorded is the caller's Dolphin, to stop and to restore anything under.
        if not launch() or (rec := recorded_pid(layout)) is None or rec == before:
            return "launch failed"
        launched_here.add(rec[0])
        return rec


def lock_slot(layout: Layout) -> Path:
    """lane.lock in a slot's root, holding this process's pid, so two runs never share a slot. A lock whose process
    has exited is taken over."""
    lock = guard(layout, layout.root / "lane.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = lock.read_text().strip()
        if holder.isdigit() and process_created(int(holder)) is not None:
            fail(f"{layout.root} is in use by pid {holder} (lane.py or smoke_8p.py)")
        lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def dolphin_running(layout) -> bool:
    rec = recorded_pid(layout)
    return rec is not None and dolphin_alive(*rec)


def wait_for(layout, log: LogTail, pattern: re.Pattern, after: int, timeout: float,
             give_up: float = 60) -> tuple[int | None, str]:
    """(offset in the log of the first match at or after `after`, ""), or (None, why not): Dolphin exited, invalid
    accesses and then nothing for `give_up` s (a crash), or nothing within `timeout` s."""
    deadline = time.monotonic() + timeout
    first_invalid = None
    scanned = after  # each poll searches only from the last complete line on
    while time.monotonic() < deadline:
        text = log.read()
        m = pattern.search(text, scanned)
        if m:
            return m.end(), ""
        if not dolphin_running(layout):
            return None, "Dolphin exited"
        if first_invalid is None and INVALID.search(text, scanned):
            first_invalid = time.monotonic()
        scanned = max(scanned, text.rfind("\n") + 1)
        if first_invalid is not None and time.monotonic() - first_invalid > give_up:
            return None, f"invalid accesses, then nothing for {give_up}s"
        time.sleep(2)
    return None, f"nothing within {timeout:.0f}s"


def cmd_log(layout: Layout, args) -> None:
    if not layout.dolphin_log.exists():
        fail(f"{layout.dolphin_log} does not exist (prepare writes Logger.ini; Dolphin creates the log)")
    lines = layout.dolphin_log.read_text(encoding="utf-8", errors="replace").splitlines()
    if args.grep:
        rx = re.compile(args.grep)
        lines = [line for line in lines if rx.search(line)]
    print("\n".join(lines[-args.tail:] if args.tail > 0 else lines))


def shortcut_paths() -> list[Path]:
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
    return [Path(winreg.QueryValueEx(key, name)[0]) / SHORTCUT_NAME for name in ("Desktop", "Programs")]


def print_log_tail(name: str) -> None:
    log = Path(os.environ["TEMP"]) / f"WiiCompiled-{name}.log"
    if not log.exists():
        return
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"--- {log} (last 40 lines) ---")
    print("\n".join(lines[-40:]))
    unsupported = [l for l in lines if "nsupported" in l]
    print(f"--- {len(unsupported)} line(s) mentioning Unsupported ---")
    print("\n".join(unsupported[:40]))


def run_setup_exe(exe: Path, argv: list[str], log_name: str) -> int:
    print(f"[wiicompiled] {exe} {' '.join(argv)}")
    rc = subprocess.run([str(exe), *argv]).returncode
    print(f"[wiicompiled] exit {rc}")
    if rc != 0:
        print_log_tail(log_name)
    return rc


# A first install rewrites the shared desktop / Start menu shortcuts to point at the new install
# (ShellIntegration.CreateShortcuts). These save the owner's and put them back byte for byte; the two
# shortcut paths are the only writes outside the root.
def backup_shortcuts(layout: Layout) -> None:
    manifest = layout.backup / "shortcuts.json"
    if manifest.exists():
        fail(f"{manifest} exists: a setup did not restore the owner's shortcuts; run wiicompiled restore-shortcuts")
    existed = {}
    for i, lnk in enumerate(shortcut_paths()):
        existed[str(lnk)] = lnk.exists()
        if lnk.exists():
            copy_file(layout, lnk, layout.backup / f"shortcut{i}.lnk")
    guard(layout, manifest).write_text(json.dumps(existed, indent=2), encoding="utf-8")


def restore_shortcuts(layout: Layout) -> None:
    manifest = layout.backup / "shortcuts.json"
    if not manifest.exists():
        fail("no shortcut backup to restore")
    errors = []
    allowed = set(shortcut_paths())
    for i, (lnk, existed) in enumerate(json.loads(manifest.read_text(encoding="utf-8")).items()):
        lnk = Path(lnk)
        if lnk not in allowed:
            errors.append(f"{lnk}: not one of the two shared shortcut paths; left alone")
            continue
        try:
            if existed:
                shutil.copy2(layout.backup / f"shortcut{i}.lnk", lnk)
            elif lnk.exists():
                lnk.unlink()
        except OSError as e:
            errors.append(f"{lnk}: {e}")
    if errors:
        fail("could not restore the owner's shortcuts (backup kept in backup/): " + "; ".join(errors))
    guard(layout, manifest).unlink()
    print("[wiicompiled] owner's shortcuts restored")


WC_PAYLOAD_FLAGS = ["--skip-retro-wfc-payload"]


def setup_installer(args) -> Path:
    """--installer, or the newest full installer in Wheel Wizard's cache (read only)."""
    if args.installer is not None:
        if not args.installer.is_file():
            fail(f"--installer {args.installer} is not a file")
        return args.installer.resolve()
    installers = sorted(SETUP_CACHE.glob("WiiCompiled-Setup-*.exe"), key=lambda p: p.stat().st_mtime)
    if not installers:
        fail(f"no WiiCompiled-Setup-*.exe in {SETUP_CACHE}; pass --installer")
    return installers[-1]


def install_wiicompiled(layout: Layout, installer: Path) -> int:
    backup_shortcuts(layout)
    try:
        return run_setup_exe(installer, ["--silent", "--portable", "--install-dir", str(layout.wc_install),
                                         "--game", str(layout.disc), "--retro-dir", str(layout.rr),
                                         *WC_PAYLOAD_FLAGS, "--progress-json"], "setup")
    finally:
        restore_shortcuts(layout)


def repair_wiicompiled(layout: Layout) -> int:
    return run_setup_exe(layout.wc_install / "WiiCompiled-Setup.exe",
                         ["--repair-products", "--install-dir", str(layout.wc_install), "--retro-dir", str(layout.rr),
                          *WC_PAYLOAD_FLAGS, "--progress-json"], "repair")


def installed_wc_version(layout: Layout) -> str:
    state = json.loads((layout.wc_install / "install-state.json").read_text(encoding="utf-8"))
    return state.get("ProductVersion") or "unknown"


# The portable root is Install's parent, so UserData (pads, NAND, logs) stays put; its small files are
# copied to backup/ first in case the new setup rewrites them. The old Install is kept beside the new one.
# Any failure after the rename (a nonzero setup, an exception, Ctrl+C) puts both back; by hand: delete
# Install, rename Install-v<old> back, and copy backup/wc-userdata-<stamp> over UserData.
def upgrade_wiicompiled(layout: Layout, installer: Path) -> int:
    old = installed_wc_version(layout)
    keep = guard(layout, layout.wc_install.with_name(f"Install-v{old}"))
    if keep.exists():
        fail(f"{keep} exists: a previous upgrade's rollback copy; move it away first")
    if (layout.backup / "shortcuts.json").exists():
        fail(f"{layout.backup / 'shortcuts.json'} exists: run wiicompiled restore-shortcuts first")
    layout.disc  # fails here, before anything moves, rather than inside setup
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = guard(layout, layout.backup / f"wc-userdata-{stamp}")
    for f in layout.wc_userdata.iterdir():
        if f.is_file():
            copy_file(layout, f, saved / f.name)
    print(f"[wiicompiled] UserData files copied to {saved}")
    try:
        guard(layout, layout.wc_install).rename(keep)
    except OSError as e:
        fail(f"could not rename {layout.wc_install} (is RetroRewind.exe running from it?): {e}")
    print(f"[wiicompiled] v{old} kept at {keep}")

    def roll_back(why: str) -> None:
        if layout.wc_install.exists():
            shutil.rmtree(guard(layout, layout.wc_install))
        keep.rename(guard(layout, layout.wc_install))
        for f in saved.iterdir():
            copy_file(layout, f, layout.wc_userdata / f.name)
        print(f"[wiicompiled] {why}; v{old} and its UserData files restored to {layout.wc_install.parent}")

    try:
        rc = install_wiicompiled(layout, installer)
    except BaseException:
        roll_back("setup did not finish")
        raise
    if rc != 0:
        roll_back("setup failed")
        return rc
    print(f"[wiicompiled] v{old} -> v{installed_wc_version(layout)}")
    return 0


WC_SOURCE = ROOT / "external" / "Wiicompiled"
WC_SOURCE_TREES = ("runtime", "aurora-main")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_wc(*argv: str) -> bytes:
    result = subprocess.run(["git", "-C", str(WC_SOURCE), *argv], capture_output=True)
    if result.returncode != 0:
        fail(f"git {' '.join(argv)}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


# The install records no commit; the folder of the installer it came from, Installers/main-<commit>, names it.
def wc_base(layout: Layout, base: str | None) -> str:
    if base is None:
        found = sorted((layout.wc_install.parent / "Installers").glob("main-*"))
        if len(found) != 1:
            fail(f"{len(found)} Installers/main-* folders; say which commit the install is with --base")
        base = found[0].name[len("main-"):]
    return git_wc("rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()


# The installed text sources are CRLF, the repository's blobs LF; a binary file is the same bytes in both.
def installed_form(data: bytes) -> bytes:
    return data if b"\0" in data else data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def wc_changes(base: str) -> dict[str, bytes | None]:
    """Every runtime/ and aurora-main/ file external/Wiicompiled's working tree changes from base, as installed,
    None when deleted. A new file is seen once it is staged (git add -N)."""
    out = git_wc("diff", "--no-renames", "--name-status", base, "--", *WC_SOURCE_TREES).decode()
    changes = {}
    for line in out.splitlines():
        status, path = line.split("\t", 1)
        changes[path] = None if status == "D" else installed_form((WC_SOURCE / path).read_bytes())
    return changes


def changes_stamp(changes: dict[str, bytes | None]) -> dict[str, str]:
    return {p: "deleted" if c is None else hashlib.sha256(c).hexdigest() for p, c in sorted(changes.items())}


# The link is not byte-reproducible, so a patch that compiled into nothing cannot be told from the exe;
# a prebuilt Aurora is the one way the patched sources go unused, and the cache names it.
def links_prebuilt_aurora(cache: Path) -> bool:
    return re.search(r"^MKW_NATIVE_PREBUILT_DIR:PATH=.+$", cache.read_text(encoding="utf-8"), re.M) is not None


def patched_dir(layout: Layout) -> Path:
    return layout.wc_install.parent / "Patched" / "RetroRewind"


# Repair verifies the toolkit's sources, so the changed files stay in the workspace for one incremental build
# only; the exe goes to Patched/RetroRewind beside Install, and the official product stays current for repair.
def build_patched(layout: Layout, installed_exe: Path, base: str) -> int:
    workspace = layout.wc_install / "BuildWorkspace"
    toolkit = layout.wc_install / "Toolkit"
    product = layout.wc_install / "RetroRewind"
    changes = wc_changes(base)
    if not changes:
        fail(f"external/Wiicompiled's working tree changes nothing under runtime/ or aurora-main/ from {base[:9]}")
    if run_setup_exe(installed_exe, ["--check-products", "--retro-dir", str(layout.rr)], "repair") != 0:
        fail("the 8P Retro Rewind product is not current; run wiicompiled repair first")
    if links_prebuilt_aurora(workspace / "native-build" / "CMakeCache.txt"):
        fail("native-build links Dependencies/native_prebuilt's Aurora, so no change would reach the exe")
    saved: dict[str, bytes | None] = {}
    for path in changes:
        target = guard(layout, workspace / path)
        in_base = subprocess.run(["git", "-C", str(WC_SOURCE), "cat-file", "-e", f"{base}:{path}"],
                                 capture_output=True).returncode == 0
        if in_base != target.exists():
            fail(f"{target} {'is missing' if in_base else 'exists'}; is the install at {base[:9]}?")
        if in_base and target.read_bytes() != installed_form(git_wc("show", f"{base}:{path}")):
            fail(f"{target} differs from {base[:9]}; repair the install, or give its commit with --base")
        saved[path] = target.read_bytes() if in_base else None
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(toolkit / "llvm-mingw" / "bin"), str(toolkit / "CMake" / "bin"),
                                   str(toolkit / "Ninja"), env["PATH"]])
    made: list[Path] = []
    try:
        for path, content in changes.items():
            target = workspace / path
            if content is None:
                target.unlink()
                continue
            for parent in reversed(target.relative_to(workspace).parents[:-1]):
                if not (workspace / parent).exists():
                    (workspace / parent).mkdir()
                    made.append(workspace / parent)
            target.write_bytes(content)
        print(f"[patch] {len(changes)} file(s) from external/Wiicompiled over {base[:9]}")
        rc = subprocess.run([str(toolkit / "CMake" / "bin" / "cmake.exe"), "--build", str(workspace / "native-build"),
                             "--target", "RetroRewind", "--parallel", str(os.cpu_count() or 8)], env=env).returncode
    finally:
        # Written now rather than copied with the old mtime: Ninja has to see each file as newer than
        # its changed object, or the next repair would link the changed code into the official exe.
        failed = []
        for path, data in saved.items():
            try:
                if data is None:
                    (workspace / path).unlink(missing_ok=True)
                else:
                    (workspace / path).write_bytes(data)
            except OSError as e:
                failed.append(f"{path}: {e}")
        for folder in reversed(made):
            try:
                folder.rmdir()
            except OSError as e:
                failed.append(f"{folder}: {e}")
        print(f"[patch] workspace restored ({len(saved)} file(s), {len(failed)} failure(s))")
        for line in failed:
            print(f"[patch] NOT RESTORED {line}; put it back as at {base[:9]} before a repair")
    if failed:
        fail("the workspace is not fully restored (above)")
    if rc != 0:
        fail("the native build failed")
    built = workspace / "native-build" / "RetroRewind.exe"
    out = guard(layout, patched_dir(layout))
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(product, out)
    shutil.copy2(built, out / "RetroRewind.exe")
    stamp = {"ProductExeSha256": sha256_file(product / "RetroRewind.exe"), "Base": base,
             "Files": changes_stamp(changes)}
    (out / "patches.json").write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"[patch] {out / 'RetroRewind.exe'} built from {len(changes)} changed file(s) over {base[:9]}")
    return 0


def patched_exe(layout: Layout, installed_exe: Path) -> Path:
    """The patched exe, refused unless it is built from the current product and today's submodule tree."""
    if run_setup_exe(installed_exe, ["--check-products", "--retro-dir", str(layout.rr)], "repair") != 0:
        fail("the 8P Retro Rewind product is not current; run wiicompiled repair, then wiicompiled patch")
    out = patched_dir(layout)
    stamp = out / "patches.json"
    if not stamp.exists():
        fail(f"no patched build in {out}; run wiicompiled patch")
    recorded = json.loads(stamp.read_text(encoding="utf-8"))
    if recorded["ProductExeSha256"] != sha256_file(layout.wc_install / "RetroRewind" / "RetroRewind.exe"):
        fail("the patched build predates the current Retro Rewind product; run wiicompiled patch again")
    if "Base" not in recorded or recorded["Files"] != changes_stamp(wc_changes(recorded["Base"])):
        fail("the patched build was made from another external/Wiicompiled tree; run wiicompiled patch again")
    return out / "RetroRewind.exe"


def press_dialog_buttons(dialog: int) -> None:
    """Clicks each of the dialog's buttons (an MB_OK box has one)."""
    buttons = []

    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(32)
        GetClassNameW(hwnd, name, len(name))
        if name.value.lower() == "button":
            buttons.append(hwnd)
        return True

    EnumChildWindows(dialog, WNDENUMPROC(visit), 0)
    for button in buttons:
        SendMessageTimeoutW(button, BM_CLICK, 0, 0, SMTO_ABORTIFHUNG, 3000, ctypes.byref(ctypes.c_size_t()))


# Starts RetroRewind.exe as --launch-retro does (its own folder as the working directory) after the same
# currency check, so its stderr, where Aurora's fatal messages go, lands in wc-runs/. A crash shows
# WiiCompiled's "Fatal Error" box, which run answers, then leaves crash_*.txt in the run's log folder;
# the process is ended through its own handle, never by image name: the owner's runs the same exe.
def run_wiicompiled(layout: Layout, installed_exe: Path, seconds: float, shot_every: float, patched: bool) -> int:
    if patched:
        exe = patched_exe(layout, installed_exe)
    elif run_setup_exe(installed_exe, ["--check-products", "--retro-dir", str(layout.rr)], "repair") != 0:
        fail("the 8P Retro Rewind product is not current; run wiicompiled repair")
    else:
        exe = layout.wc_install / "RetroRewind" / "RetroRewind.exe"
    logs = layout.wc_userdata / "Logs"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    runs = guard(layout, layout.wc_runs)
    runs.mkdir(parents=True, exist_ok=True)
    stdio = runs / f"{stamp}.stdio.log"
    with open(stdio, "wb") as out:
        proc = subprocess.Popen([str(exe)], cwd=exe.parent, stdout=out, stderr=subprocess.STDOUT)
    print(f"[run] pid {proc.pid}, stdout and stderr to {stdio}")
    start = time.monotonic()
    next_shot = shot_every if shot_every > 0 else float("inf")
    box = None
    while proc.poll() is None and time.monotonic() - start < seconds:
        box = next((w for w in process_windows(proc.pid) if "Fatal Error" in window_title(w)), None)
        if box is not None:
            break
        elapsed = time.monotonic() - start
        if elapsed >= next_shot:
            save_shots(layout, proc.pid, runs, f"{stamp}-{int(elapsed):04d}s")
            next_shot += shot_every
        time.sleep(1)
    elapsed = time.monotonic() - start
    exited = proc.poll()
    how = f"exited {exited}"
    if exited is None:
        # With the box up, only the box: v0.9.0's game window stops answering PrintWindow until it is closed.
        save_shots(layout, proc.pid, runs, f"{stamp}-{int(elapsed):04d}s-end", [box] if box is not None else None)
        if box is not None:
            # The box (a MessageBox) blocks the abort handler before it writes crash_*.txt and the
            # memory dumps, so OK comes first and the dump gets time to finish.
            press_dialog_buttons(box)
            deadline = time.monotonic() + 5
            while IsWindow(box) and time.monotonic() < deadline:
                time.sleep(0.25)
            pressed = "OK pressed"
            if IsWindow(box):
                PostMessageW(box, WM_CLOSE, 0, 0)
                pressed = "OK ignored, WM_CLOSE sent"
            try:
                how = f"Fatal Error box, {pressed}, exited {proc.wait(timeout=60)}"
            except subprocess.TimeoutExpired:
                how = f"Fatal Error box, {pressed}, still running after 60s"
        else:
            how = "ended"
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)
            how += "; killed"
    # Windows reuses pids and Logs keeps earlier runs: only a folder written during this run counts.
    began = time.time() - (time.monotonic() - start) - 5
    ours = [d for d in logs.iterdir() if d.is_dir() and d.name.endswith(f"_pid{proc.pid}")
            and d.stat().st_mtime >= began] if logs.exists() else []
    folder = max(ours, key=lambda d: d.stat().st_mtime, default=None)
    crash = next(iter(sorted(folder.glob("crash_*.txt"))), None) if folder is not None else None
    print(f"[run] {how} after {elapsed:.0f}s; log folder {folder}")
    if folder is not None and (folder / "console.log").exists():
        lines = (folder / "console.log").read_text(encoding="utf-8", errors="replace").splitlines()
        marks = [l for l in lines if re.search(r"ss8 |Scene|New race|\[runtime\]|\[memory\]", l)]
        print("--- console.log, ss8/scene/runtime lines (last 25) ---")
        print("\n".join(marks[-25:]))
    if crash is not None:
        print(f"--- {crash} (head) ---")
        print("\n".join(crash.read_text(encoding="utf-8", errors="replace").splitlines()[:6]))
    tail = [l for l in stdio.read_text(encoding="utf-8", errors="replace").splitlines()
            if not l.startswith(("[nand]", "[OSReport]"))]
    print(f"--- {stdio.name}, last 15 lines other than [nand]/[OSReport] ---")
    print("\n".join(tail[-15:]))
    if crash is not None or box is not None or exited is not None:
        print("[run] FAIL: " + ("crashed" if crash is not None or box is not None else "exited early"))
        return 1
    print(f"[run] PASS: ran {seconds:.0f}s with no crash")
    return 0


def cmd_wiicompiled(layout: Layout, args) -> None:
    installed_exe = layout.wc_install / "WiiCompiled-Setup.exe"

    if args.action == "restore-shortcuts":
        restore_shortcuts(layout)
        return
    if args.action == "setup":
        if (layout.wc_install / "install-state.json").exists():
            fail("WiiCompiled is already installed in the 8P root; use repair or upgrade")
        sys.exit(install_wiicompiled(layout, setup_installer(args)))

    if not installed_exe.exists():
        fail("no WiiCompiled install in the 8P root; run wiicompiled setup first")
    if args.action == "upgrade":
        if args.installer is None:
            fail("upgrade needs --installer (the new release's WiiCompiled-Setup.exe)")
        sys.exit(upgrade_wiicompiled(layout, setup_installer(args)))
    if args.action == "check":
        sys.exit(run_setup_exe(installed_exe, ["--check-products", "--retro-dir", str(layout.rr)], "repair"))
    if args.action == "repair":
        sys.exit(repair_wiicompiled(layout))
    if args.action == "patch":
        sys.exit(build_patched(layout, installed_exe, wc_base(layout, args.base)))
    if args.action == "run":
        sys.exit(run_wiicompiled(layout, installed_exe, args.seconds, args.shot_every, args.patched))
    if args.patched:
        exe = patched_exe(layout, installed_exe)
        subprocess.Popen([str(exe)], cwd=exe.parent)
        print(f"[wiicompiled] started {exe}")
        return
    subprocess.Popen([str(installed_exe), "--launch-retro"])
    print(f"[wiicompiled] started {installed_exe} --launch-retro")


def owner_files() -> list[Path]:
    files = [RR_SRC / "Binaries" / "Code.pul", RR_SRC / "UI" / "MenuMulti.szs", RR_XML_SRC,
             *(DOLPHIN_USER_SRC / "Wii" / "title" / "00010004" / title_id(r) / "data" / "rksys.dat"
               for r in REGIONS.values()),
             RECOMP_SRC / "UserData" / "Config.toml", RECOMP_SRC / "Install" / "install-state.json",
             *shortcut_paths()]
    files += sorted((DOLPHIN_USER_SRC / "Config").glob("*.ini"))
    # The owner's RR saves: the XML's <savegame> redirect under Wheel Wizard's Riivolution root.
    files += sorted(p for p in (WW_RIIVO / "riivolution" / "save").rglob("*") if p.is_file())
    return files


def cmd_isolation(layout: Layout, args) -> None:
    current = {}
    for f in owner_files():
        current[str(f)] = {"sha256": sha256(f), "size": f.stat().st_size, "mtime": f.stat().st_mtime} if f.exists() else None
    if args.action == "snapshot":
        guard(layout, layout.snapshot).write_text(json.dumps(current, indent=2), encoding="utf-8")
        print(f"[isolation] {len(current)} files recorded in {layout.snapshot}")
        return
    if not layout.snapshot.exists():
        fail("no snapshot; run isolation snapshot first")
    before = json.loads(layout.snapshot.read_text(encoding="utf-8"))
    changed = [f for f in sorted(set(before) | set(current))
               if (before.get(f) or {}).get("sha256") != (current.get(f) or {}).get("sha256")]
    for f in changed:
        print(f"[isolation] CHANGED {f}")
    print(f"[isolation] {len(changed)} of {len(current)} owner files changed since the snapshot")
    sys.exit(1 if changed else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, help="the 8P install root (default: slot 0's)")
    ap.add_argument("--slot", type=int, default=0, help="slot K's root: 0 is the owner's 8P copy, 1+ unattended copies beside it")
    ap.add_argument("--vanilla", action="store_true", help="the root of Mario Kart Wii without Retro Rewind (and its slots)")
    ap.add_argument("--disc", type=Path, help="a Mario Kart Wii disc image (PAL, USA or Japan) in place of Wheel Wizard's")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    p = sub.add_parser("code")
    p.add_argument("variant", choices=CODE_PULS)
    p.add_argument("--from", dest="src", type=Path, help="this Code.pul (Loader.pul beside it) in place of the variant's build")
    p.set_defaults(fn=cmd_code)
    p = sub.add_parser("script")
    p.add_argument("file", type=Path, help="a menu script (tools/menu_scripts/)")
    p.set_defaults(fn=cmd_script)
    p = sub.add_parser("dolphin")
    p.add_argument("--debugger", action="store_true", help="open Dolphin's debugger panes (code, breakpoints, call stack)")
    p.add_argument("--gdb", type=int, nargs="?", const=2345, metavar="PORT",
                   help="enable the GDB stub on PORT (default 2345); emulation waits for a client")
    p.add_argument("--pause-on-panic", action="store_true", help="pause emulation at a panic alert")
    p.add_argument("--unattended", action="store_true",
                   help="keep GC ports 1-4 plugged in with no host pad (for the debug boot)")
    p.add_argument("--wiimotes", action="store_true",
                   help="with --unattended: connect emulated Wii Remotes 1-4 with a Classic Controller (boots of 5-8 locals)")
    p.add_argument("--res", type=int, default=1, choices=range(1, 9),
                   help="with --unattended: internal resolution, a multiple of native (default 1)")
    p.add_argument("--fast", action="store_true",
                   help="with --unattended: unlimited emulation speed (menu-script lanes, D114)")
    p.add_argument("--dump", action="store_true",
                   help="record the launch's video and audio to Dolphin/Dump/Frames and Dump/Audio (real speed)")
    p.add_argument("--footage", action="store_true",
                   help="with --dump: record the internal-resolution frame, lossless (Ut Video), with 16x anisotropy")
    p.add_argument("--plain", action="store_true", help="--vanilla root: the disc alone, no Riivolution pack (1-4P parity)")
    p.add_argument("--movie", type=Path, help="play this Dolphin input movie (.dtm) from boot")
    p.add_argument("--osd", action="store_true", help="show Dolphin's frame and input counts and a movie's pad state")
    p.add_argument("--image", action="store_true", help="the copy's Wii image (wii), no Riivolution")
    p.set_defaults(fn=cmd_dolphin)
    sub.add_parser("wii").set_defaults(fn=cmd_wii)
    p = sub.add_parser("stop")
    p.add_argument("--timeout", type=float, default=0, help="seconds to wait for a graceful close before /F (default 0: /F at once)")
    p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("shot")
    p.add_argument("--out", type=Path, help="output directory (default <root>/shots)")
    p.set_defaults(fn=cmd_shot)
    p = sub.add_parser("log")
    p.add_argument("--tail", type=int, default=60, metavar="N", help="print the last N lines (0: all)")
    p.add_argument("--grep", metavar="REGEX", help="only lines matching REGEX, then the last N of those")
    p.set_defaults(fn=cmd_log)
    p = sub.add_parser("wiicompiled")
    p.add_argument("action", choices=["setup", "upgrade", "check", "repair", "patch", "launch", "run", "restore-shortcuts"])
    p.add_argument("--base", metavar="COMMIT",
                   help="patch: the WiiCompiled commit the install was built from (default: its Installers/main-<sha>)")
    p.add_argument("--patched", action="store_true", help="launch/run: the patched build in WiiCompiled/Patched")
    p.add_argument("--installer", type=Path, help="setup/upgrade: this WiiCompiled-Setup.exe (setup's default: Wheel Wizard's cache)")
    p.add_argument("--seconds", type=float, default=180, help="run: end the game after this many seconds")
    p.add_argument("--shot-every", type=float, default=30, metavar="S", help="run: a screenshot every S seconds (0: only at the end)")
    p.set_defaults(fn=cmd_wiicompiled)
    p = sub.add_parser("isolation")
    p.add_argument("action", choices=["snapshot", "verify"])
    p.set_defaults(fn=cmd_isolation)
    args = ap.parse_args()
    global DISC
    DISC = args.disc.resolve() if args.disc else DISC
    if args.root is not None and args.slot:
        ap.error("--root and --slot both name the root; pick one")
    args.fn(Layout(args.root or slot_root(args.slot, args.vanilla), args.vanilla), args)


if __name__ == "__main__":
    main()
