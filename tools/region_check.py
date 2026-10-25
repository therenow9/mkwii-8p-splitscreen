#!/usr/bin/env python3
"""Checks a build's NTSC-U and NTSC-J binaries (Code.E.bin, Code.J.bin) against its PAL one.

Kamek links every version from one source, translating hook and symbol addresses through
GameSource/versions.txt. An address that file does not map passes through unchanged, and an address
compiled into the code as a number never changes; both build and run, wrongly, on another disc. So
every address in each version is checked against mkw-sp's chunk tables (vendored in WiiCompiled's
projects/mkwii-ntsc-{u,j}/region_port.json), and at every hook the game's own instructions are compared
across the three discs' binaries (extracted to build/regions/{P,E,J}/main.dol and StaticR.rel).

Usage:
  region_check.py [BUILD ...] [--regions DIR] [--wiicompiled DIR]   # BUILD: a build_code.py --out folder
  region_check.py --derive ADDR ...    # an address's E and J ones from the game code that builds it (versions.txt lines)
"""
from __future__ import annotations

import argparse
import bisect
import json
import struct
import sys
from pathlib import Path

import mkwdis

ROOT = Path(__file__).resolve().parent.parent
GAME_LO, GAME_HI = 0x80004000, 0x809C4970  # below is low memory, the same on every disc
REL_BASE = {"P": 0x805102E0, "E": 0x8050BF60, "J": 0x8050FC60}
HOOKS = {32: "write32", 33: "write16", 34: "write8", 35: "condwritepointer", 36: "condwrite32", 37: "condwrite16",
         38: "condwrite8", 64: "branch", 65: "branchlink", 66: "condbranch", 1: "writepointer"}
RELOCS = {1: "addr32", 4: "addr16lo", 5: "addr16hi", 6: "addr16ha", 10: "rel24"}
ARGS = {1: 1, 4: 1, 5: 1, 6: 1, 10: 1, 32: 1, 33: 1, 34: 1, 35: 2, 36: 2, 37: 2, 38: 2, 64: 1, 65: 1, 66: 2}
# D-form opcodes whose 16-bit immediate can finish a lis: addi, ori, addic, and the loads and stores.
LO_OPS = {14, 24, 12, *range(32, 56)}


def writes(n: int) -> int | None:
    """The general register an instruction writes, as far as following a constant needs."""
    op = n >> 26
    if op in (7, 8, 12, 13, 14, 15, *range(32, 36), *range(40, 44), 46):
        return (n >> 21) & 0x1F
    if op in (20, 21, 23, *range(24, 30)):
        return (n >> 16) & 0x1F
    if op in (33, 35, 41, 43, 37, 39, 45, 47, 49, 51, 53, 55):  # update forms also write rA
        return (n >> 16) & 0x1F
    if op == 31:
        xo = (n >> 1) & 0x3FF
        if xo in (0, 32) or xo in (151, 183, 215, 247, 407, 439, 663, 695, 727, 759, 983, 662, 918, 150, 86, 54, 278, 470, 1014):
            return None  # compares, stores, cache ops
        if xo in (28, 60, 124, 284, 316, 412, 444, 476, 24, 536, 792, 824, 26, 922, 954):
            return (n >> 16) & 0x1F
        return (n >> 21) & 0x1F
    return None


def built_addresses(word, lo: int, hi: int):
    """(at, use_at, decode) for each absolute address code builds in a register: lis rD, hi (or addis rD, rX, hi for a
    table indexed by rX, or for x == constant as addis rD, x, -hi then cmplwi rD, lo), followed while rD holds it.
    decode(hi16, lo16) gives the address from the two instructions' immediates."""
    for off in range(lo, hi, 4):
        w = word(off)
        if w >> 26 != 15:
            continue
        rd, indexed = (w >> 21) & 0x1F, (w >> 16) & 0x1F != 0
        for o in range(off + 4, min(off + 4 * 96, hi), 4):
            n = word(o)
            op, ra = n >> 26, (n >> 16) & 0x1F
            if n in (0x4E800020, 0x4E800420):
                break  # blr, bctr: the function ends (a b is followed through: loop bodies sit after it)
            if op == 18 or n == 0x4E800421:  # bl, bctrl: a call keeps only r14-r31
                if rd < 14:
                    break
                continue
            if indexed and op == 10 and ra == rd:
                yield off, o, lambda h, l: (l - (h << 16)) & 0xFFFFFFFF
            elif op == 24 and (n >> 21) & 0x1F == rd and not indexed:
                yield off, o, lambda h, l: (h << 16) | l
            elif op in LO_OPS and op != 24 and ra == rd:
                yield off, o, lambda h, l: ((h << 16) + (l - 0x10000 if l & 0x8000 else l)) & 0xFFFFFFFF
            if writes(n) == rd:
                break


def load_port(path: Path) -> list[tuple[int, int, int]]:
    return [tuple(int(x, 16) for x in c) for c in json.loads(path.read_text())["chunks"]]


def port(chunks, a: int) -> int | None:
    for s, e, t in chunks:
        if s <= a < e:
            return a - s + t
    return None


