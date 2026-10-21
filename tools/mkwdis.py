#!/usr/bin/env python3
"""PAL (RMCP01) Mario Kart Wii disassembler for reverse-engineering hook sites.

Loads main.dol and StaticR.rel (relocated at 0x805102E0, the address WiiCompiled
and Pulsar assume), applies the REL relocations so cross-module branches resolve,
and labels addresses with WiiCompiled's MAP.txt plus Pulsar's symbols.txt.

The game files are read from the local WiiCompiled install (never committed).

Usage:
  mkwdis.py fn <name-or-addr> [--max N]   disassemble a whole function (until the next symbol)
  mkwdis.py dis <addr> [count]            disassemble <count> instructions
  mkwdis.py sym <regex>                   search symbols
  mkwdis.py xref <addr>                   find bl/b instructions targeting <addr>
  mkwdis.py imm <value>                   find instructions using an immediate or displacement (cmpwi rX, 4; lwz 0x1c(rY))
  mkwdis.py dref <addr> [--span N]        find data references: lis-based loads/stores/addresses, r2/r13, data words
  mkwdis.py bits --field 4 --width 32 --bits 28-31    every test, set and clear of a field's bits, typed by owner
  mkwdis.py bits --field 0x48:4 --width 16            the same through a pointer field ([+0x48]+4)
  mkwdis.py bits --ctors --bits 28-31                 the bitfield argument of every ModelDirector constructor call
"""
from __future__ import annotations

import argparse
import bisect
import os
import re
import struct
import sys
from pathlib import Path

import capstone

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = Path(os.environ.get("APPDATA", "")) / "CT-MKWII" / "Recomp" / "Install" / "BuildWorkspace"
WORKSPACE = Path(os.environ.get("MKW_WORKSPACE", DEFAULT_WORKSPACE))
DOL_PATH = WORKSPACE / "Assets" / "main.dol"
REL_PATH = WORKSPACE / "Assets" / "StaticR.rel"
MAP_PATH = WORKSPACE / "projects" / "mkwii" / "MAP.txt"
PULSAR_SYMS = ROOT / "external" / "rr-pulsar" / "GameSource" / "symbols.txt"
REL_BASE = 0x805102E0


class Image:
    """Sparse big-endian memory image built from the DOL and the relocated REL."""

    def __init__(self) -> None:
        self.segments: list[tuple[int, bytearray, bool]] = []  # (addr, data, is_text)
        self.entry = 0

    def add(self, addr: int, data: bytes, is_text: bool) -> None:
        self.segments.append((addr, bytearray(data), is_text))

    def find(self, addr: int):
        for base, data, is_text in self.segments:
            if base <= addr < base + len(data):
                return base, data, is_text
        return None

    def read(self, addr: int, size: int) -> bytes:
        seg = self.find(addr)
        if seg is None:
            raise KeyError(f"0x{addr:08X} is not mapped")
        base, data, _ = seg
        return bytes(data[addr - base: addr - base + size])

    def write32(self, addr: int, value: int) -> None:
        base, data, _ = self.find(addr)
        struct.pack_into(">I", data, addr - base, value & 0xFFFFFFFF)

    def read32(self, addr: int) -> int:
        return struct.unpack(">I", self.read(addr, 4))[0]

    def text_segments(self):
        return [(b, d) for b, d, t in self.segments if t]


def load_dol(img: Image, path: Path) -> None:
    raw = path.read_bytes()
    offs = struct.unpack(">18I", raw[0x00:0x48])
    addrs = struct.unpack(">18I", raw[0x48:0x90])
    sizes = struct.unpack(">18I", raw[0x90:0xD8])
    img.entry, = struct.unpack_from(">I", raw, 0xE0)
    for i in range(18):
        if sizes[i]:
            img.add(addrs[i], raw[offs[i]:offs[i] + sizes[i]], i < 7)


