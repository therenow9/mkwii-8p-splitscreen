#!/usr/bin/env python3
"""Unattended smoke tests for the 8P copy: boot a race, restart it, and report.

Each case builds a --debug-boot Code.pul (build_code.py), installs it (install_8p.py code), and
launches Dolphin unattended. The boot races SS8_BOOT_LOCALS players on GC ports 1-4, then Wii Remote
channels 1-4 (cases above 4 locals launch with --wiimotes), and port 1 is
scripted (DebugBoot.cpp): about 25 s into the race it pauses (later in the --boot-respawn and
--boot-use-item cases, after their scripted respawns and items). A case passes when:
  - the race runs to the scripted START (an "ss8 boot: race N press 1000" OSREPORT line),
  - and the log has no invalid memory access by AFTER_PAUSE seconds later. The pause restarts the
    race in 1P; in 2-4P the multiplayer pause menu differs and the script exits to the menus, which
    then show the title's attract demo. The report says which happened.
An autodrive case (--boot-autodrive) gives every local's kart to the AI and presses A once a second
instead of pausing: it passes when all locals finish, the results pass and the next race loads (its
first "ss8 boot: race 3 press" line: "Next Race" loads a one-screen scene first, race 2 to the boot),
with no invalid memory access by AFTER_PAUSE seconds later.
The shipped-build case (locals 0) builds no boot: it launches the plain build, which idles at Retro
Rewind's title ("Press the A Button"; no attract demo started within 75 s when tried), and passes
when the game window is still open AFTER_PAUSE seconds after boot with no invalid memory access.
The log has no title marker, so only the screenshot shows the title.
Each case leaves a screenshot of its race or title. The report goes to <root>/smoke/<time>/.

The sweep boots every track of the 8P copy's ConfigRT.pul and then ConfigCT.pul (--config picks one;
main variants only), debug8 with SWEEP_LOCALS locals, with a shorter watch after the pause: the
attract demo it leads to is not the track. --sweep8 boots them on the normal build with 8 locals instead, which the
boot gives a 5-8 game's memory (menuLocalCount), and every variant too, as its own row keyed variant << 12 | PulsarId
(0x1155 is 0x155's variant 1), which passes only if the race logs that variant. --config BT boots the vanilla arenas and every ConfigBT.pul
arena instead: the shipped build with BATTLE_LOCALS locals in a team Balloon round (--boot-battle),
watched to the round's end ("ss8 boot: race N round end"), where players 5-8 park beside 1-4, and shot
BATTLE_SHOT_AFTER s later. --vanilla runs the cases on the vanilla build and root (install_8p.py --vanilla), on Luigi
Circuit, without the two retro-track cases; its sweep is the menus': each of the cup pages' 32 courses and 10 arenas
(VS, BT, or all with Ghost Valley 2 at 5 locals) driven by a script cut from v-8p-vs-race, v-8p-battle or v-5p-join and
run as a vanilla-menu lane (tools/lane.py), keyed by its cursor (cup << 4 | course, an arena 0x100 + that); it passes
when the lane is ok and its race logged a course.
--slots N runs N tracks at once. A failing track is run once more, so a row says FAIL (both runs) or FLAKY. Rows go to
results.jsonl as each track ends; report.md and a self-refreshing status.html are rewritten with
them. Without --child the process is the supervisor: it keeps the PC awake, optionally runs the
matrix first, and relaunches the sweep (--child --resume) until every track has a row, so a dead
python or a wedged Dolphin costs one track, not the night.

Usage:
  smoke_8p.py                    run every case
  smoke_8p.py d8-3p normal-2p    run these cases
  smoke_8p.py --slots N [case ...]   run the cases on slots 0..N-1 at once (install_8p.py --slot K, made by prepare)
  smoke_8p.py --list             list the cases
  smoke_8p.py --sweep [--sweep8] [--config all|RT|CT|BT] [--slots N] [--matrix] [--tracks 0x100,0x138] [--resume <smoke dir>]
  smoke_8p.py --vanilla [case ...] | --vanilla --sweep [--config all|VS|BT] [--slots N] [--tracks ...] [--resume ...]
  --fast (with any of these) runs every boot case's Dolphin at unlimited speed: the boot counts frames, the timeouts and
  the watches after the pause stay wall-clock, so they cover more of the game.
"""
from __future__ import annotations

import argparse
import ctypes
import html
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(ROOT / "tools"))
import install_8p  # noqa: E402
import lane  # noqa: E402

RACE_TIMEOUT = 300  # launch to the scripted START, s (an 8-view load is slow)
AUTODRIVE_TIMEOUT = 720  # launch, three laps, the results and the next race's load, s
AFTER_PAUSE = 40  # the scripted pause leads to a restart or the menus; watched this long, s

SWEEP_LOCALS = 3
SWEEP_LOAD_TIMEOUT = 180
SWEEP_AFTER_PAUSE = 10
SWEEP_RELAUNCHES = 25  # supervisor: child sweeps started before it gives up
BATTLE_LOCALS = 8
BATTLE_LOAD_TIMEOUT = 900  # launch to the round's end, s: four 8-screen slots at --fast run a 180 s round at about half speed
BATTLE_SHOT_AFTER = 6  # the round's end to the shot, s: page 0x33 parks the karts, then 0x34 waits for an A

VANILLA_SWEEP_TOTAL = 420  # launch to the lane's end, s: the menus, the load and the script's 30 s of race
VANILLA_LUIGI_CIRCUIT = 0x08
GCN_LUIGI_CIRCUIT = 0x138
DS_LUIGIS_MANSION = 0x14B