def game(a: int) -> bool:
    return GAME_LO <= a < GAME_HI


class Build:
    def __init__(self, folder: Path) -> None:
        self.bins = {v: (folder / f"Code.{v}.bin").read_bytes() for v in "PEJ"}
        if len({len(b) for b in self.bins.values()}) != 1:
            raise SystemExit(f"{folder}: the three versions differ in size")
        self.code_size, = struct.unpack_from(">I", self.bins["P"], 12)
        self.names: list[tuple[int, str]] = []
        self.sections: dict[str, tuple[int, int]] = {}
        marks: dict[str, int] = {}
        for line in (folder / "Code.map").read_text().splitlines()[2:]:
            off, _, name = line.split(None, 2)
            if name.startswith(("_f_", "_e_")):
                marks[name] = int(off, 16)
            else:
                self.names.append((int(off, 16), name))
        self.names.sort()
        for sec in ("text", "ctors", "rodata", "data"):
            self.sections[sec] = (marks[f"_f_{sec}"], marks[f"_e_{sec}"])

    def name(self, off: int) -> str:
        i = bisect.bisect_right(self.names, (off, "￿")) - 1
        return f"{self.names[i][1]}+0x{off - self.names[i][0]:X}" if i >= 0 else f"0x{off:X}"

    def word(self, v: str, off: int) -> int:
        return struct.unpack_from(">I", self.bins[v], 0x20 + off)[0]

    def commands(self, v: str):
        """(id, absolute address or None, blob offset or None, args) for each command after the code."""
        b, pos = self.bins[v], 0x20 + self.code_size
        while pos < len(b):
            head, = struct.unpack_from(">I", b, pos)
            pos += 4
            cid, low = head >> 24, head & 0xFFFFFF
            addr = None
            if low == 0xFFFFFE:
                addr, = struct.unpack_from(">I", b, pos)
                pos += 4
            if cid not in ARGS:
                raise SystemExit(f"unknown Kamek command {cid} at 0x{pos - 4:X} ({v})")
            args = struct.unpack_from(f">{ARGS[cid]}I", b, pos)
            pos += 4 * ARGS[cid]
            yield cid, addr, None if addr is not None else low, args


class Checker:
    def __init__(self, regions: Path, wiicompiled: Path) -> None:
        self.ports = {"E": load_port(wiicompiled / "projects" / "mkwii-ntsc-u" / "region_port.json"),
                      "J": load_port(wiicompiled / "projects" / "mkwii-ntsc-j" / "region_port.json")}
        self.images = {}
        for v in "PEJ":
            img = mkwdis.Image()
            mkwdis.load_dol(img, regions / v / "main.dol")
            mkwdis.load_rel(img, regions / v / "StaticR.rel", REL_BASE[v])
            self.images[v] = img
        self.findings: list[str] = []
        self.trusted: set[tuple[int, str]] = set()

    def address(self, where: str, p: int, got: dict[str, int]) -> None:
        """An address Kamek wrote for each version, against mkw-sp's port of the PAL one."""
        for v in "EJ":
            want = port(self.ports[v], p)
            if want is None:
                # mkw-sp maps no .bss and few globals; there RR's own map, used on every disc, is the reference.
                if got[v] == p:
                    self.findings.append(f"{where}: 0x{p:08X} unchanged in {v}, and mkw-sp's map has no counterpart")
                else:
                    self.trusted.add((p, v))
            elif got[v] != want:
                kind = "unchanged" if got[v] == p else "mapped differently"
                self.findings.append(f"{where}: 0x{p:08X} {kind} in {v}: 0x{got[v]:08X}, mkw-sp says 0x{want:08X}")

    def instruction(self, where: str, p: int, got: dict[str, int], stub: dict[str, list[int]] | None = None) -> None:
        """The game's own word at a hook, PAL against each region (branches compared by their ported target). A word
        that differs passes when the hook's stub re-creates each region's own word (stub: its first words per version)."""
        try:
            wp = self.images["P"].read32(p)
        except KeyError:
            return
        for v in "EJ":
            try:
                wv = self.images[v].read32(got[v])
            except KeyError:
                self.findings.append(f"{where}: 0x{got[v]:08X} is outside the {v} binaries")
                continue
            if wp == wv or (game(wp) and port(self.ports[v], wp) == wv):  # the same word, or a ported pointer
                continue
            if stub is not None and wp in stub["P"] and wv in stub[v]:
                continue
            if wp >> 26 == wv >> 26 == 18 and wp & 3 == wv & 3:
                tp = (p + ((wp & 0x03FFFFFC) ^ 0x02000000) - 0x02000000) & 0xFFFFFFFF
                tv = (got[v] + ((wv & 0x03FFFFFC) ^ 0x02000000) - 0x02000000) & 0xFFFFFFFF
                if port(self.ports[v], tp) == tv:
                    continue
            self.findings.append(f"{where}: the game's word at 0x{p:08X} is 0x{wp:08X}, at {v} 0x{got[v]:08X} 0x{wv:08X}")

    def check(self, build: Build) -> None:
        # Code: absolute addresses built in a register.
        lo, hi = build.sections["text"]
        for off, o, use in built_addresses(lambda a: build.word("P", a), lo, hi):
            got = {v: use(build.word(v, off) & 0xFFFF, build.word(v, o) & 0xFFFF) for v in "PEJ"}
            if game(got["P"]):
                self.address(build.name(off), got["P"], got)
        # Data: whole words (vtables of game classes, tables of addresses).
        for sec in ("rodata", "data", "ctors"):
            lo, hi = build.sections[sec]
            for off in range(lo, hi, 4):
                got = {v: build.word(v, off) for v in "PEJ"}
                if game(got["P"]):
                    self.address(build.name(off), got["P"], got)
        # Hooks: the address of each patch, and the game's instruction there.
        cmds = {v: list(build.commands(v)) for v in "PEJ"}
        for i, (cid, ap, off, args) in enumerate(cmds["P"]):
            # A relocation in our code (a call or pointer to a game symbol): its target, as each version resolved it.
            if ap is None and cid in RELOCS and game(args[0]):
                self.address(f"{RELOCS[cid]} at {build.name(off)}", args[0], {v: cmds[v][i][3][0] for v in "PEJ"})
            if ap is None or not game(ap):
                continue
            got = {v: cmds[v][i][1] for v in "PEJ"}
            where = f"{HOOKS.get(cid, cid)} at 0x{ap:08X}"
            self.address(where, ap, got)
            if all(got[v] is not None for v in "EJ"):
                stub = None
                if cid in (64, 65) and args[0] < build.code_size:  # a branch to our code: its first 32 words
                    stub = {v: [build.word(v, args[0] + 4 * k) for k in range(32) if args[0] + 4 * k < build.code_size]
                            for v in "PEJ"}
                self.instruction(where, ap, got, stub)
            for a, (vp, ve, vj) in enumerate(zip(args, cmds["E"][i][3], cmds["J"][i][3])):
                if cid in (1, 35) and a == 0 and game(vp):
                    self.address(f"{where} value", vp, {"P": vp, "E": ve, "J": vj})