def load_rel(img: Image, path: Path, base: int) -> None:
    raw = path.read_bytes()
    module_id, = struct.unpack_from(">I", raw, 0x00)
    num_sections, section_table = struct.unpack_from(">II", raw, 0x0C)
    imp_offset, imp_size = struct.unpack_from(">II", raw, 0x28)
    bss_size, = struct.unpack_from(">I", raw, 0x20)

    section_addr: dict[int, int] = {}
    bss_index = None
    for i in range(num_sections):
        off, size = struct.unpack_from(">II", raw, section_table + i * 8)
        is_exec = off & 1
        off &= ~3
        if size == 0:
            continue
        if off == 0:
            bss_index = i
            continue
        addr = base + off
        section_addr[i] = addr
        img.add(addr, raw[off:off + size], bool(is_exec))
    if bss_index is not None:
        # BSS is a separate heap block after the module, past its 0x20 header; matches WiiCompiled's
        # RelFile.CalculateBssOffset and Pulsar's symbols.txt (Racedata::sInstance 0x809BD728).
        bss_addr = base + ((len(raw) + 0x20 + 0x1F) & ~0x1F)
        section_addr[bss_index] = bss_addr
        img.add(bss_addr, bytes(bss_size), False)

    # Apply relocations so branch targets into main.dol and the REL itself are correct.
    for k in range(imp_size // 8):
        imp_module, rel_off = struct.unpack_from(">II", raw, imp_offset + k * 8)
        pos = rel_off
        cur = 0
        while True:
            offset, rtype, section, addend = struct.unpack_from(">HBBI", raw, pos)
            pos += 8
            if rtype == 203:  # R_DOLPHIN_END
                break
            if rtype == 202:  # R_DOLPHIN_SECTION
                cur = section_addr.get(section, 0)
                continue
            cur += offset
            if rtype == 201:  # R_DOLPHIN_NOP
                continue
            if imp_module == module_id:
                target = section_addr.get(section, 0) + addend
            else:  # module 0: main.dol, absolute addresses
                target = addend
            try:
                word = img.read32(cur & ~3)
            except KeyError:
                continue
            if rtype == 1:  # R_PPC_ADDR32
                img.write32(cur, target)
            elif rtype == 4:  # ADDR16_LO
                _patch16(img, cur, target & 0xFFFF)
            elif rtype == 5:  # ADDR16_HI
                _patch16(img, cur, (target >> 16) & 0xFFFF)
            elif rtype == 6:  # ADDR16_HA
                _patch16(img, cur, ((target + 0x8000) >> 16) & 0xFFFF)
            elif rtype == 10:  # R_PPC_REL24
                disp = (target - cur) & 0x03FFFFFC
                img.write32(cur, (word & 0xFC000003) | disp)
            elif rtype == 2:  # ADDR24
                img.write32(cur, (word & 0xFC000003) | (target & 0x03FFFFFC))
            elif rtype == 11:  # REL14
                disp = (target - cur) & 0xFFFC
                img.write32(cur, (word & 0xFFFF0003) | disp)


def _patch16(img: Image, addr: int, value: int) -> None:
    base, data, _ = img.find(addr)
    struct.pack_into(">H", data, addr - base, value)


class Symbols:
    def __init__(self) -> None:
        self.by_addr: dict[int, str] = {}

    def load_map(self, path: Path) -> None:
        if not path.exists():
            return
        for line in path.read_text(errors="replace").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{8}", parts[0]):
                name = parts[1].strip()
                addr = int(parts[0], 16)
                if not name.startswith("0x") or addr not in self.by_addr:
                    self.by_addr[addr] = name

    def load_pulsar(self, path: Path) -> None:
        if not path.exists():
            return
        for line in path.read_text(errors="replace").splitlines():
            m = re.match(r"\s*([^#=\s][^=]*?)\s*=\s*0x([0-9a-fA-F]{8})", line)
            if m:
                addr = int(m.group(2), 16)
                cur = self.by_addr.get(addr)
                if cur is None or cur.startswith("0x"):
                    self.by_addr[addr] = m.group(1)

    def finalize(self) -> None:
        self.addrs = sorted(self.by_addr)

    def name(self, addr: int) -> str | None:
        return self.by_addr.get(addr)

    def describe(self, addr: int) -> str:
        i = bisect.bisect_right(self.addrs, addr) - 1
        if i < 0:
            return f"0x{addr:08X}"
        base = self.addrs[i]
        off = addr - base
        return self.by_addr[base] + (f"+0x{off:X}" if off else "")

    def lookup(self, text: str) -> int:
        if re.fullmatch(r"(0x)?[0-9a-fA-F]{8}", text):
            return int(text, 16)
        for addr, name in self.by_addr.items():
            if name == text:
                return addr
        hits = [(a, n) for a, n in self.by_addr.items() if text in n]
        if len(hits) == 1:
            return hits[0][0]
        raise SystemExit(f"ambiguous or unknown symbol {text!r}: {[n for _, n in hits[:10]]}")

    def next_after(self, addr: int) -> int | None:
        i = bisect.bisect_right(self.addrs, addr)
        return self.addrs[i] if i < len(self.addrs) else None


def load_all():
    img = Image()
    if not DOL_PATH.exists():
        raise SystemExit(f"main.dol not found at {DOL_PATH} (set MKW_WORKSPACE)")
    load_dol(img, DOL_PATH)
    load_rel(img, REL_PATH, REL_BASE)
    syms = Symbols()
    syms.load_map(MAP_PATH)
    syms.load_pulsar(PULSAR_SYMS)
    syms.finalize()
    Fn.load_fns(syms)
    return img, syms


def make_cs():
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN | capstone.CS_MODE_PS)
    return md


def fmt_insn(insn, syms: Symbols) -> str:
    op = insn.op_str
    if insn.mnemonic.startswith("b") and re.search(r"0x[0-9a-f]+$", op):
        m = re.search(r"0x([0-9a-f]+)$", op)
        tgt = int(m.group(1), 16)
        op = op + f"  <{syms.describe(tgt)}>"
    return f"{insn.address:08X}: {insn.bytes.hex()}  {insn.mnemonic:<8} {op}"


def iter_insns(md, base: int, data: bytearray, lo: int = 0, hi: int = 0xFFFFFFFF):
    """Every decodable instruction in [lo, hi]. Capstone's disasm stops at the first word it cannot
    decode (data in .text, some paired-single forms), so restart one word past each stop."""
    start = max(0, lo - base) & ~3
    end = min(len(data), hi - base + 4) if hi >= base else 0
    for chunk in range(start, end, 0x1000):
        off, stop = chunk, min(chunk + 0x1000, end)
        while off < stop:
            last = off - 4
            for insn in md.disasm(bytes(data[off:stop]), base + off):
                yield insn
                last = insn.address - base
            off = last + 8


_OPERAND = re.compile(r"(-?(?:0x[0-9a-f]+|\d+))(?:\((r\d+)\))?$")


