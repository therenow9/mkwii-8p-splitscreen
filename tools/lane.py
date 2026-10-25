#!/usr/bin/env python3
"""Run menu-script lanes unattended in the 8P copy's Dolphin: build, install, run, shoot, log.

Usage:
  lane.py run VARIANT SCRIPT [SHOT ...] [--out DIR] [--total S] [--fast] [--wiimotes] [--no-build]
              [--keep-settings] [--past-race] [--res N] [--footage]
  lane.py batch FILE [--out DIR] [--slots N] [--first-slot K]   one lane per line, each line run's arguments ('#'
                                                                  comments); one report.md

VARIANT is ss8-menu, ss8-debug8-menu, or vanilla-menu (Mario Kart Wii without Retro Rewind, on install_8p.py's
--vanilla root and its slots); SCRIPT a tools/menu_scripts name or a path. Each variant is
built once, before the first lane, and every lane installs its script into the copy's NAND, where the build reads it
at boot. A SHOT is "REGEX@DELAY" (a shot DELAY s after REGEX first appears in the log) or "@time@N" (N s after
launch). A lane ends at the first of: 5 s after its race starts (unless --past-race), the script's last frame,
Dolphin exiting, or --total. Players 5-8 press on Classic Controllers, so a script that presses ports 5-8 launches
with --wiimotes itself.

--slots N runs the lanes on slots 0..N-1 (install_8p.py --slot K, each made by `prepare`), one lane per slot at a
time, each slot's windows in its own column of the primary display; --first-slot K starts at slot K instead, as for a
slot prepared from another disc (install_8p.py --slot K --disc FILE prepare).

A lane refuses to install or start while a Dolphin the batch did not start runs (the owner's included), and puts
its copy's RRGameSettings.pul (the vanilla game's save) back afterwards: RR saves its settings whole, and the vanilla
game its licenses' rules, so a script's "setting" line or rules page would reach every later run.
"""
from __future__ import annotations

import argparse
import hashlib
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(ROOT / "tools"))
import install_8p  # noqa: E402

BUILD_FLAGS = {"ss8-menu": [], "ss8-debug8-menu": ["--debug-screens", "8"], "vanilla-menu": ["--vanilla"]}
SUMMARY = re.compile(r"ss8 (?:select|skin|race player|menu: step|preview).*|Invalid.*")
LOADED = re.compile(r"ss8 menu: script \S+ steps (\d+)")


def run(args: list[str], log: Path) -> int:
    with log.open("w", encoding="utf-8") as f:
        return subprocess.run([PY, *args], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode


def script_path(name: str) -> Path:
    path = Path(name)
    return path if path.suffix else ROOT / "tools" / "menu_scripts" / f"{name}.txt"


def fingerprint() -> str:
    """The state of what a lane builds from, the root's tools/ and rr-pulsar: it must not change mid-batch. Docs may."""
    parts = []
    for repo, path in ((ROOT, "tools"), (ROOT / "external" / "rr-pulsar", ".")):
        diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD", "--", path], capture_output=True).stdout
        others = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--others", "--exclude-standard", "--", path],
                                capture_output=True).stdout.split(b"\0")
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True).stdout
        h = hashlib.sha256(head + diff)
        for f in filter(None, others):
            h.update(f + (repo / f.decode()).read_bytes())
        parts.append(h.hexdigest()[:12])
    return " ".join(parts)


def row(name: str, slot: int, result: str = "") -> dict:
    return {"lane": name, "slot": slot, "result": result, "end": "", "invalid": 0, "gpu": "", "steps": 0, "seconds": 0, "shots": []}


def is_vanilla(variant: str) -> bool:
    return variant.startswith("vanilla")