@dataclass
class Case:
    name: str
    screens: int  # 0 = the normal build
    locals: int  # 0 = no boot: the shipped build, idling at the title
    track: int = 0
    load_timeout: int = RACE_TIMEOUT
    after_pause: int = AFTER_PAUSE
    items: bool = False
    autodrive: bool = False
    respawn: bool = False  # --boot-respawn: players respawn one by one before the pause
    use_item: str = ""  # --boot-use-item: the last racer uses this item before the pause
    pause_by: int = 0  # --boot-pause-by: this local (5-8) drives the pause menu on its Classic Controller
    battle: bool = False  # --boot-battle: a team Balloon round, watched to its end
    track_variant: int = 0  # --boot-variant: the track's variant, 1..its variantCount

    def build_args(self) -> list[str]:
        if self.locals == 0:
            return ["--vanilla"] if vanilla else []
        args = ["--debug-boot", "--boot-locals", str(self.locals), "--boot-track", hex(self.track)]
        args += ["--boot-items"] if self.items else []
        args += ["--boot-autodrive"] if self.autodrive else []
        args += ["--boot-respawn"] if self.respawn else []
        args += ["--boot-use-item", self.use_item] if self.use_item else []
        args += ["--boot-pause-by", str(self.pause_by)] if self.pause_by else []
        args += ["--boot-battle"] if self.battle else []
        args += ["--boot-variant", str(self.track_variant)] if self.track_variant else []
        return (["--vanilla"] if vanilla else []) + (["--debug-screens", str(self.screens)] if self.screens else []) + args

    def variant(self) -> str:
        build = "vanilla" if vanilla else "ss8"
        if self.locals == 0:
            return build
        return f"{build}-debug{self.screens}-boot" if self.screens else f"{build}-boot"


CASES = [
    Case("normal-title", 0, 0),
    Case("normal-1p", 0, 1),
    Case("normal-2p", 0, 2),
    Case("normal-4p", 0, 4),
    Case("normal-6p", 0, 6),
    Case("normal-8p", 0, 8),
    Case("normal-8p-finish", 0, 8, load_timeout=AUTODRIVE_TIMEOUT, autodrive=True),
    Case("normal-8p-pause5", 0, 8, pause_by=5),
    Case("d8-1p", 8, 1),
    Case("d8-2p", 8, 2),
    Case("d8-3p", 8, 3),
    Case("d8-4p", 8, 4),
    Case("d8-3p-gcnlc", 8, 3, GCN_LUIGI_CIRCUIT),
    Case("d8-2p-dslm", 8, 2, DS_LUIGIS_MANSION),
    Case("d6-2p", 6, 2),
    Case("d8-2p-items", 8, 2, items=True),
    Case("d8-5p", 8, 5),
    Case("d6-6p", 6, 6),
    Case("d8-8p", 8, 8),
    Case("d8-8p-finish", 8, 8, load_timeout=AUTODRIVE_TIMEOUT, autodrive=True),
    Case("d8-8p-items", 8, 8, load_timeout=AUTODRIVE_TIMEOUT, items=True, autodrive=True),
    Case("d8-8p-respawn", 8, 8, respawn=True),
    Case("d6-6p-respawn", 6, 6, respawn=True),
    Case("d8-8p-items-thunder", 8, 8, use_item="thunder"),
    Case("d8-5p-items-blooper", 8, 5, use_item="blooper"),
]
# Plain MKW has neither retro track; its CourseId 0 is Mario Circuit, where RR's PulsarId 0 is Luigi Circuit.
VANILLA_CASES = [replace(c, name="v-" + c.name, track=VANILLA_LUIGI_CIRCUIT) for c in CASES if c.track == 0]