def operand(text: str):
    """(value, base register or None) for an immediate or displacement operand, else None."""
    m = _OPERAND.fullmatch(text)
    return (int(m.group(1), 0), m.group(2)) if m else None


def find_sda_bases(img: Image, md) -> dict[str, int]:
    """r2/r13 as set once by __init_registers: `lis rN, hi` then `ori|addi rN, rN, lo`. Nothing
    else in the game writes them, so the first such pair in the entry's text segment is the base."""
    found: dict[str, int] = {}
    pending: dict[str, int] = {}
    base, data, _ = img.find(img.entry)
    for insn in iter_insns(md, base, data):
        ops = insn.op_str.split(", ")
        if not ops or ops[0] not in ("r2", "r13") or ops[0] in found:
            continue
        if insn.mnemonic == "lis":
            pending[ops[0]] = (int(ops[1], 0) << 16) & 0xFFFFFFFF
        elif insn.mnemonic in ("ori", "addi") and ops[1] == ops[0] and ops[0] in pending:
            hi, lo = pending.pop(ops[0]), int(ops[2], 0)
            found[ops[0]] = (hi + lo if insn.mnemonic == "addi" else hi | lo) & 0xFFFFFFFF
        if len(found) == 2:
            break
    return found


# First operand is a source, or there is no register destination.
_NO_DEST = ("st", "cmp", "b", "mt", "tw", "dcb", "icb", "sync", "isync", "psq_st", "nop")
_VOLATILE = ("r0", *(f"r{i}" for i in range(3, 13)))


def dref(img: Image, syms: Symbols, target: int, span: int) -> None:
    """Data references to [target, target+span): lis-based address arithmetic followed through
    addi/ori/addis into loads, stores and address-taking, r2/r13 SDA accesses, and data words."""
    md = make_cs()
    sda = find_sda_bases(img, md)
    print(f"; SDA bases {', '.join(f'{r}=0x{v:08X}' for r, v in sda.items())}")
    hit = lambda ea: target <= ea < target + span
    for base, data in img.text_segments():
        regs: dict[str, int] = dict(sda)
        for insn in iter_insns(md, base, data):
            a, mn = insn.address, insn.mnemonic
            if syms.name(a):
                regs = dict(sda)
            ops = insn.op_str.split(", ")
            note = None
            if mn == "lis":
                regs[ops[0]] = (int(ops[1], 0) << 16) & 0xFFFFFFFF
                continue
            if mn in ("addi", "ori", "addis") and len(ops) == 3 and ops[1] in regs:
                imm = int(ops[2], 0)
                v = {"addi": regs[ops[1]] + imm, "ori": regs[ops[1]] | imm,
                     "addis": regs[ops[1]] + (imm << 16)}[mn] & 0xFFFFFFFF
                if ops[0] not in sda:
                    regs[ops[0]] = v
                if hit(v):
                    note = f"address 0x{v:08X}"
            else:
                mem = operand(ops[-1]) if ops else None
                if mem and mem[1] in regs:
                    ea = (regs[mem[1]] + mem[0]) & 0xFFFFFFFF
                    if hit(ea):
                        note = f"{'load' if mn.startswith('l') else 'store'} 0x{ea:08X}"
                    if mn.endswith("u") or mn.endswith("ux"):
                        regs.pop(mem[1], None)
                if ops and ops[0] in regs and ops[0] not in sda and not mn.startswith(_NO_DEST):
                    regs.pop(ops[0], None)
            if note:
                print(f"{fmt_insn(insn, syms)}    ; {note} in {syms.describe(a)}")
            if mn in ("blr", "bctr", "rfi"):
                regs = dict(sda)
            elif mn in ("bl", "bctrl", "blrl"):
                for r in _VOLATILE:
                    regs.pop(r, None)
    for base, data, is_text in img.segments:
        if is_text:
            continue
        for off in range(0, len(data) - 3, 4):
            if hit(struct.unpack_from(">I", data, off)[0]):
                a = base + off
                print(f"{a:08X}: data word 0x{struct.unpack_from('>I', data, off)[0]:08X}    ; in {syms.describe(a)}")


# The field scanner decodes instruction words itself: capstone prints rlwinm under several aliases
# (srwi, clrlwi, extrwi, ...) and has no register-access data for PPC.
REL_TEXT = 0x805102E0  # StaticR.rel: all game classes. main.dol text below it is libraries and boot code.
_FAMILY = re.compile(r"^(?!ModelDirectorPair)\w*ModelDirector::")
# Static members: their r3 is a ResFile or a name, not a director, so a call to one types nothing.
_STATIC = re.compile(r"::(GetBRRES|BindBRRES\w*|RipAndBindBRRES|BRRESExists|GetResMdl|MdlExists|HasResMdl)\b")
_DIRECTOR_CTORS = {0x8055B298: 6, 0x8055B50C: 5, 0x8055B558: 6}  # ctor -> bitfield argument register
# Read first-hand: ShadowModelDirector's ctors OR 4 (and 0x20 with a ClipInfo) into the caller's bitfield
# argument before the base ctor, so the scan resolves their callers' r6 / r4 too.
_DIRECTOR_WRAPPERS = {0x805B8984: 6, 0x805B89F4: 4}
_LOADS = {32: 4, 33: 4, 34: 1, 35: 1, 40: 2, 41: 2, 42: 2, 43: 2}  # lwz(u), lbz(u), lhz(u), lha(u)
_STORES = {36: 4, 37: 4, 38: 1, 39: 1, 44: 2, 45: 2}  # stw(u), stb(u), sth(u)
_SKIP_BASES = (1, 2, 13)  # stack and small-data bases never hold an object pointer