def lane(slot: int, tiled: bool, args, out: Path, shots: Path) -> dict:
    vanilla = is_vanilla(args.variant)
    layout = install_8p.Layout(install_8p.slot_root(slot, vanilla), vanilla)
    on = ["tools/install_8p.py", "--slot", str(slot)] + (["--vanilla"] if vanilla else [])
    script = script_path(args.script)
    parsed = install_8p.menu_script(script, layout.region)
    presses_5_8 = any(port >= 4 for _, port, _, _ in parsed["steps"])
    result = row(out.name, slot)
    # Before anything is written into the slot: on slot 0 that would be under the owner's running 8P game.
    if why := install_8p.foreign_dolphin():
        result["result"] = why
        return result
    out.mkdir(parents=True, exist_ok=True)
    started, since = time.monotonic(), time.time()
    code = install_8p.CODE_PULS[args.variant]
    (out / "code.sha").write_text(f"{install_8p.sha256(code)}  {code}\n", encoding="utf-8")
    settings = layout.save if vanilla else layout.nand_mod / "RRGameSettings.pul"
    settings_before = settings.read_bytes()
    (out / f"{settings.stem}.before{settings.suffix}").write_bytes(settings_before)
    pid = None
    try:
        if run([*on, "code", args.variant], out / "install.log") or run([*on, "script", str(script)], out / "script.log"):
            result["result"] = "install failed"
            return result
        launch = [*on, "dolphin", "--unattended"]
        launch += ["--wiimotes"] if args.wiimotes or presses_5_8 else []
        launch += ["--fast"] if args.fast else []
        launch += ["--res", str(args.res)] + (["--dump", "--footage"] if args.footage else [])
        rec = install_8p.guarded_launch(layout, lambda: run(launch, out / "dolphin.log") == 0)
        if isinstance(rec, str):
            result["result"] = rec
            return result
        pid = rec
        launched = time.monotonic()
        if tiled:
            install_8p.place_windows(pid[0], slot % install_8p.TILES)
        log = install_8p.LogTail(layout.dolphin_log)

        for n, spec in enumerate(args.shot, 1):
            pattern, delay = spec.rsplit("@", 1)
            if pattern == "@time":
                while time.monotonic() - launched < float(delay) and install_8p.dolphin_alive(*pid):
                    time.sleep(0.5)
            else:
                left = args.total - (time.monotonic() - launched)
                at, why = install_8p.wait_for(layout, log, re.compile(pattern, re.M), 0, left)
                if at is None:
                    result["end"] = f"no {pattern!r}: {why}"
                    break
                time.sleep(float(delay))
            if not install_8p.dolphin_alive(*pid):
                break
            written, refused = install_8p.save_shots(layout, pid[0], shots, f"{n:02d}")
            result["shots"].append(f"{n:02d} {spec} at {time.monotonic() - launched:.0f} s"
                                   + ("" if written and not refused else " (no shot)"))

        ticks, race, scanned, frame = re.compile(r"tick frame (\d+)"), re.compile(r"ss8 race player 7"), 0, 0
        while not result["end"]:
            text = log.read()
            frame = max([frame, *map(int, ticks.findall(text, scanned))])
            if not install_8p.dolphin_alive(*pid):
                result["end"] = "Dolphin exited"
            elif not args.past_race and race.search(text, scanned):
                time.sleep(5)
                result["end"] = "race started"
            elif frame >= parsed["end"]:
                result["end"] = f"script end, frame {parsed['end']}"
            elif time.monotonic() - launched > args.total:
                result["end"] = f"--total {args.total} s"
            else:
                scanned = max(scanned, text.rfind("\n") + 1)
                time.sleep(2)
        text = log.read()
        (out / "game.log").write_text(text, encoding="utf-8")
        summary = [m.group() for m in SUMMARY.finditer(text)][:600]
        (out / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
        result["invalid"] = sum("Invalid" in line for line in text.splitlines())
        result["gpu"] = " ".join(m.group(1) for m in install_8p.GPU_FIFO.finditer(text))
        result["steps"] = text.count("ss8 menu: step")
        missed = len(args.shot) - sum(not s.endswith("(no shot)") for s in result["shots"])
        # The copy's script as the game read it: a short or missing file runs no presses and could still end "ok".
        loaded = (m := LOADED.search(text)) is not None and int(m.group(1)) == len(parsed["steps"])
        result["result"] = ("FAIL" if result["invalid"] or result["gpu"] or missed or not loaded or result["end"] == "Dolphin exited"
                            else "ok") + (f", GPU opcode {result['gpu']}" if result["gpu"] else "") + (
                            f", {missed} shot(s) missed" if missed else "") + (
                            "" if loaded else ", the script did not load")
    finally:
        if pid is not None:
            run([*on, "stop"], out / "stop.log")
            if args.footage:
                (out / "dump").mkdir(exist_ok=True)
                # Only this run's files: another run's dumps stay. Windows holds a killed Dolphin's dump open a moment.
                for f in (*(layout.dolphin / "Dump" / "Frames").glob("*.avi"), *(layout.dolphin / "Dump" / "Audio").glob("*.wav")):
                    if f.stat().st_mtime < since:
                        continue
                    for _ in range(30):
                        try:
                            f.replace(out / "dump" / f.name)
                            break
                        except PermissionError:
                            time.sleep(1)
                    else:
                        print(f"[lane] {f} still locked, left in place", file=sys.stderr)
        if pid is not None:
            settings_after = settings.read_bytes()
            if not args.keep_settings:
                settings.write_bytes(settings_before)
            (out / f"{settings.stem}.after{settings.suffix}").write_bytes(settings_after)
        result["seconds"] = round(time.monotonic() - started)
    return result


def lane_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lane.py run", add_help=False)
    p.add_argument("variant", choices=BUILD_FLAGS)
    p.add_argument("script")
    p.add_argument("shot", nargs="*", help='"REGEX@DELAY" or "@time@N"')
    p.add_argument("--name", help="the lane's folder in a batch (default: the script's name)")
    p.add_argument("--total", type=float, default=420, help="end the lane this many seconds after launch")
    p.add_argument("--fast", action="store_true", help="unlimited emulation speed (D114; not for sound or @time shots)")
    p.add_argument("--wiimotes", action="store_true", help="connect Classic Controllers though no press is on ports 5-8")
    p.add_argument("--no-build", action="store_true", help="install the variant's last build as it is")
    p.add_argument("--keep-settings", action="store_true", help="leave RR's settings (the vanilla save) as the lane saved them")
    p.add_argument("--past-race", action="store_true", help="keep running after the race starts")
    p.add_argument("--res", type=int, default=1, help="Dolphin's internal resolution, a multiple of native")
    p.add_argument("--footage", action="store_true", help="record video (lossless) and sound into the lane's dump/ (real speed)")
    return p


def report_line(r: dict) -> str:
    return f"| {r['lane']} | {r['slot']} | {r['result']} | {r['end']} | {r['invalid']} | {r['gpu']} | {r['steps']} | {r['seconds']} |"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["run", "batch"])
    ap.add_argument("--out", type=Path, help="output folder (default build/lanes/<time>)")
    ap.add_argument("--slots", type=int, default=1, choices=range(1, install_8p.TILES + 1),
                    help="lanes at once, on slots 0..N-1 (default 1)")
    ap.add_argument("--first-slot", type=int, default=0, help="the slots start at K, not 0")
    known, rest = ap.parse_known_args()
    slots = range(known.first_slot, known.first_slot + known.slots)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = (known.out or ROOT / "build" / "lanes" / stamp).resolve()
    if known.mode == "run":
        lanes = [lane_parser().parse_args(rest)]
    else:
        if len(rest) != 1:
            ap.error("batch takes one FILE")
        lines = [line.split("#", 1)[0] for line in Path(rest[0]).read_text().splitlines()]
        lanes = [lane_parser().parse_args(shlex.split(line)) for line in lines if line.strip()]
    # A script's error ends the batch here, before anything is built or installed, never inside a slot's thread.
    for args in lanes:
        install_8p.menu_script(script_path(args.script))
    kinds = sorted({is_vanilla(a.variant) for a in lanes})
    layouts = {v: {k: install_8p.Layout(install_8p.slot_root(k, v), v) for k in slots} for v in kinds}
    for layout in (layout for v in kinds for layout in layouts[v].values()):
        if not layout.launch.exists():
            sys.exit(f"[lane] {layout.root} is not prepared: install_8p.py --slot K prepare")
    if why := install_8p.foreign_dolphin():
        sys.exit(f"[lane] {why}")
    locks = [install_8p.lock_slot(layout) for v in kinds for layout in layouts[v].values()]
    try:
        out.mkdir(parents=True, exist_ok=True)
        before = fingerprint()
        # One build per variant serves every lane: the script is the copy's file, not the build's.
        built = {}
        for variant in dict.fromkeys(a.variant for a in lanes if not a.no_build):
            print(f"== build {variant} {time.strftime('%H:%M:%S')}", flush=True)
            built[variant] = run(["tools/build_code.py", *BUILD_FLAGS[variant], "--menu-script"],
                                 out / f"build-{variant}.log") == 0
        todo: queue.Queue = queue.Queue()
        for n, args in enumerate(lanes):
            todo.put((n, args))
        rows: list[dict | None] = [None] * len(lanes)

        def worker(slot: int) -> None:
            while True:
                try:
                    n, args = todo.get_nowait()
                except queue.Empty:
                    return
                name = args.name or script_path(args.script).stem
                print(f"== {name} slot {slot} {time.strftime('%H:%M:%S')}", flush=True)
                if not built.get(args.variant, True):
                    r = row(name, slot, "build failed")
                else:
                    try:
                        r = lane(slot, known.slots > 1, args, out / name,
                                 layouts[is_vanilla(args.variant)][slot].root / "shots" / "lanes" / out.name / name)
                    except Exception as e:  # noqa: BLE001 - one lane's error must not cost the batch its report and checks
                        r = row(name, slot, f"ERROR {e!r}")
                rows[n] = r
                print(report_line(r) + "".join(f"\n   shot {s}" for s in r["shots"]), flush=True)

        started = time.monotonic()
        threads = [threading.Thread(target=worker, args=(k,)) for k in slots]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = round(time.monotonic() - started)
        after = fingerprint()
        # The owner's files are the same for every slot: slot 0's snapshot checks them.
        isolation = subprocess.run([PY, "tools/install_8p.py", *(["--vanilla"] if kinds == [True] else []), "isolation", "verify"],
                                   cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()
        rows = [r or row(lanes[n].name or script_path(lanes[n].script).stem, -1, "ERROR: no result") for n, r in enumerate(rows)]
        report = ["| lane | slot | result | end | invalid | gpu | steps | s |", "|---|---|---|---|---|---|---|---|",
                  *map(report_line, rows), "", f"Lanes: {wall} s on {known.slots} slot(s)",
                  *(f"Shots, slot {k}: {layout.root / 'shots' / 'lanes' / out.name}" for v in kinds for k, layout in layouts[v].items()),
                  f"Tree fingerprint before {before}, after {after}"
                  + ("" if before == after else " (CHANGED during the batch: the builds are the first tree's, the later "
                     "lanes' install_8p.py may not be)"),
                  f"Isolation: {isolation[-1] if isolation else 'no output'}"]
        (out / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report[len(rows) + 3:]), flush=True)
    finally:
        for lock in locks:
            lock.unlink()
    sys.exit(0 if all(r["result"] == "ok" for r in rows) else 1)


if __name__ == "__main__":
    main()