def run(args: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run([PY, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def invalid_summary(log: str) -> tuple[int, str]:
    pcs: dict[str, int] = {}
    for m in install_8p.INVALID.finditer(log):
        pcs[m.group(1)] = pcs.get(m.group(1), 0) + 1
    top = ", ".join(f"{pc} x{n}" for pc, n in sorted(pcs.items(), key=lambda kv: -kv[1])[:3])
    gpu = [m.group(1) for m in install_8p.GPU_FIFO.finditer(log)]
    top = ", ".join(filter(None, [top, *(f"GPU opcode {op}" for op in gpu)]))
    return sum(pcs.values()) + len(gpu), top


def symbol(pc: str) -> str:
    """The game function holding `pc`, from mkwdis. Code.pul's addresses (ours and RR's) are not in
    the image, and mkwdis then names whatever symbol precedes them, so a far offset reads as none."""
    try:
        out = run(["tools/mkwdis.py", "fn", pc], timeout=60).stdout.splitlines()
    except Exception:  # noqa: BLE001 - a missing name must not cost the case its report
        return ""
    m = re.match(r"(\S+)\+0x([0-9A-F]+):", out[0]) if out else None
    return f"{m.group(1)}+0x{m.group(2)}" if m and int(m.group(2), 16) < 0x2000 else ""


def game_window_open(shot_stdout: str) -> bool:
    """Whether `install_8p.py shot` captured a game window. Each line ends in the window title's
    repr; only a running game's render window names the game ID. The paths in the same lines hold
    the install root, which itself contains "Mario Kart Wii"."""
    titles = re.findall(r"""^\[shot\] .*  (['"])(.*)\1$""", shot_stdout, re.M)
    return any(re.search(r"\(RMC[PEJ]01\)", title) for _, title in titles)


# Cases share build/'s output directories, each with its own boot defines: a slot builds and installs under this
# lock, and the next case's build may overwrite the output once the copy has it.
building = threading.Lock()
fast = False
sweep_slots = 1
battle_sweep = False
sweep8 = False
vanilla = False


def run_case(case: Case, slot: int, out: Path, tiled: bool = False) -> dict:
    result = {"case": case.name, "status": "FAIL", "detail": "", "invalid": "", "shot": "",
              "load_s": None, "first_invalid": "", "pc": "", "fn": ""}
    layout = install_8p.Layout(install_8p.slot_root(slot, vanilla), vanilla)
    on = ["tools/install_8p.py", "--slot", str(slot)] + (["--vanilla"] if vanilla else [])
    # Nothing is installed or stopped under a Dolphin this run did not start: on slot 0 it can be the owner's game.
    if why := install_8p.foreign_dolphin():
        result["detail"] = why
        return result
    with building:
        build = run(["tools/build_code.py", *case.build_args()])
        if build.returncode != 0:
            tail = (build.stdout + build.stderr).strip().splitlines()
            result["detail"] = "build failed: " + (tail[-1] if tail else "no output")
            return result
        code = run([*on, "code", case.variant()], timeout=120)
        if code.returncode != 0:
            result["detail"] = "install failed"
            return result
    said = []
    # A boot sets RR settings in RAM (custom skins off, a Battle's teams), and RR saves its settings whole, so the
    # file carries them into every later run of this slot unless it is put back; the vanilla game saves its licenses.
    settings = layout.save if vanilla else layout.nand_mod / "RRGameSettings.pul"
    settings_before = settings.read_bytes() if case.locals and settings.exists() else None

    def start() -> bool:
        # Players 5-8 hold Wii Remote channels 1-4 (D24).
        launch = run([*on, "dolphin", "--unattended"] + (["--wiimotes"] if case.locals > 4 else []) +
                     # The title case has no boot to count frames: its 40 s is the wall clock's.
                     (["--fast"] if fast and case.locals else []), timeout=60)
        said.append((launch.stdout + launch.stderr).strip()[-200:])
        return launch.returncode == 0

    rec = install_8p.guarded_launch(layout, start)
    if isinstance(rec, str):
        result["detail"] = rec + (f": {said[0]}" if said else "")
        return result
    launched = time.monotonic()
    if tiled:
        install_8p.place_windows(rec[0], slot)
    game_log = install_8p.LogTail(layout.dolphin_log)
    try:
        # A boot-time line, about 2.5 s after the apploader; the shipped build logs nothing at the title.
        marker = (r"OSIsTitleInstalled\(\w+\) errcode" if case.locals == 0 else r"ss8 boot: race \d+ round end" if case.battle else
                  r"ss8 boot: race 3 press" if case.autodrive else r"ss8 boot: race \d+ press 1000")
        start, why = install_8p.wait_for(layout, game_log, re.compile(marker), 0, case.load_timeout)
        if start is not None:
            result["load_s"] = round(time.monotonic() - launched)
        if case.locals == 0 and start is not None:
            time.sleep(case.after_pause)
        if case.battle and start is not None:
            time.sleep(BATTLE_SHOT_AFTER)
        # The race (behind the opening pause menu) or the title is on screen, or the hang is.
        # shot writes inside its own slot's root only; the PNG is moved into the report's folder.
        shots = run([*on, "shot", "--out", str(layout.root / "smoke" / out.name)], timeout=60)
        # Named by window number, so a refused window 1 never puts window 2 under the case's name.
        for m in re.finditer(r"^\[shot\] (?!refused )(\S.*?-(\d+)\.png)  \d+x\d+", shots.stdout, re.M):
            n = int(m.group(2))
            dst = out / (f"{case.name}.png" if n == 1 else f"{case.name}-window{n}.png")
            shutil.move(m.group(1), dst)
            if n == 1:
                result["shot"] = dst.name
        if start is None:
            log = game_log.read()
            count, top = invalid_summary(log)
            result["invalid"] = f"{count}" + (f" ({top})" if top else "")
            what = ("boot marker" if case.locals == 0 else "round end" if case.battle else "next race" if case.autodrive
                    else "scripted START")
            result["detail"] = f"no {what}: {why} (load hang or crash)"
            note_first_invalid(result, log)
            (out / f"{case.name}.log").write_text(log, encoding="utf-8")
            return result
        if case.locals != 0:
            time.sleep(case.after_pause)
            if not install_8p.dolphin_running(layout):
                result["detail"] = f"Dolphin exited within {case.after_pause} s of the " + (
                    "round end" if case.battle else "next race" if case.autodrive else "scripted START")
                log = game_log.read()
                note_first_invalid(result, log)
                (out / f"{case.name}.log").write_text(log, encoding="utf-8")
                return result
        log = game_log.read()
        after = log[start:]
        # The first scene change is the pause's; the menus' attract demo restarts its own scene later.
        first = re.search(r"Scene (Restart|Exit)", after)
        went = ("stayed in the race" if first is None else
                "restarted" if first.group(1) == "Restart" else "exited to the menus")
        count, top = invalid_summary(log)
        result["invalid"] = f"{count}" + (f" ({top})" if top else "")
        note_first_invalid(result, log)
        # SetWinning races variant 0 for a variant past the track's count, so the race's own line is the check.
        raced = re.search(r"ss8 boot: race \d+ track [0-9a-f]+ variant (\d+)", log)
        raced_variant = int(raced.group(1)) if raced else None
        stopped = False
        if case.locals == 0:
            stopped = not game_window_open(shots.stdout)
            done = "emulation had stopped" if stopped else f"ran {case.after_pause} s past boot"
        elif case.battle:
            done = f"reached the round's end, then ran {case.after_pause} s"
        elif case.autodrive:
            done = f"every local finished, and the next race ran {case.after_pause} s"
        else:
            done = f"raced to the scripted pause, which then {went} ({case.after_pause} s)"
        if count:
            result["detail"] = f"invalid accesses or GPU opcodes; {done}"
        elif stopped:
            result["detail"] = done
        elif case.track_variant and raced_variant != case.track_variant:
            result["detail"] = f"raced variant {raced_variant}, not {case.track_variant}; {done}"
        else:
            result["status"] = "PASS"
            result["detail"] = done
        (out / f"{case.name}.log").write_text(log, encoding="utf-8")
        return result
    finally:
        run([*on, "stop"], timeout=60)
        if settings_before is not None:
            settings.write_bytes(settings_before)


def note_first_invalid(result: dict, log: str) -> None:
    """The first invalid access is the one to read: after it the emulator runs on into more."""
    m = install_8p.INVALID.search(log)
    if m:
        line_start = log.rfind("\n", 0, m.start()) + 1
        line_end = log.find("\n", m.end())
        result["first_invalid"] = log[line_start:line_end if line_end >= 0 else len(log)].strip()
        result["pc"] = m.group(1)
        result["fn"] = symbol(m.group(1))


# The sweep.

def vanilla_sweep(out: Path, config: str) -> list[tuple[int, str]]:
    """(cursor key, name) for the vanilla menu sweep, each row's script written into out/scripts."""
    def cut(script: str, nav: list[str]) -> list[str]:
        lines = [line for line in (ROOT / "tools" / "menu_scripts" / f"{script}.txt").read_text(encoding="utf-8").splitlines()
                 if not line.startswith("#")]
        return (["autodrive"] if "autodrive" not in lines else []) + lines[:lines.index("press 1 a        # cup")] + nav + [
            "press 1 a        # Start: OK", "wait 240", "mash 1 a 1800"]

    def pick(row: int, across: int, down: int) -> list[str]:
        return (["press 1 down", "wait 60"] * row + ["press 1 right", "wait 60"] * across + ["press 1 a        # cup", "wait 240"]
                + ["press 1 down", "wait 60"] * down + ["press 1 a        # course", "wait 240"])

    rows = []
    if config in ("all", "VS"):
        rows += [(c << 4 | k, f"cup {c + 1} course {k + 1}", cut("v-8p-vs-race", pick(c // 4, c % 4, k))) for c in range(8) for k in range(4)]
    if config in ("all", "BT"):
        rows += [(0x100 | c << 4 | k, f"stages {c + 1} arena {k + 1}", cut("v-8p-battle", pick(0, c, k))) for c in range(2) for k in range(5)]
    if config == "all":
        # Ghost Valley 2's Boos are sized by the race's locals.
        rows.append((0x200 | 4 << 4 | 2, "cup 5 course 3, 5 locals", cut("v-5p-join", pick(1, 0, 2))))
    (out / "scripts").mkdir(exist_ok=True)
    for key, name, script in rows:
        (out / "scripts" / f"{key:#x}.txt").write_text("\n".join([f"# vanilla sweep {key:#x}: {name}", *script]) + "\n",
                                                        encoding="utf-8", newline="\n")
    return [(key, name) for key, name, _ in rows]


def vanilla_lane(name: str, key: int, slot: int, out: Path, tiled: bool) -> dict:
    """One vanilla sweep row: its script on the vanilla-menu build, as a lane (tools/lane.py)."""
    result = {"case": name, "status": "FAIL", "detail": "", "invalid": "", "shot": "",
              "load_s": None, "first_invalid": "", "pc": "", "fn": ""}
    args = lane.lane_parser().parse_args(["vanilla-menu", str(out / "scripts" / f"{key:#x}.txt"), "ss8 context:@6", "--no-build",
                                          "--past-race", "--total", str(VANILLA_SWEEP_TOTAL)] + (["--fast"] if fast else []))
    shots = install_8p.slot_root(slot, True) / "shots" / "lanes" / out.name / name
    r = lane.lane(slot, tiled, args, out / name, shots)
    log_file = out / name / "game.log"
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    for png in sorted(shots.glob("01-*.png"))[:1]:
        shutil.move(png, out / f"{name}.png")
        result["shot"] = f"{name}.png"
    raced = re.search(r"ss8 context: gamemode \d+ battle \d+ course (0x[0-9a-f]+|0) ", log)
    result["invalid"] = str(r["invalid"])
    note_first_invalid(result, log)
    if r["result"] == "ok" and raced:
        result["status"] = "PASS"
        result["detail"] = f"raced course {raced.group(1)}; {r['end']}"
    else:
        result["detail"] = f"{r['result']}; {r['end']}" + ("" if raced else "; no race")
    return result


def sweep_tracks(layout, config: str) -> list[tuple[int, str]]:
    """(PulsarId, name) for every track of the 8P copy's Config<config>.pul, or RT then CT for "all",
    read at run time. Pulsar numbers CT's tracks after RT's and BT's after CT's (PulsarSystem.cpp,
    LoadConfigFileNames). BT also takes the ten vanilla arenas, PulsarIds 0x20-0x29 as their CourseIds."""
    if config == "all":
        return sweep_tracks(layout, "RT") + sweep_tracks(layout, "CT")
    sys.path.insert(0, str(ROOT / "external" / "rr-pulsar" / "scripts"))
    sys.dont_write_bytecode = True  # no __pycache__ in the rr-pulsar submodule
    import export_track_slots  # noqa: E402

    binaries = layout.rr / "Binaries"
    before = {"RT": [], "CT": ["RT"], "BT": ["RT", "CT"]}[config]
    first = sum(len(export_track_slots.extract_rows(binaries / f"Config{c}.pul", 0)) for c in before)
    data = (binaries / f"Config{config}.pul").read_bytes()
    rows = export_track_slots.extract_rows(binaries / f"Config{config}.pul", first)
    entries = export_track_slots.parse_track_entries(data, export_track_slots.parse_config_sections(data)[0])
    vanilla = []
    if config == "BT":
        ids = (ROOT / "external" / "rr-pulsar" / "GameSource" / "MarioKartWii" / "System" / "Identifiers.hpp").read_text(encoding="utf-8")
        courses = ids[ids.index("enum CourseId {"):]
        courses = courses[:courses.index("};")]
        vanilla = sorted((int(n, 16), name) for name, n in re.findall(r"^\s*(\w+) = (0x2[0-9a-fA-F]),", courses, re.M))
    return vanilla + [(v << 12 | r.pulsar_id, f"{r.name} v{v}" if v else r.name)
                      for r, e in zip(rows, entries) for v in range(e.variant_count + 1 if sweep8 else 1)]


def read_results(out: Path) -> list[dict]:
    try:
        lines = (out / "results.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass  # a line torn by a killed child; that track runs again
    return rows


def attempt(case: Case, slot: int, out: Path, tiled: bool = False) -> dict:
    try:
        return run_case(case, slot, out, tiled)
    except Exception:  # noqa: BLE001 - one case's failure must not end the run
        try:
            run(["tools/install_8p.py", "--slot", str(slot), "stop"], timeout=60)
        except Exception:  # noqa: BLE001
            pass
        return {"case": case.name, "status": "ERROR", "detail": traceback.format_exc().strip().splitlines()[-1],
                "invalid": "", "shot": "", "load_s": None, "first_invalid": "", "pc": "", "fn": ""}


def rows_for(out: Path, tracks: list[tuple[int, str]]) -> list[dict]:
    """This sweep's rows: a resumed folder can hold another config's tracks."""
    wanted = {pid for pid, _ in tracks}
    return [r for r in read_results(out) if r["track"] in wanted]


def sweep_case(name: str, key: int) -> Case:
    if battle_sweep:
        return Case(name, 0, BATTLE_LOCALS, key, BATTLE_LOAD_TIMEOUT, SWEEP_AFTER_PAUSE, battle=True)
    if sweep8:
        return Case(name, 0, 8, key & 0xFFF, SWEEP_LOAD_TIMEOUT, SWEEP_AFTER_PAUSE, track_variant=key >> 12)
    return Case(name, 8, SWEEP_LOCALS, key, SWEEP_LOAD_TIMEOUT, SWEEP_AFTER_PAUSE)


def sweep_child(out: Path, tracks: list[tuple[int, str]]) -> int:
    done = {r["track"] for r in rows_for(out, tracks)}
    todo = [t for t in tracks if t[0] not in done]
    taking, writing = threading.Lock(), threading.Lock()
    current: dict[str, dict] = {}
    count = [len(done)]

    def worker(slot: int) -> None:
        while True:
            with taking:
                if not todo:
                    return
                pid, name = todo.pop(0)
                count[0] += 1
                n = count[0]
            with writing:
                current[str(slot)] = {"track": pid, "name": name, "since": time.time()}
                (out / "current.json").write_text(json.dumps(current), encoding="utf-8")
                render(out, tracks)
            print(f"[sweep] {n}/{len(tracks)} {pid:#x} {name} on slot {slot} ...", flush=True)
            began = time.monotonic()
            tiled = sweep_slots > 1
            def one(name: str) -> dict:
                return vanilla_lane(name, pid, slot, out, tiled) if vanilla else attempt(sweep_case(name, pid), slot, out, tiled)
            first = one(f"{pid:#x}")
            retry = None
            if first["status"] != "PASS":
                retry = one(f"{pid:#x}-retry")
            if first["status"] == "PASS":
                status = "PASS"
            elif retry["status"] == "PASS":
                status = "FLAKY"
            elif first["status"] == retry["status"] == "ERROR":
                status = "ERROR"
            else:
                status = "FAIL"
            row = {"track": pid, "id": f"{pid:#x}", "name": name, "status": status, "first": first, "retry": retry,
                   "seconds": round(time.monotonic() - began), "finished": time.strftime("%H:%M:%S"), "slot": slot}
            with writing:
                with (out / "results.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(row) + "\n")
            main_run = first if status in ("PASS", "FAIL", "ERROR") else retry
            print(f"[sweep] {pid:#x} {name}: {status} marker at {first['load_s']} s, invalid {first['invalid'] or 0}"
                  f"{(', ' + (first['fn'] or first['pc'])) if first['pc'] else ''} {main_run['detail']}", flush=True)
            with writing:
                current.pop(str(slot), None)
                (out / "current.json").write_text(json.dumps(current), encoding="utf-8")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(sweep_slots)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    (out / "current.json").unlink(missing_ok=True)
    render(out, tracks)
    return 0


def render(out: Path, tracks: list[tuple[int, str]], final: str = "") -> None:
    rows = rows_for(out, tracks)
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    remaining = len(tracks) - len(rows)
    per = sum(r["seconds"] for r in rows) / len(rows) if rows else 0
    eta = time.strftime("%H:%M", time.localtime(time.time() + per * remaining / sweep_slots)) if per and remaining else "-"
    try:
        current = json.loads((out / "current.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        current = {}
    try:
        matrix = (out / "matrix.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        matrix = "not run"
    counts = ", ".join(f"{k} {v}" for k, v in sorted(by_status.items()))

    what = ("the vanilla menus: each course or arena by its cup page's cursor, the locals on their defaults, 30 s of race"
            if vanilla else
            f"the shipped build, {BATTLE_LOCALS} locals, a team Balloon round to its end (timeout {BATTLE_LOAD_TIMEOUT} s), "
            f"shot {BATTLE_SHOT_AFTER} s after it, {SWEEP_AFTER_PAUSE} s watched" if battle_sweep else
            f"the normal build, 8 locals with a 5-8 game's memory, load timeout {SWEEP_LOAD_TIMEOUT} s, 25 s race, "
            f"{SWEEP_AFTER_PAUSE} s after the pause; every variant, keyed variant << 12 | PulsarId" if sweep8 else
            f"debug8, {SWEEP_LOCALS} locals, load timeout {SWEEP_LOAD_TIMEOUT} s, 25 s race, {SWEEP_AFTER_PAUSE} s after "
            f"the pause; main variants only")
    md = [f"# Track sweep {out.name}", "",
          f"{what}; {sweep_slots} slot(s).", "",
          f"{len(rows)}/{len(tracks)} done: {counts or 'none yet'}. Mean {per:.0f} s per track.", "",
          f"Matrix: {matrix}", ""]
    if final:
        md += [final, ""]
    md += ["| track | name | result | to marker s | invalid | first invalid (fn) | detail | shot |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = r["first"]
        detail = f["detail"] + (f"; retry: {r['retry']['status']} {r['retry']['detail']}" if r["retry"] else "")
        md.append(f"| {r['id']} | {r['name']} | {r['status']} | {f['load_s'] if f['load_s'] is not None else '-'} | "
                  f"{f['invalid'] or 0} | {f['first_invalid']} {('(' + f['fn'] + ')') if f['fn'] else ''} | "
                  f"{detail} | {f['shot']} |")
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")

    colour = {"PASS": "#2e7d32", "FAIL": "#c62828", "FLAKY": "#ef6c00", "ERROR": "#6a1b9a"}
    e = html.escape
    now = ""
    if current:
        now = "<p class=now>Now: " + "; ".join(
            f"slot {e(k)}: <b>{e(hex(c['track']))} {e(c['name'])}</b> since {time.strftime('%H:%M:%S', time.localtime(c['since']))}"
            for k, c in sorted(current.items())) + "</p>"
    latest = next((s for r in reversed(rows) if (s := r["first"]["shot"] or (r["retry"] or {}).get("shot", ""))), "")
    fails = [r for r in rows if r["status"] != "PASS"]

    def thumb(shot: str) -> str:
        return f'<a href="{e(shot)}"><img src="{e(shot)}"></a>' if shot else ""

    fail_rows = "".join(
        f"<tr><td>{e(r['id'])}</td><td>{e(r['name'])}</td><td style='color:{colour[r['status']]}'>{r['status']}</td>"
        f"<td>{e(r['first']['first_invalid'])}<br><small>{e(r['first']['fn'])}</small></td>"
        f"<td>{e(r['first']['detail'])}</td>"
        f"<td>{thumb(r['first']['shot'])}</td></tr>"
        for r in fails)
    # A black or wall-facing spare tile passes, so every track's frame is on the page to be looked at.
    grid = "".join(
        f"<figure>{thumb(r['first']['shot'] or (r['retry'] or {}).get('shot', ''))}"
        f"<figcaption>{e(r['id'])} {e(r['name'])}</figcaption></figure>" for r in rows)
    all_rows = "".join(
        f"<tr><td>{e(r['id'])}</td><td>{e(r['name'])}</td><td style='color:{colour[r['status']]}'>{r['status']}</td>"
        f"<td>{r['first']['load_s'] if r['first']['load_s'] is not None else '-'}</td><td>{r['seconds']}</td>"
        f"<td>{e(r['finished'])}</td></tr>" for r in reversed(rows))
    refresh = "" if final else '<meta http-equiv="refresh" content="15">'
    page = f"""<!doctype html><html><head><meta charset="utf-8">{refresh}<title>SS8 track sweep</title>
<style>
:root{{--bg:#fafafa;--fg:#1b1b1b;--line:#ddd}}
@media (prefers-color-scheme: dark){{:root{{--bg:#161616;--fg:#e8e8e8;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;margin:16px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}} td,th{{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}}
img{{max-width:240px}} .big{{font-size:20px}} .now{{font-size:16px}} .latest img{{max-width:100%;width:640px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}} figure{{margin:0}} figcaption{{font-size:12px}}
</style></head><body>
<h1>SS8 track sweep {e(out.name)}</h1>
<p class=big>{len(rows)}/{len(tracks)} done · {e(counts or 'none yet')} · mean {per:.0f} s/track · ETA {eta}</p>
{now}
<p>{e(final) if final else 'Refreshes every 15 s.'} Matrix: {e(matrix)}. Full table: report.md.</p>
<h2>Not passing ({len(fails)})</h2>
<table><tr><th>track</th><th>name</th><th>result</th><th>first invalid access</th><th>detail</th><th>shot</th></tr>{fail_rows}</table>
<h2>Latest screenshot</h2><p class=latest>{'<img src="' + e(latest) + '">' if latest else 'none yet'}</p>
<h2>Every track's frame, in sweep order</h2><div class=grid>{grid}</div>
<h2>All tracks, newest first</h2>
<table><tr><th>track</th><th>name</th><th>result</th><th>to marker s</th><th>total s</th><th>finished</th></tr>{all_rows}</table>
</body></html>
"""
    (out / "status.html").write_text(page, encoding="utf-8", newline="\n")


def keep_awake() -> None:
    """Keeps Windows from sleeping or blanking the display while this process lives."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def supervise(layout, out: Path, tracks: list[tuple[int, str]], args) -> int:
    keep_awake()
    sup = out / "supervisor.log"

    def note(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(f"[supervisor] {line}", flush=True)
        with sup.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")

    render(out, tracks)
    if args.matrix and not (out / "matrix.txt").exists():
        note("matrix: the cases first")
        proc = subprocess.Popen([PY, str(Path(__file__)), "--slots", str(sweep_slots)] + (["--fast"] if fast else []), cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        report = ""
        for line in proc.stdout:
            print(line, end="", flush=True)
            m = re.match(r"\[smoke\] report: (.*)", line.strip())
            if m:
                report = m.group(1)
        rc = proc.wait()
        (out / "matrix.txt").write_text(f"{'all pass' if rc == 0 else 'FAILURES'}, {report}\n", encoding="utf-8")
        note(f"matrix done: rc {rc}, {report}")

    def child(slots: int, only: list[int]) -> list[str]:
        cmd = [PY, str(Path(__file__)), "--sweep", "--child", "--config", args.config, "--slots", str(slots), "--resume", str(out)]
        only_arg = ",".join(f"{t:#x}" for t in only) or args.tracks
        return cmd + (["--tracks", only_arg] if only_arg else []) + (["--fast"] if fast else []) + (["--sweep8"] if sweep8 else []) + (
            ["--vanilla"] if vanilla else [])

    deaths: dict[int, int] = {}
    solo: list[int] = []
    for n in range(1, SWEEP_RELAUNCHES + 1):
        if len({r["track"] for r in rows_for(out, tracks)}) >= len(tracks):
            break
        note(f"sweep child {n} starting" + (" on one slot for " + ", ".join(f"{t:#x}" for t in solo) if solo else ""))
        rc = subprocess.run(child(1, solo) if solo else child(sweep_slots, []), cwd=ROOT).returncode
        for k in range(sweep_slots):
            run(["tools/install_8p.py", "--slot", str(k)] + (["--vanilla"] if vanilla else []) + ["stop"], timeout=60)
        try:
            current = json.loads((out / "current.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            current = {}
        note(f"sweep child {n} exited rc {rc}" + (" during " + ", ".join(f"{c['track']:#x}" for c in current.values()) if current else ""))
        # A death with several tracks in flight names none of them: those run again one at a time.
        if len(current) > 1:
            solo = [c["track"] for c in current.values()]
            continue
        solo = [t for t in solo if t not in {r["track"] for r in rows_for(out, tracks)}]
        for c in current.values():
            pid = c["track"]
            deaths[pid] = deaths.get(pid, 0) + 1
            # A track that takes the child down twice gets a row, so it cannot spend every relaunch.
            if deaths[pid] >= 2:
                lost = {"case": f"{pid:#x}", "status": "ERROR", "detail": "the sweep child died on this track twice",
                        "invalid": "", "shot": "", "load_s": None, "first_invalid": "", "pc": "", "fn": ""}
                row = {"track": pid, "id": f"{pid:#x}", "name": c["name"], "status": "ERROR", "first": lost,
                       "retry": None, "seconds": 0, "finished": time.strftime("%H:%M:%S")}
                with (out / "results.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                    f.write("\n" + json.dumps(row) + "\n")
                note(f"{pid:#x} given an ERROR row after two child deaths")
    done = len({r["track"] for r in rows_for(out, tracks)})
    iso = run(["tools/install_8p.py"] + (["--vanilla"] if vanilla else []) + ["isolation", "verify"], timeout=120)
    iso_line = (iso.stdout.strip().splitlines() or ["[isolation] no output"])[-1]
    final = f"Finished {time.strftime('%Y-%m-%d %H:%M')}: {done}/{len(tracks)} tracks. {iso_line}"
    note(final)
    (out / "current.json").unlink(missing_ok=True)
    render(out, tracks, final)
    (out / "sweep.done").write_text(final + "\n", encoding="utf-8")
    rows = rows_for(out, tracks)
    return 0 if done >= len(tracks) and all(r["status"] == "PASS" for r in rows) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases", nargs="*", help="case names (default: all)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sweep", action="store_true", help="boot every ConfigRT.pul and ConfigCT.pul track (see above)")
    ap.add_argument("--sweep8", action="store_true",
                    help="sweep: 8 locals on the normal build with a 5-8 game's memory, and every track variant")
    ap.add_argument("--matrix", action="store_true", help="sweep: run the case matrix first")
    ap.add_argument("--tracks", help="sweep: only these PulsarIds (with --sweep8, variant << 12 | PulsarId), comma-separated (0x138,0x14b)")
    ap.add_argument("--resume", help="sweep: continue in this smoke directory")
    ap.add_argument("--config", choices=("all", "RT", "CT", "BT", "VS"), default="all",
                    help="sweep: ConfigRT.pul (retro), ConfigCT.pul (custom), both, or the arenas (vanilla and ConfigBT.pul); "
                         "with --vanilla: VS (the 32 courses), BT (the 10 arenas) or all")
    ap.add_argument("--slots", type=int, default=1, choices=range(1, install_8p.TILES + 1),
                    help="cases or sweep tracks at once, on slots 0..N-1")
    ap.add_argument("--fast", action="store_true", help="unlimited emulation speed (D171)")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--vanilla", action="store_true", help="the vanilla build on install_8p.py's --vanilla root and slots")
    args = ap.parse_args()
    global fast, sweep_slots, battle_sweep, sweep8, vanilla
    fast, sweep_slots, battle_sweep, sweep8, vanilla = args.fast, args.slots, args.config == "BT", args.sweep8, args.vanilla
    if vanilla and (sweep8 or args.config in ("RT", "CT") or args.matrix):
        ap.error("--vanilla sweeps the menus' courses and arenas: --config all, VS or BT, no --sweep8 or --matrix")
    if not vanilla and args.config == "VS":
        ap.error("--config VS is the --vanilla sweep's")
    layout = install_8p.Layout(install_8p.slot_root(0, vanilla), vanilla)
    all_cases = VANILLA_CASES if vanilla else CASES
    if args.list:
        for c in all_cases:
            print(f"{c.name:16} {c.variant():20} locals {c.locals}  track {c.track:#x}")
        return 0
    slot_layouts = [install_8p.Layout(install_8p.slot_root(k, vanilla), vanilla) for k in range(args.slots)]
    for k, slot_layout in enumerate(slot_layouts):
        if not slot_layout.launch.exists():
            ap.error(f"slot {k} is not prepared: install_8p.py{' --vanilla' if vanilla else ''} --slot {k} prepare")

    if args.sweep:
        out = Path(args.resume) if args.resume else layout.root / "smoke" / (time.strftime("%Y%m%d-%H%M%S") + (
            "-vsweep" if vanilla else "-sweep8" if sweep8 else "-sweep") + ("" if args.config == "all" else "-" + args.config.lower()))
        out.mkdir(parents=True, exist_ok=True)
        tracks = vanilla_sweep(out, args.config) if vanilla else sweep_tracks(layout, args.config)
        if args.tracks:
            wanted = [int(t, 16) for t in args.tracks.split(",")]
            names = dict(tracks)
            tracks = [(t, names.get(t, f"(not in Config{args.config})")) for t in wanted]
        if args.child:
            # The child installs and launches on slots 0..N-1; the supervisor that waits on it takes no lock, so its
            # matrix child can.
            locks = [install_8p.lock_slot(slot_layout) for slot_layout in slot_layouts]
            try:
                return sweep_child(out, tracks)
            finally:
                for lock in locks:
                    lock.unlink()
        print(f"[sweep] {len(tracks)} tracks; status page: {out / 'status.html'}", flush=True)
        # Every row installs the one vanilla-menu build; its script is the row's.
        if vanilla and run(["tools/build_code.py", "--vanilla", "--menu-script"]).returncode != 0:
            ap.error("the vanilla menu build failed: tools/build_code.py --vanilla --menu-script")
        return supervise(layout, out, tracks, args)

    by_name = {c.name: c for c in all_cases}
    unknown = [n for n in args.cases if n not in by_name]
    if unknown:
        ap.error(f"unknown case(s): {', '.join(unknown)}; --list shows them")
    cases = [by_name[n] for n in args.cases] if args.cases else all_cases

    if why := install_8p.foreign_dolphin():
        print(f"[smoke] {why}", file=sys.stderr)
        return 1
    locks = [install_8p.lock_slot(slot_layout) for slot_layout in slot_layouts]
    out = layout.root / "smoke" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict | None] = [None] * len(cases)
    todo = list(enumerate(cases))
    taking = threading.Lock()

    def worker(slot: int) -> None:
        while True:
            with taking:
                if not todo:
                    return
                n, case = todo.pop(0)
            print(f"[smoke] {case.name} on slot {slot} ...", flush=True)
            r = attempt(case, slot, out, args.slots > 1)
            r["slot"] = slot
            print(f"[smoke] {case.name}: {r['status']} {r['detail']} {r['invalid']}", flush=True)
            results[n] = r

    started = time.monotonic()
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(args.slots)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        for lock in locks:
            lock.unlink()
    wall = round(time.monotonic() - started)

    lines = ["| case | slot | result | detail | invalid accesses, GPU opcodes | screenshot |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['slot']} | {r['status']} | {r['detail']} | {r['invalid']} | {r['shot']} |" for r in results]
    # The owner's files are the same for every slot: slot 0's snapshot checks them.
    iso = run(["tools/install_8p.py"] + (["--vanilla"] if vanilla else []) + ["isolation", "verify"], timeout=120)
    lines += ["", f"Cases: {wall} s on {args.slots} slot(s)" + (", --fast" if fast else ""), (iso.stdout.strip().splitlines() or ["[isolation] no output"])[-1]]
    report = "\n".join(lines)
    (out / "report.md").write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"[smoke] report: {out / 'report.md'}")
    return 0 if all(r["status"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