def _mask(mb: int, me: int) -> int:
    if mb <= me:
        return sum(1 << (31 - b) for b in range(mb, me + 1))
    return 0xFFFFFFFF if mb == me + 1 else ~_mask(me + 1, mb - 1) & 0xFFFFFFFF


def _rotl(x: int, n: int) -> int:
    n &= 31
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF if n else x


def _bits(mask: int) -> str:
    bits = [b for b in range(32) if mask >> b & 1]
    if not bits:
        return "-"
    runs, start = [], bits[0]
    for prev, b in zip(bits, bits[1:] + [None]):
        if b != prev + 1:
            runs.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = b
    return ",".join(runs)


class Fn:
    """One function's instructions with the linear reaching-definition queries the scanner types by."""

    # _save_gpr_N / _restore_gpr_N (prologue and epilogue helpers) leave r3-r10 alone; set by load_fns.
    keeps_args: set[int] = set()

    @staticmethod
    def load_fns(syms: Symbols) -> None:
        Fn.keeps_args = {a for a, n in syms.by_addr.items() if re.match(r"_(save|rest(ore)?)_?[gf]pr", n)}

    def __init__(self, img: Image, md, start: int, end: int) -> None:
        self.start = start
        base, data, _ = img.find(start)
        self.insns = list(iter_insns(md, base, data, start, end - 4))
        self.words = [struct.unpack(">I", bytes(i.bytes))[0] for i in self.insns]
        self.writebacks: set[int] = set()  # stores already reported as a tracked value going back

    def defs(self, k: int) -> set[int]:
        """GPRs instruction k writes."""
        w, insn = self.words[k], self.insns[k]
        op = w >> 26
        if op == 18 and w & 1 or op == 19 and w & 1:  # bl, bctrl, blrl: the volatile registers
            return set() if self.call_target(k) in Fn.keeps_args else {0, *range(3, 13)}
        if op == 46:  # lmw
            return set(range((w >> 21) & 31, 32))
        out = set()
        if op in (33, 35, 37, 39, 41, 43, 45, 49, 51, 53, 55):  # update forms write the base
            out.add((w >> 16) & 31)
        ops = insn.op_str.split(", ")
        if ops and re.fullmatch(r"r\d+", ops[0]) and not insn.mnemonic.startswith(_NO_DEST):
            out.add(int(ops[0][1:]))
        return out

    def lastdef(self, reg: int, k: int) -> int | None:
        for j in range(k - 1, -1, -1):
            if reg in self.defs(j):
                return j
        return None

    def ident(self, reg: int, k: int, depth: int = 0):
        """What `reg` holds before instruction k, as a comparable expression."""
        j = self.lastdef(reg, k)
        if j is None:
            return ("entry", reg)
        if depth > 6:
            return ("def", self.insns[j].address)
        w = self.words[j]
        op, rd, ra, rb = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        if op == 31 and (w >> 1) & 0x3FF == 444 and rd == rb:  # mr
            return self.ident(rd, j, depth + 1)
        if op in _LOADS and ra:
            return ("load", _simm(w), self.ident(ra, j, depth + 1))
        if op == 14 and ra:  # addi
            return ("addi", _simm(w), self.ident(ra, j, depth + 1))
        if (op == 18 or op == 19) and w & 1:
            return ("ret", self.insns[j].address) if reg == 3 else ("clobbered", self.insns[j].address)
        return ("def", self.insns[j].address)

    def const(self, reg: int, k: int, depth: int = 0):
        """The constant `reg` holds before instruction k, or None."""
        j = self.lastdef(reg, k)
        if j is None or depth > 6:
            return None
        w = self.words[j]
        op, rd, ra, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op in (14, 15):  # addi / addis, li / lis when ra is 0
            src = 0 if ra == 0 else self.const(ra, j, depth + 1)
            return None if src is None else (src + (_simm(w) << (16 if op == 15 else 0))) & 0xFFFFFFFF
        if op in (24, 25):  # ori / oris: rd is the source here, ra the destination
            src = self.const(rd, j, depth + 1)
            return None if src is None else src | (imm << (16 if op == 25 else 0))
        if op == 31 and (w >> 1) & 0x3FF == 444 and rd == (w >> 11) & 31:
            return self.const(rd, j, depth + 1)
        return None

    def screen_variable(self, reg: int, k: int) -> bool:
        """`reg` is `1 << rS` or `0x10000 << rS`: the screen-bit idiom."""
        j = self.lastdef(reg, k)
        if j is None:
            return False
        w = self.words[j]
        return w >> 26 == 31 and (w >> 1) & 0x3FF == 24 and self.const((w >> 21) & 31, j) in (1, 0x10000)

    def call_target(self, k: int) -> int | None:
        w = self.words[k]
        if w >> 26 == 18 and w & 1:
            disp = w & 0x03FFFFFC
            disp -= 0x04000000 if disp & 0x02000000 else 0
            return (0 if w & 2 else self.insns[k].address) + disp
        return None


def _simm(w: int) -> int:
    d = w & 0xFFFF
    return d - 0x10000 if d & 0x8000 else d


