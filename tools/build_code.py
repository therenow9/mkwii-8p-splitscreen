#!/usr/bin/env python3
"""Build Retro Rewind's Code.pul (rr-pulsar fork + SplitScreen8) without GNU make.

Mirrors external/rr-pulsar/Makefile: compiles every PulsarEngine .cpp/.S with
CodeWarrior, links with Kamek (-dynamic, PAL/NTSC/JPN/KOR via versions.txt), then
packs the region binaries with scripts/combine_bin.py.

Configuration comes from environment variables or the repo-root .env file:
  MWCC   path to mwcceppc.exe          (default: tools/cw-wii17/mwcceppc.exe)
  MWASM  path to mwasmeppc.exe         (default: tools/cw-wii17/mwasmeppc.exe)
  KAMEK  path to Kamek.exe             (default: tools/kamek/Kamek.exe)
  EXTRA_CFLAGS  extra compiler flags   (e.g. -DSPLITSCREEN8_DEBUG)
Relative tool paths are taken from the repo root.

An object is rebuilt when its source or any header in its .d file (mwcceppc -MD -gccdep) is newer,
and every object is rebuilt when the compile flags differ from the output directory's cflags.txt.

--debug-screens N widens a VS race of 2-4 locals (1-4 in a debug boot) to N screens and compiles in
SplitScreen8's diagnostics (-DSS8_DEBUG_SCREENS=N), and builds into build/code-debugN. Every build
widens a VS race of 5-8 locals: 5-6 to 6 screens, 7-8 to 8.

--debug-boot boots past the menus into a VS race (-DSS8_DEBUG_BOOT; track -DSS8_BOOT_TRACK=PulsarId;
--boot-variant N races the track's variant N, -DSS8_BOOT_VARIANT=N;
--boot-locals N local players on GC ports 1-4 then Wii Remote channels 1-4, -DSS8_BOOT_LOCALS=N;
--boot-items races with items,
-DSS8_BOOT_ITEMS=1; --boot-autodrive gives every local's kart to the AI, so all of them finish,
-DSS8_BOOT_AUTODRIVE=1; --boot-manual leaves out the scripted presses, for a playtest with real pads,
-DSS8_BOOT_MANUAL=1; --boot-respawn sends players through the game's fall respawn one by one,
-DSS8_BOOT_RESPAWN=1; --boot-use-item ITEM has the last racer use a blooper, pow or thunder (P5 a
bullet, the last local a mega, star or cloud) three times, implying --boot-items, -DSS8_BOOT_USE_ITEM=1..7;
--boot-speedo turns RR's speedometer setting on in RAM, -DSS8_BOOT_SPEEDO=1; --boot-no-map leaves
out a widened race's centre map, -DSS8_BOOT_NO_MAP=1; --boot-pause-by N has local N (5-8) drive the
scripted pause menu on its Classic Controller instead of GC port 1, -DSS8_BOOT_PAUSE_BY=N; --boot-battle
boots a team Balloon Battle instead, presses nothing and logs the round's end, -DSS8_BOOT_BATTLE=1) and adds
"-boot" to the output directory.
DebugBoot.cpp is always recompiled there, because the --boot-* options change a define without
changing a file.

--menu-script (not with --debug-boot) presses GC pad buttons on ports 1-4, and Classic Controller
buttons on Wii channels 1-4 as ports 5-8, from boot as the copy's script says (install_8p.py script
packs a tools/menu_scripts/ file into the copy, and MenuScript.cpp reads it), -DSS8_MENU_SCRIPT, and
adds "-menu" to the output directory. One build serves every script.

--vanilla builds SplitScreen8 for Mario Kart Wii without Retro Rewind: only SplitScreen8/ and
UI/SameCharacterSelect.cpp, with -DSS8_VANILLA, into build/code-vanilla, plus the Pulsar Loader.pul that loads
it (Loader/, Kamek -static=0x80004000). The link fails on any RR symbol a SplitScreen8 file still calls.
With --debug-boot, --boot-track is the game's CourseId (0x00-0x1f, an arena 0x20-0x29 with --boot-battle).

No -DPROD: public builds talk to the non-RR test WFC domain and leave out the
private anticheat, so the result is a local-play build.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Overridden by --pulsar-repo / --out, e.g. to build a stock rr-pulsar worktree as a baseline.
PULSAR_REPO = ROOT / "external" / "rr-pulsar"
BUILD = ROOT / "build" / "code"


def load_env() -> dict[str, str]:
    env = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    env.update({k: v for k, v in os.environ.items() if k in ("MWCC", "MWASM", "KAMEK", "EXTRA_CFLAGS")})
    return env


def write_version_file(suffix: str) -> None:
    try:
        desc = subprocess.run(["git", "describe", "--always", "--dirty", "--match", "NOT A TAG"],
                              cwd=PULSAR_REPO, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        desc = "unknown"
    text = f'#include "Version.hpp"\n\nwchar_t *GIT_COMMIT = L"{desc}{suffix}";\n'
    path = PULSAR_REPO / "PulsarEngine" / "Version.cpp"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def read_deps(dep: Path) -> list[Path] | None:
    """The sources and headers an object was built from, from the .d file mwcceppc's -MD -gccdep writes
    beside it: "<obj>: <src> \\" then one absolute header per line. None when it is missing or unreadable.
    -MMD would list nothing here, because -I- makes every -i directory a system include."""
    try:
        text = dep.read_text(errors="replace")
    except OSError:
        return None
    # The target ends at the first colon followed by whitespace; a drive letter's colon is followed by "\".
    body = text.replace("\\\r\n", " ").replace("\\\n", " ")
    sep = body.find(": ")
    if sep < 0:
        return None
    names = body[sep + 2:].replace("\\ ", "\0").split()
    return [PULSAR_REPO / n.replace("\0", " ") for n in names]


def needs_build(src: Path, obj: Path) -> bool:
    if not obj.exists():
        return True
    built = obj.stat().st_mtime
    deps = [src] if src.suffix == ".S" else read_deps(obj.with_suffix(".d"))
    if not deps:
        return True
    try:
        return any(d.stat().st_mtime > built for d in deps)
    except OSError:
        return True


def flags_stamp(cflags: list[str]) -> str:
    # Only DebugBoot.cpp reads the SS8_BOOT_* values, and it is always recompiled in a boot build, so the
    # smoke cases that share one output directory do not rebuild everything for a different --boot-locals.
    return "\n".join(f for f in cflags if not f.startswith("-DSS8_BOOT_")) + "\n"


# --boot-use-item choices, in DebugBoot.cpp's bootItems order (SS8_BOOT_USE_ITEM is the index + 1).
BOOT_ITEMS = ("blooper", "pow", "thunder", "bullet", "mega", "star", "cloud")

def main() -> int:
    global PULSAR_REPO, BUILD
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", action="store_true", help="rebuild every object")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--random-key", default="0xADD2BFAF", help="PULSAR_RANDOM_KEY (Makefile default)")
    ap.add_argument("--pulsar-repo", type=Path, default=PULSAR_REPO, help="rr-pulsar checkout to build")
    ap.add_argument("--out", type=Path, default=None, help="output directory")
    ap.add_argument("--debug-screens", type=int, choices=(6, 8), help="widen races of 2-4 locals to this many screens (debug build)")
    ap.add_argument("--debug-boot", action="store_true", help="boot straight into a VS race (debug build)")
    ap.add_argument("--boot-track", type=lambda s: int(s, 0), default=0,
                    help="PulsarId of the --debug-boot track (default 0, Luigi Circuit); with --vanilla a CourseId (0, Mario Circuit)")
    ap.add_argument("--boot-variant", type=int, default=0,
                    help="variant of the --debug-boot track, 1..its variantCount (default 0, the main one)")
    ap.add_argument("--boot-locals", type=int, choices=range(1, 9), default=1,
                    help="local players in the --debug-boot race: GC ports 1-4, then Wii Remote channels 1-4 (default 1)")
    ap.add_argument("--boot-items", action="store_true", help="race the --debug-boot race with items (default none)")
    ap.add_argument("--boot-autodrive", action="store_true",
                    help="the AI drives every local in the --debug-boot race, so all finish (default off)")
    ap.add_argument("--boot-manual", action="store_true",
                    help="no scripted presses in the --debug-boot race, for real pads (default off)")
    ap.add_argument("--boot-respawn", action="store_true",
                    help="players respawn one by one in the --debug-boot race, from 15 s in (default off)")
    ap.add_argument("--boot-use-item", choices=BOOT_ITEMS,
                    help="the last racer (P5 for a bullet, the last local for a mega, star or cloud) uses this item three times in the --debug-boot race, from 20 s in")
    ap.add_argument("--boot-speedo", action="store_true",
                    help="RR's speedometer setting on (whole digits) for the --debug-boot race, in RAM only (default: the save's)")
    ap.add_argument("--boot-pause-by", type=int, choices=range(5, 9),
                    help="local 5-8 drives the --debug-boot race's scripted pause menu on its Classic Controller")
    ap.add_argument("--boot-no-map", action="store_true",
                    help="no centre map in a widened --debug-boot race (default: the map)")
    ap.add_argument("--boot-battle", action="store_true",
                    help="a team Balloon Battle in the --debug-boot arena, no presses, the round's end logged (default VS)")
    ap.add_argument("--vanilla", action="store_true",
                    help="SplitScreen8 for Mario Kart Wii without Retro Rewind, and its Loader.pul")
    ap.add_argument("--menu-script", action="store_true",
                    help="press pad buttons from boot as the copy's script says (debug build; install_8p.py script)")
    args = ap.parse_args()
    debug, boot, vanilla = args.debug_screens, args.debug_boot, args.vanilla
    if vanilla and (args.boot_variant or args.boot_speedo):
        ap.error("--boot-variant and --boot-speedo are RR's; --vanilla has neither")
    if vanilla and boot and not (0x20 <= args.boot_track <= 0x29 if args.boot_battle else args.boot_track <= 0x1F):
        ap.error("--vanilla's --boot-track is a CourseId: 0x00-0x1f, or with --boot-battle an arena 0x20-0x29")
    if args.menu_script and boot:
        ap.error("--menu-script and --debug-boot both script the pads; pick one")
    if (args.boot_track or args.boot_variant or args.boot_locals != 1 or args.boot_items or args.boot_autodrive or args.boot_manual
            or args.boot_respawn or args.boot_use_item or args.boot_speedo or args.boot_no_map or args.boot_pause_by
            or args.boot_battle) and not boot:
        ap.error("--boot-track, --boot-variant, --boot-locals, --boot-items, --boot-autodrive, --boot-manual, --boot-respawn, --boot-use-item, "
                 "--boot-speedo, --boot-no-map, --boot-pause-by and --boot-battle need --debug-boot")
    if args.boot_battle and (args.boot_pause_by or args.boot_autodrive):
        ap.error("--boot-battle presses nothing; it cannot take --boot-pause-by or --boot-autodrive")
    if args.boot_pause_by and (args.boot_pause_by > args.boot_locals or args.boot_manual or args.boot_autodrive):
        ap.error("--boot-pause-by N needs at least N --boot-locals and the scripted pause (not --boot-manual or --boot-autodrive)")
    if args.boot_autodrive and args.boot_manual:
        ap.error("--boot-autodrive presses A to reach the next race; it cannot be --boot-manual")
    if (args.boot_respawn or args.boot_use_item) and args.boot_manual:
        ap.error("--boot-respawn and --boot-use-item are scripted; they cannot be --boot-manual")
    use_item = BOOT_ITEMS.index(args.boot_use_item) + 1 if args.boot_use_item else 0
    menu = args.menu_script
    out = args.out or BUILD.with_name("code" + ("-vanilla" if vanilla else "") + (f"-debug{debug}" if debug else "") + ("-boot" if boot else "")
                                      + ("-menu" if menu else ""))
    PULSAR_REPO, BUILD = args.pulsar_repo.resolve(), out.resolve()

    env = load_env()
    # The tools run with rr-pulsar as their working directory, so anchor relative paths here.
    mwcc = ROOT / env.get("MWCC", "tools/cw-wii17/mwcceppc.exe")
    mwasm = ROOT / env.get("MWASM", "tools/cw-wii17/mwasmeppc.exe")
    kamek = ROOT / env.get("KAMEK", "tools/kamek/Kamek.exe")
    for tool in (mwcc, mwasm, kamek):
        if not tool.exists():
            print(f"missing tool: {tool} (see README.md, Toolchain)", file=sys.stderr)
            return 1

    gamesource = PULSAR_REPO / "GameSource"
    engine = PULSAR_REPO / "PulsarEngine"
    cflags = [
        "-I-", "-i", str(PULSAR_REPO / "KamekInclude"), "-i", str(gamesource), "-i", str(engine),
        "-i", str(PULSAR_REPO / "Loader"),
        "-opt", "all", "-inline", "auto", "-enum", "int", "-proc", "gekko", "-fp", "hard",
        "-sdata", "0", "-sdata2", "0", "-maxerrors", "1", "-func_align", "4",
        f"-DPULSAR_RANDOM_KEY={args.random_key}",
    ] + ([f"-DSS8_DEBUG_SCREENS={debug}"] if debug else []) \
      + (["-DSS8_DEBUG_BOOT", f"-DSS8_BOOT_TRACK={args.boot_track}", f"-DSS8_BOOT_VARIANT={args.boot_variant}",
          f"-DSS8_BOOT_LOCALS={args.boot_locals}",
          f"-DSS8_BOOT_ITEMS={int(args.boot_items or use_item != 0)}", f"-DSS8_BOOT_AUTODRIVE={int(args.boot_autodrive)}",
          f"-DSS8_BOOT_MANUAL={int(args.boot_manual)}", f"-DSS8_BOOT_RESPAWN={int(args.boot_respawn)}",
          f"-DSS8_BOOT_USE_ITEM={use_item}", f"-DSS8_BOOT_SPEEDO={int(args.boot_speedo)}",
          f"-DSS8_BOOT_NO_MAP={int(args.boot_no_map)}", f"-DSS8_BOOT_PAUSE_BY={args.boot_pause_by or 0}",
          f"-DSS8_BOOT_BATTLE={int(args.boot_battle)}"]
         if boot else []) \
      + (["-DSS8_MENU_SCRIPT"] if menu else [])       + (["-DSS8_VANILLA"] if vanilla else []) \
      + shlex.split(env.get("EXTRA_CFLAGS", ""))
    asflags = ["-proc", "gekko", "-c"]

    write_version_file("-ss8" + ("v" if vanilla else "") + (f"-dbg{debug}" if debug else "") + ("-boot" if boot else "") + ("-menu" if menu else ""))
    # Compile flags are not in any .d file: a change to them (EXTRA_CFLAGS, --random-key, the tool paths)
    # rebuilds every object.
    stamp, stamp_text = BUILD / "cflags.txt", flags_stamp([str(mwcc), str(mwasm), *cflags])
    rebuild_all = args.clean or not stamp.exists() or stamp.read_text() != stamp_text
    if rebuild_all and BUILD.exists():
        for p in sorted([*BUILD.rglob("*.o"), *BUILD.rglob("*.d")]):
            p.unlink()

    jobs: list[tuple[list[str], Path, Path]] = []
    objs: list[Path] = []
    # -gccdep writes the .d beside the object rather than into the working directory under the source's name.
    compile_cpp = [str(mwcc), *cflags, "-MD", "-gccdep", "-c", "-o"]
    kamek_obj = BUILD / "kamek.o"
    jobs.append((compile_cpp, PULSAR_REPO / "KamekInclude" / "kamek.cpp", kamek_obj))
    objs.append(kamek_obj)
    for src in sorted(engine.rglob("*")):
        if src.suffix not in (".cpp", ".S"):
            continue
        rel = src.relative_to(engine).as_posix()
        if vanilla and not (rel.startswith("SplitScreen8/") or rel in ("UI/SameCharacterSelect.cpp", "Dolphin/DolphinIOS.cpp")):
            continue
        obj = BUILD / "PulsarEngine" / src.relative_to(engine).with_suffix(".o")
        objs.append(obj)
        tool = compile_cpp if src.suffix == ".cpp" else [str(mwasm), *asflags, "-o"]
        jobs.append((tool, src, obj))

    todo = [j for j in jobs if rebuild_all or needs_build(j[1], j[2]) or j[1].name == "RoomKey.cpp"
            or (boot and j[1].name == "DebugBoot.cpp")]
    print(f"[build] compiling {len(todo)}/{len(jobs)} files with {mwcc.name}")

    def run(job):
        tool, src, obj = job
        obj.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([*tool, str(obj), str(src)], cwd=PULSAR_REPO, capture_output=True, text=True)
        return src, r

    failed = False
    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for src, r in ex.map(run, todo):
            out = (r.stdout + r.stderr).strip()
            if r.returncode != 0:
                failed = True
                print(f"[error] {src.relative_to(PULSAR_REPO)}\n{out}", file=sys.stderr)
            elif out:
                print(f"[warn] {src.relative_to(PULSAR_REPO)}\n{out}")
    if failed:
        return 1
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(stamp_text)

    print("[build] linking with Kamek")
    link = [str(kamek), *map(str, objs), "-dynamic",
            f"-externals={gamesource / 'symbols.txt'}", f"-externals={gamesource / 'anticheat.txt'}",
            f"-versions={gamesource / 'versions.txt'}",
            f"-output-kamek={BUILD / 'Code.$KV$.bin'}", f"-output-map={BUILD / 'Code.map'}"]
    r = subprocess.run(link, cwd=PULSAR_REPO, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout + r.stderr, file=sys.stderr)
        return 1

    combine = [sys.executable, str(PULSAR_REPO / "scripts" / "combine_bin.py"),
               "-p", str(BUILD / "Code.P.bin"), "-e", str(BUILD / "Code.E.bin"),
               "-j", str(BUILD / "Code.J.bin"), "-o", str(BUILD / "Code.pul")]
    r = subprocess.run(combine, cwd=PULSAR_REPO, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout + r.stderr, file=sys.stderr)
        return 1
    print(f"[build] wrote {BUILD / 'Code.pul'}")
    return build_loader(mwcc, kamek) if vanilla else 0


def build_loader(mwcc: Path, kamek: Path) -> int:
    """Loader.pul, as Loader/Makefile builds it: the Riivolution pack loads it, and it loads /Binaries/Code.pul."""
    loader = PULSAR_REPO / "Loader"
    cflags = ["-I-", "-i", ".", "-i", str(PULSAR_REPO / "KamekInclude"), "-i", str(PULSAR_REPO / "GameSource"),
              "-i", str(PULSAR_REPO / "GameSource" / "MarioKartWii"), "-i", str(PULSAR_REPO / "PulsarEngine"),
              "-opt", "all", "-inline", "auto", "-enum", "int", "-proc", "gekko", "-fp", "hard",
              "-sdata", "0", "-sdata2", "0", "-maxerrors", "1", "-func_align", "4"]
    objs = []
    for src in ("mkw.cpp", "kamekLoader.cpp"):
        obj = BUILD / "Loader" / Path(src).with_suffix(".o")
        obj.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([str(mwcc), *cflags, "-c", "-o", str(obj), src], cwd=loader, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[error] Loader/{src}\n{(r.stdout + r.stderr).strip()}", file=sys.stderr)
            return 1
        objs.append(str(obj))
    r = subprocess.run([str(kamek), *objs, "-static=0x80004000", f"-output-code={BUILD / 'Loader.pul'}"],
                       cwd=loader, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout + r.stderr, file=sys.stderr)
        return 1
    print(f"[build] wrote {BUILD / 'Loader.pul'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