def derive(checker: Checker, targets: list[int]) -> None:
    """Each target's address on the other discs, from the game's own code that builds it: every such instruction
    pair is ported by mkw-sp's code chunks and decoded there. Prints the votes and a versions.txt line per region."""
    found: dict[int, list[tuple[int, int, object]]] = {t: [] for t in targets}
    for base, data in checker.images["P"].text_segments():
        words = struct.unpack(f">{len(data) // 4}I", bytes(data[: len(data) // 4 * 4]))
        for off, o, use in built_addresses(lambda a: words[(a - base) // 4], base, base + len(words) * 4):
            a = use(words[(off - base) // 4] & 0xFFFF, words[(o - base) // 4] & 0xFFFF)
            if a in found:
                found[a].append((off, o, use))
    for t in targets:
        line = [f"0x{t:08X}: {len(found[t])} reference(s)"]
        for v in "EJ":
            votes: dict[int, int] = {}
            for off, o, use in found[t]:
                pv, ov = port(checker.ports[v], off), port(checker.ports[v], o)
                if pv is None or ov is None:
                    continue
                wp, wo = checker.images["P"].read32(off), checker.images["P"].read32(o)
                wv, wov = checker.images[v].read32(pv), checker.images[v].read32(ov)
                if wp & 0xFFFF0000 != wv & 0xFFFF0000 or wo & 0xFFFF0000 != wov & 0xFFFF0000:
                    continue  # not the same instructions there
                got = use(wv & 0xFFFF, wov & 0xFFFF)
                votes[got] = votes.get(got, 0) + 1
            if votes:
                best = max(votes, key=votes.get)
                line.append(f"{v} 0x{best:08X} ({votes[best]}/{sum(votes.values())}) -> {t:08x}-{t + 3:08x}: "
                            f"{'+' if best >= t else '-'}0x{abs(best - t):x}")
            else:
                line.append(f"{v} none")
        print("  ".join(line))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("builds", nargs="*", type=Path, default=[ROOT / "build" / "code"])
    ap.add_argument("--regions", type=Path, default=ROOT / "build" / "regions")
    ap.add_argument("--wiicompiled", type=Path, default=ROOT / "external" / "Wiicompiled")
    ap.add_argument("--derive", nargs="+", metavar="ADDR", help="derive these PAL addresses' E and J ones instead")
    args = ap.parse_args()
    checker = Checker(args.regions, args.wiicompiled)
    if args.derive:
        derive(checker, [int(a, 16) for a in args.derive])
        return 0
    total = 0
    for folder in args.builds:
        checker.findings, checker.trusted = [], set()
        checker.check(Build(folder))
        for f in checker.findings:
            print(f"{folder.name}: {f}")
        print(f"{folder.name}: {len(checker.findings)} finding(s); {len(checker.trusted)} address-region pairs "
              "only versions.txt maps")
        total += len(checker.findings)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