def _functions(img: Image, syms: Symbols):
    for base, data in img.text_segments():
        end = base + len(data)
        starts = [a for a in syms.addrs if base <= a < end]
        for a, b in zip(starts, starts[1:] + [end]):
            yield a, b


def _calls(img: Image) -> dict[int, list[int]]:
    """Callee -> every `bl` site."""
    calls: dict[int, list[int]] = {}
    for base, data in img.text_segments():
        for off in range(0, len(data) - 3, 4):
            w = struct.unpack_from(">I", data, off)[0]
            if w >> 26 == 18 and w & 1:
                disp = w & 0x03FFFFFC
                disp -= 0x04000000 if disp & 0x02000000 else 0
                calls.setdefault((0 if w & 2 else base + off) + disp, []).append(base + off)
    return calls


def _containing(syms: Symbols, addr: int) -> tuple[int, int]:
    i = bisect.bisect_right(syms.addrs, addr) - 1
    return syms.addrs[i], (syms.addrs[i + 1] if i + 1 < len(syms.addrs) else addr + 4)


def _at(fn: Fn, addr: int) -> int | None:
    return next((i for i, x in enumerate(fn.insns) if x.address == addr), None)


# Unnamed functions here are ModelDirector's own (the header names several, e.g. CreateTransformator
# 0x8055DC00); the only other class in the range is MiiHeadsModel. ModelDirectorPair sits below it.
_DIRECTOR_TEXT = (0x8055B298, 0x8055F8D0)


def _family_names(img: Image, syms: Symbols, md, calls) -> set[str]:
    """ModelDirector and every class whose constructor runs a ModelDirector constructor on its own
    `this`: those classes' methods receive a director in r3."""
    names = {n.split("::")[0] for n in syms.by_addr.values() if _FAMILY.match(n)}
    work = list(_DIRECTOR_CTORS)
    while work:
        ctor = work.pop()
        for site in calls.get(ctor, []):
            start, end = _containing(syms, site)
            name = syms.by_addr.get(start, "")
            if "::__ct" not in name or name.split("::")[0] in names:
                continue
            fn = Fn(img, md, start, end)
            k = _at(fn, site)
            if k is not None and fn.ident(3, k) == ("entry", 3):
                names.add(name.split("::")[0])
                work.append(start)
    return names


class FieldScan:
    """Every load of a field followed through its function: what each use does to which bits."""

    def __init__(self, img, syms, md, field: int, width: int, via: int | None, query: int, cap: int) -> None:
        self.img, self.syms, self.md = img, syms, md
        self.lo, self.hi, self.via, self.query, self.cap = field, field + width // 8, via, query, cap
        self.width = width
        self.events: list[tuple] = []  # (address, kind, field mask, function start, Fn, seed index)
        self.seeds = self.capped = 0
        self.family = _family_names(img, syms, md, _calls(img))

    def in_family(self, start: int) -> str | None:
        name = self.syms.by_addr.get(start, "")
        if name.split("::")[0] in self.family:
            return "director"
        if name.startswith("0x") and _DIRECTOR_TEXT[0] <= start < _DIRECTOR_TEXT[1]:
            return "director~"
        return None

    def field_base(self, fn: Fn, ra: int, k: int) -> bool:
        if ra in _SKIP_BASES:
            return False
        if self.via is None:
            return True
        i = fn.ident(ra, k)
        return i[0] == "load" and i[1] == self.via

    def run(self) -> None:
        for start, end in _functions(self.img, self.syms):
            fn = None
            base, data, _ = self.img.find(start)
            for off in range(start - base, end - base, 4):
                w = struct.unpack_from(">I", data, off)[0]
                op = w >> 26
                if op not in _LOADS and op not in _STORES:
                    continue
                d, size = _simm(w), _LOADS.get(op) or _STORES[op]
                if d + size <= self.lo or d >= self.hi:
                    continue
                fn = fn or Fn(self.img, self.md, start, end)
                k = next((i for i, x in enumerate(fn.insns) if x.address == base + off), None)
                if k is None or not self.field_base(fn, (w >> 16) & 31, k):
                    continue
                shift = 8 * (self.hi - (d + size))
                if d < self.lo or d + size > self.hi:
                    self.emit(fn, k, k, "overlap", (1 << self.width) - 1)
                elif op in _LOADS:
                    self.follow(fn, k, (w >> 21) & 31, shift, size, signed=op in (42, 43))
                elif k not in fn.writebacks:  # a store of a value no load here carried
                    value = fn.const((w >> 21) & 31, k)
                    mask = ((1 << 8 * size) - 1) << shift
                    if value is None:
                        self.emit(fn, k, k, "store?", mask)
                    else:
                        self.emit(fn, k, k, f"store={value:#x}", (value << shift) & mask)

    def emit(self, fn: Fn, k: int, seed: int, kind: str, mask: int) -> None:
        self.events.append((fn.insns[k].address, kind, mask, fn.start, fn, seed))

    def follow(self, fn: Fn, seed: int, reg: int, shift: int, size: int, signed: bool = False) -> None:
        """Track the loaded register (and copies of it) until redefined or the function ends.
        live = field bits it still carries; sets = field bits ORed in on the way."""
        self.seeds += 1
        loaded = ((1 << 8 * size) - 1) << shift
        tracked = {reg: (loaded, 0)}
        top = 1 << (8 * size - 1 + shift)  # lha copies this field bit into every register bit above it

        def fb(m: int) -> int:  # a register mask -> field bits
            return (m << shift) & loaded | (top if signed and m >> 8 * size else 0)
        seed_base = fn.ident((fn.words[seed] >> 16) & 31, seed)
        for k in range(seed + 1, min(len(fn.insns), seed + 1 + self.cap)):
            if not tracked:
                return
            w, insn = fn.words[k], fn.insns[k]
            op, rs, ra, rb = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
            xo, rc = (w >> 1) & 0x3FF, w & 1
            new = {}
            if op in (21, 20) and rs in tracked:  # rlwinm, rlwimi with a tracked source
                live, sets = tracked[rs]
                sh, mb, me = rb, (w >> 6) & 31, (w >> 1) & 31
                m = _mask(mb, me)
                surviving = fb(_rotl(m, 32 - sh))
                if sh or rc or op == 20:
                    self.emit(fn, k, seed, "test", surviving & live)
                if op == 21 and sh == 0:
                    new[ra] = (live & fb(m), sets & fb(m))
            elif op == 20 and ra in tracked:  # rlwimi into a tracked value
                live, sets = tracked[ra]
                m = fb(_mask((w >> 6) & 31, (w >> 1) & 31))
                new[ra] = (live & ~m, sets | m)
            elif op in (28, 29) and rs in tracked:  # andi. / andis.
                live, sets = tracked[rs]
                imm = fb((w & 0xFFFF) << (16 if op == 29 else 0))
                self.emit(fn, k, seed, "test", imm & live)
                new[ra] = (live & imm, sets & imm)
            elif op in (24, 25, 26, 27) and rs in tracked:  # ori, oris, xori, xoris
                live, sets = tracked[rs]
                imm = fb((w & 0xFFFF) << (16 if op in (25, 27) else 0))
                if imm:
                    new[ra] = (live, sets | imm)
                else:
                    new[ra] = (live, sets)  # nop / mr-like
            elif op in (10, 11) and ra in tracked:  # cmplwi, cmpwi
                self.emit(fn, k, seed, "cmp", tracked[ra][0])
            elif op == 31 and xo in (0, 32) and (ra in tracked or rb in tracked):  # cmpw, cmplw
                self.emit(fn, k, seed, "cmp", tracked.get(ra, tracked.get(rb))[0])
            elif op == 31 and xo in (28, 60, 444, 316, 412, 124, 476, 284) and (rs in tracked or rb in tracked):
                # and, andc, or, xor, orc, nor, nand, eqv: rS and rB are sources, rA the destination
                if xo == 444 and rs == rb:  # mr
                    new[ra] = tracked[rs]
                else:
                    mine, other = (rs, rb) if rs in tracked else (rb, rs)
                    live, sets = tracked[mine]
                    if other in tracked:
                        self.emit(fn, k, seed, "opaque", live)
                    elif fn.screen_variable(other, k):
                        kind = {28: "var-test", 60: "var-clear", 444: "var-set", 316: "var-toggle"}.get(xo, "opaque")
                        self.emit(fn, k, seed, kind, live)
                        if xo in (60, 444, 316):
                            new[ra] = (live, sets)
                    else:
                        c = fn.const(other, k)
                        if c is None:
                            self.emit(fn, k, seed, "opaque", live)
                        elif xo == 28:
                            self.emit(fn, k, seed, "test", fb(c) & live)
                            new[ra] = (live & fb(c), sets & fb(c))
                        elif xo == 60 and mine == rs:
                            new[ra] = (live & ~fb(c), sets & ~fb(c))
                        elif xo in (444, 316):
                            new[ra] = (live, sets | fb(c))
                        else:
                            self.emit(fn, k, seed, "opaque", live)
            elif op == 31 and xo == 824 and rs in tracked:  # srawi
                self.emit(fn, k, seed, "test", fb(0xFFFFFFFF << rb) & tracked[rs][0])
            elif op == 31 and xo in (26, 954, 922) and rs in tracked:  # cntlzw, extsb, extsh
                m = {26: 0xFFFFFFFF, 954: 0xFF, 922: 0xFFFF}[xo]
                self.emit(fn, k, seed, "test", fb(m) & tracked[rs][0])
            elif op in _STORES and rs in tracked:
                live, sets = tracked[rs]
                d, size = _simm(w), _STORES[op]
                if fn.ident(ra, k) == seed_base and self.lo <= d and d + size <= self.hi:
                    st_mask = (((1 << 8 * size) - 1) << 8 * (self.hi - (d + size))) & loaded
                    if sets & st_mask:
                        self.emit(fn, k, seed, "set", sets & st_mask)
                    if loaded & st_mask & ~live:
                        self.emit(fn, k, seed, "clear", loaded & st_mask & ~live)
                    fn.writebacks.add(k)
                else:
                    self.emit(fn, k, seed, "copy", live)
            elif (op == 18 or op == 19) and w & 1:  # a call: tracked arguments escape
                for r in tracked:
                    if 3 <= r <= 10:
                        self.emit(fn, k, seed, "arg", tracked[r][0])
            else:  # anything else that reads a tracked register (as a source or an address) is unmodelled
                ops = insn.op_str.split(", ")
                sources = ops if insn.mnemonic.startswith(_NO_DEST) or not ops or not re.fullmatch(r"r\d+", ops[0]) \
                    else ops[1:]
                for r in tracked:
                    if any(re.search(rf"\(r{r}\)", s) for s in sources):
                        self.emit(fn, k, seed, "ptr", tracked[r][0])
                    elif any(re.search(rf"\br{r}\b", s) for s in sources):
                        self.emit(fn, k, seed, "opaque", tracked[r][0])
            for r in fn.defs(k):
                if r not in new:
                    tracked.pop(r, None)
            tracked.update(new)
        if tracked:
            self.capped += 1

    def owner(self, fn: Fn, seed: int) -> str:
        w = fn.words[seed]
        base = fn.ident((w >> 16) & 31, seed)
        if self.via is not None:
            base = base[2]  # the director the pointer field was loaded from
        if base == ("entry", 3) and self.in_family(fn.start):
            return self.in_family(fn.start)
        for k in range(len(fn.insns)):
            t = fn.call_target(k)
            if t is not None and self.in_family(t) and not _STATIC.search(self.syms.by_addr.get(t, "")) \
                    and fn.ident(3, k) == base:
                return "director"
        if self.path(fn, base) in self.director_paths():
            return "director@"
        return "untyped"

    def director_paths(self) -> set[str]:
        """Access paths rooted at a named class's `this` that some call anywhere passes as a director,
        e.g. [Kart::Part*this+0x7c]: a use of the same path in another method types as a director."""
        if not hasattr(self, "_paths"):
            self._paths = set()
            for target, sites in _calls(self.img).items():
                if not self.in_family(target) or _STATIC.search(self.syms.by_addr.get(target, "")):
                    continue
                for site in sites:
                    fn = Fn(self.img, self.md, *_containing(self.syms, site))
                    k = _at(fn, site)
                    p = self.path(fn, fn.ident(3, k)) if k is not None else "?"
                    if "*this" in p and p != f"{self.syms.by_addr.get(fn.start, '').rsplit('::', 1)[0]}*this":
                        self._paths.add(p)
        return self._paths

    def path(self, fn: Fn, i, depth: int = 0) -> str:
        """An identity as an access path: [Kart::Movement*this+0x0] for a field of `this`'s field."""
        if i[0] == "entry":
            name = self.syms.by_addr.get(fn.start, "")
            cls = name.rsplit("::", 1)[0] if i[1] == 3 and "::" in name else ""
            return f"{cls}*this" if cls and not cls.startswith("0x") else f"r{i[1]}@entry"
        if i[0] in ("load", "addi") and depth < 4:
            return f"[{self.path(fn, i[2], depth + 1)}+{i[1]:#x}]" if i[0] == "load" else \
                f"{self.path(fn, i[2], depth + 1)}+{i[1]:#x}"
        return f"{i[0]}@{i[1]:08X}" if isinstance(i[1], int) and i[1] > 0xFFFF else "?"

    # Whole-value uses: on a field that is a pointer or a count, which most +4 fields are, they are noise.
    _WHOLE = ("cmp", "copy", "arg", "opaque", "ptr", "store?", "overlap")

    def report(self, whole: bool) -> None:
        print(f"; field +{self.lo:#x}..+{self.hi - 1:#x}{f' via +{self.via:#x}' if self.via is not None else ''}, "
              f"bits {_bits(self.query)}; {self.seeds} loads followed, {self.capped} still tracked at the cap")
        print(f"; director family: {', '.join(sorted(self.family))}")
        print("; kinds: test/set/clear and store=K act on bits; var-* use a 1<<screen mask; "
              f"{'/'.join(self._WHOLE)} use the whole value" + ("" if whole else " (director-typed only; --whole for all)"))
        rows = []
        for addr, kind, mask, start, fn, seed in self.events:
            hit = mask & self.query
            if not hit and not kind.startswith("var"):
                continue
            owner = self.owner(fn, seed)
            if kind in self._WHOLE and owner == "untyped" and not whole:
                continue
            w = fn.words[seed]
            base = self.path(fn, fn.ident((w >> 16) & 31, seed))
            rows.append((addr >= REL_TEXT, owner, addr, kind, hit, fn.insns[seed], base))
        for in_rel, title in ((True, "StaticR.rel (game code)"), (False, "main.dol (libraries and boot code)")):
            part = sorted((r for r in rows if r[0] == in_rel),
                          key=lambda r: (r[1] == "untyped", r[3].startswith("var"), r[2]))
            print(f"\n; {title}: {len(part)} uses in {len({self.syms.describe(r[2]).split('+')[0] for r in part})} functions")
            for _, owner, addr, kind, hit, s, base in part:
                print(f"{owner:<9} {addr:08X} {kind:<11} {_bits(hit):<8} {self.syms.describe(addr):<46} "
                      f"{base}  (seed {s.address:08X} {s.mnemonic} {s.op_str})")


def ctor_args(img: Image, syms: Symbols, md, query: int) -> None:
    """The bitfield argument at every call of a ModelDirector constructor, following wrappers that pass
    their own argument through (subclass constructors)."""
    calls = _calls(img)
    work, seen, flagged = list(_DIRECTOR_CTORS.items()) + list(_DIRECTOR_WRAPPERS.items()), set(), 0
    while work:
        target, reg = work.pop()
        if (target, reg) in seen:
            continue
        seen.add((target, reg))
        print(f"\n; {syms.describe(target)}: bitfield in r{reg}, {len(calls.get(target, []))} calls")
        for site in sorted(calls.get(target, [])):
            start, end = _containing(syms, site)
            fn = Fn(img, md, start, end)
            k = _at(fn, site)
            value = fn.const(reg, k)
            if value is not None:
                mark = "  <-- query bits" if value & query else ""
                flagged += bool(mark)
                print(f"{site:08X} {value:#010x}  {syms.describe(site)}{mark}")
                continue
            i = fn.ident(reg, k)
            if i[0] == "entry":
                print(f"{site:08X} passes its r{i[1]} through  {syms.describe(site)}")
                work.append((start, i[1]))
            else:
                flagged += 1
                print(f"{site:08X} unresolved {i}  {syms.describe(site)}  <-- read by hand")
    print(f"\n; {flagged} calls set a query bit or are unresolved")


def disasm(img: Image, syms: Symbols, addr: int, count: int) -> None:
    md = make_cs()
    code = img.read(addr, count * 4)
    for i in range(count):
        a = addr + i * 4
        name = syms.name(a)
        if name and i:
            print(f"\n{name}:")
        word = code[i * 4: i * 4 + 4]
        insns = list(md.disasm(word, a))
        if insns:
            print(fmt_insn(insns[0], syms))
        else:
            print(f"{a:08X}: {word.hex()}  .word")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fn"); p.add_argument("target"); p.add_argument("--max", type=int, default=600)
    p = sub.add_parser("dis"); p.add_argument("addr"); p.add_argument("count", type=int, nargs="?", default=40)
    p = sub.add_parser("sym"); p.add_argument("regex")
    p = sub.add_parser("xref"); p.add_argument("target")
    p = sub.add_parser("imm"); p.add_argument("value"); p.add_argument("--mnemonic", default="")
    p.add_argument("--range", default="", help="start-end hex addresses to limit the search")
    p = sub.add_parser("dref"); p.add_argument("target")
    p.add_argument("--span", type=lambda x: int(x, 0), default=1, help="also report accesses up to target+span-1")
    p = sub.add_parser("bits")
    p.add_argument("--field", default="4", help="byte offset, or ptr:offset for a field behind a pointer field")
    p.add_argument("--width", type=int, default=32, choices=(8, 16, 32))
    p.add_argument("--bits", default="", help="field bits to report, LSB 0 (e.g. 28-31); default all")
    p.add_argument("--cap", type=int, default=100000, help="instructions to follow a load (default: to the function end)")
    p.add_argument("--ctors", action="store_true", help="report ModelDirector constructor bitfield arguments")
    p.add_argument("--whole", action="store_true", help="also list untyped whole-value uses (compares, copies, args)")
    args = ap.parse_args()

    img, syms = load_all()

    if args.cmd == "sym":
        rx = re.compile(args.regex, re.I)
        for a in syms.addrs:
            if rx.search(syms.by_addr[a]):
                print(f"{a:08X} {syms.by_addr[a]}")
    elif args.cmd == "fn":
        start = syms.lookup(args.target)
        end = syms.next_after(start) or start + 4 * args.max
        n = min((end - start) // 4, args.max)
        print(f"{syms.describe(start)}:  ({n} insns)")
        disasm(img, syms, start, n)
    elif args.cmd == "dis":
        disasm(img, syms, syms.lookup(args.addr), args.count)
    elif args.cmd == "xref":
        target = syms.lookup(args.target)
        for base, data in img.text_segments():
            for off in range(0, len(data) - 3, 4):
                w = struct.unpack_from(">I", data, off)[0]
                if (w >> 26) == 18:  # b / bl
                    disp = w & 0x03FFFFFC
                    if disp & 0x02000000:
                        disp -= 0x04000000
                    a = base + off
                    if (0 if w & 2 else a) + disp == target:
                        print(f"{a:08X} {'bl' if w & 1 else 'b '} from {syms.describe(a)}")
    elif args.cmd == "imm":
        value = int(args.value, 0)
        # A 16-bit immediate or displacement prints sign-extended: 0xd6ec appears as -0x2914.
        wanted = {value, value - 0x10000} if 0x8000 <= value <= 0xFFFF else {value}
        md = make_cs()
        lo, hi = 0, 0xFFFFFFFF
        if args.range:
            lo, hi = (int(x, 16) for x in args.range.split("-"))
        for base, data in img.text_segments():
            if base + len(data) < lo or base > hi:
                continue
            for insn in iter_insns(md, base, data, lo, hi):
                if args.mnemonic and not insn.mnemonic.startswith(args.mnemonic):
                    continue
                if any((v := operand(op)) and v[0] in wanted for op in insn.op_str.split(", ")):
                    print(f"{fmt_insn(insn, syms)}    ; {syms.describe(insn.address)}")
    elif args.cmd == "dref":
        dref(img, syms, syms.lookup(args.target), args.span)
    elif args.cmd == "bits":
        query = 0
        for part in filter(None, args.bits.split(",")):
            lo, _, hi = part.partition("-")
            query |= _mask(31 - int(hi or lo), 31 - int(lo))
        query = query or (1 << args.width) - 1
        if args.ctors:
            ctor_args(img, syms, make_cs(), query)
        else:
            via, _, field = args.field.rpartition(":")
            scan = FieldScan(img, syms, make_cs(), int(field, 0), args.width, int(via, 0) if via else None, query, args.cap)
            scan.run()
            scan.report(args.whole)


if __name__ == "__main__":
    main()
