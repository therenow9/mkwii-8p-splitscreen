#!/usr/bin/env python3
"""A GDB remote-serial-protocol client for Dolphin's GDB stub.

Dolphin's stub serves one connection per emulation session: when the client disconnects, the stub
shuts down for good. So `serve` holds that connection and takes the other commands, one per
request, on a local control port. Start Dolphin with the stub first (install_8p.py dolphin --gdb
[PORT]); emulation waits at the entry point until `serve` connects.

Usage:
  dolphin_gdb.py serve                     connect to the stub and serve commands (run it in the background)
  dolphin_gdb.py status                    qSupported reply and halt reason
  dolphin_gdb.py regs                      r0-r31, pc, lr, ctr, cr, msr, xer
  dolphin_gdb.py mem ADDR LEN              hex dump (ADDR is hex; LEN is decimal or 0x hex)
  dolphin_gdb.py poke ADDR HEXBYTES        write bytes to memory (a halted target), then read them back
  dolphin_gdb.py stack                     registers and a symbolised back-chain from r1
  dolphin_gdb.py layout                    the raw `g` reply word by word, and `p` for pc..xer
  dolphin_gdb.py cont                      continue without waiting for a stop
  dolphin_gdb.py wait [--timeout S]        wait for the stop after `cont`
  dolphin_gdb.py interrupt                 stop a running target
  dolphin_gdb.py bp ADDR | unbp ADDR       set / clear a breakpoint (hex or symbol)
  dolphin_gdb.py break ADDR [--if "r5 < 0x80000000"] [--max-hits N] [--timeout S]
                                           run until ADDR (hex or symbol) is hit with the condition true
  dolphin_gdb.py watch ADDR:LEN [ADDR:LEN ...] [--kind write|read|access] [--timeout S]
                                           run until one of the ranges is written (or read), then stack
  dolphin_gdb.py detach                    detach and end `serve`; Dolphin keeps running

Options before the command: --host (127.0.0.1), --port (2345), --control-port (2346),
--connect-timeout (10 s, `serve` only).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import operator
import re
import shlex
import socket
import struct
import sys
import time
from pathlib import Path

ACK_TIMEOUT = 5.0
REPLY_TIMEOUT = 10.0
MEM_CHUNK = 0x800
STACK_LO, STACK_HI = 0x80000000, 0x81800000

# Dolphin's `g` reply holds r0-r31 only (128 bytes); the rest come one at a time from `p` with
# gdb's powerpc numbers. Checked at the entry stop: p40 = 0x800060A4, the halt reply's pc.
REGNUM = {"pc": 64, "msr": 65, "cr": 66, "lr": 67, "ctr": 68, "xer": 69}
SPECIAL = ["pc", "lr", "ctr", "cr", "msr", "xer"]


class GdbError(Exception):
    pass


def fail(msg: str) -> None:
    sys.exit(f"[dolphin_gdb] {msg}")


def checksum(raw: bytes) -> bytes:
    return b"%02x" % (sum(raw) & 0xFF)


def decode_payload(raw: bytes) -> str:
    """Undoes `}` escapes (next byte XOR 0x20) and run-length encoding (`X*N`: X, N-29 more times)."""
    out = bytearray()
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == 0x7D and i + 1 < len(raw):
            out.append(raw[i + 1] ^ 0x20)
            i += 2
        elif c == 0x2A and i + 1 < len(raw) and out:
            out += out[-1:] * (raw[i + 1] - 29)
            i += 2
        else:
            out.append(c)
            i += 1
    return out.decode("latin-1")


class Gdb:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.sock: socket.socket | None = None
        self.buf = bytearray()
        self.supported = ""
        self.running = False  # after `c` until its stop reply is read

    def connect(self, timeout: float) -> None:
        # Dolphin opens the port only once emulation starts, so a refusal is retried until timeout.
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection((self.host, self.port), timeout=max(0.5, deadline - time.monotonic()))
                break
            except OSError as e:
                if time.monotonic() + 0.5 >= deadline:
                    raise GdbError(f"cannot connect to {self.host}:{self.port} within {timeout}s: {e}") from e
                time.sleep(0.5)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.supported = self.query("qSupported")

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _fill(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("no reply from the stub")
        self.sock.settimeout(remaining)
        data = self.sock.recv(65536)
        if not data:
            raise ConnectionError("the stub closed the connection")
        self.buf += data

    def _await_ack(self, deadline: float) -> bool:
        while True:
            if not self.buf:
                self._fill(deadline)
            c = self.buf[0]
            if c == ord("+"):
                del self.buf[0]
                return True
            if c == ord("-"):
                del self.buf[0]
                return False
            if c == ord("$"):  # a reply that overtook its ack; leave it for _read_packet
                return True
            del self.buf[0]

    def _drain(self) -> None:
        # Requests are strictly one at a time, so bytes waiting before a send are stale. Dolphin
        # sends a second stop reply after an interrupt; left unread, every later reply is off by one.
        self.sock.setblocking(False)
        try:
            while self.sock.recv(65536):
                pass
        except (BlockingIOError, InterruptedError):
            pass
        finally:
            self.sock.setblocking(True)
        self.buf.clear()

    def send(self, data: str) -> None:
        if not self.running:
            self._drain()
        raw = data.encode("latin-1")
        pkt = b"$" + raw + b"#" + checksum(raw)
        for _ in range(3):
            self.sock.sendall(pkt)
            if self._await_ack(time.monotonic() + ACK_TIMEOUT):
                return
        raise GdbError(f"the stub rejected {data!r} three times")

    def read_packet(self, timeout: float = REPLY_TIMEOUT) -> str:
        deadline = time.monotonic() + timeout
        while True:
            while (start := self.buf.find(b"$")) < 0:
                self.buf.clear()  # stray acks
                self._fill(deadline)
            del self.buf[:start]
            while (end := self.buf.find(b"#")) < 0 or len(self.buf) < end + 3:
                self._fill(deadline)
            raw, cs = bytes(self.buf[1:end]), bytes(self.buf[end + 1:end + 3]).lower()
            del self.buf[:end + 3]
            if cs == checksum(raw):
                self.sock.sendall(b"+")
                return decode_payload(raw)
            self.sock.sendall(b"-")

    def interrupt(self) -> None:
        self.sock.sendall(b"\x03")

    def query(self, pkt: str, timeout: float = REPLY_TIMEOUT) -> str:
        self.send(pkt)
        return self.read_packet(timeout)

    def halt_reason(self) -> str:
        return self.query("?")

    def read_regs(self) -> str:
        reply = self.query("g")
        if reply.startswith("E"):
            raise GdbError(f"g: {reply}")
        return reply

    def read_reg(self, num: int) -> int | None:
        reply = self.query("p%x" % num)
        if len(reply) < 8 or reply.startswith("E") or "x" in reply[:8]:
            return None
        return int(reply[:8], 16)

    def regs(self) -> dict[str, int | None]:
        regs = decode_regs(self.read_regs())
        for name in SPECIAL:
            regs[name] = self.read_reg(REGNUM[name])
        return regs

    def read_mem(self, addr: int, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            size = min(MEM_CHUNK, n - len(out))
            reply = self.query("m%x,%x" % (addr + len(out), size))
            if reply.startswith("E") or not reply:
                raise GdbError(f"m{addr + len(out):x},{size:x}: {reply or 'empty reply'}")
            out += bytes.fromhex(reply)
        return bytes(out)

    def write_mem(self, addr: int, data: bytes) -> None:
        reply = self.query("M%x,%x:%s" % (addr, len(data), data.hex()))
        if reply != "OK":
            raise GdbError(f"M{addr:x},{len(data):x}: {reply or 'empty reply'}")

    def read32(self, addr: int) -> int:
        return struct.unpack(">I", self.read_mem(addr, 4))[0]

    def set_bp(self, addr: int) -> None:
        reply = self.query("Z0,%x,4" % addr)
        if reply != "OK":
            raise GdbError(f"Z0 at 0x{addr:08X}: {reply or 'unsupported'}")

    def clear_bp(self, addr: int) -> str:
        return self.query("z0,%x,4" % addr)

    def set_watch(self, kind: int, addr: int, n: int) -> None:
        reply = self.query("Z%d,%x,%x" % (kind, addr, n))
        if reply != "OK":
            raise GdbError(f"Z{kind} at 0x{addr:08X},{n:#x}: {reply or 'unsupported'}")

    def clear_watch(self, kind: int, addr: int, n: int) -> str:
        return self.query("z%d,%x,%x" % (kind, addr, n))

    def cont(self, timeout: float) -> tuple[str, bool]:
        """Continues until a stop reply; after timeout, interrupts. Returns (stop reply, timed out)."""
        self.send("c")
        try:
            return self.read_packet(timeout), False
        except TimeoutError:
            self.interrupt()
            reply = self.read_packet(REPLY_TIMEOUT)
            time.sleep(0.2)  # the second stop reply after an interrupt; the next send drains it
            return reply, True

    def cont_nowait(self) -> None:
        self.send("c")

    def step(self) -> str:
        self.send("s")
        return self.read_packet()

    def detach(self) -> str:
        return self.query("D")

    def backtrace(self, regs: dict, max_frames: int = 32) -> list[int]:
        # EABI: [sp] is the caller's sp, and a function saves its LR at its caller's sp+4.
        frames = [regs["pc"], regs["lr"]]
        sp = regs["r1"]
        while len(frames) < max_frames:
            try:
                next_sp = self.read32(sp)
            except GdbError:
                break
            if next_sp == 0 or not STACK_LO <= next_sp < STACK_HI or next_sp <= sp:
                break
            try:
                frames.append(self.read32(next_sp + 4))
            except GdbError:
                break
            sp = next_sp
        return frames


def word_at(hexstr: str, off: int) -> int | None:
    s = hexstr[off * 2:off * 2 + 8]
    if len(s) < 8 or "x" in s.lower():
        return None
    return int(s, 16)


def decode_regs(hexstr: str) -> dict[str, int | None]:
    return {f"r{i}": word_at(hexstr, i * 4) for i in range(32)}


COND_RE = re.compile(r"\s*(r(?:[12]?\d|3[01])|pc|lr|ctr|cr|msr|xer)\s*(<=|>=|==|!=|<|>)\s*(0x[0-9a-f]+|\d+)\s*",
                     re.IGNORECASE)
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def parse_condition(text: str):
    """`REG OP INT`, compared as unsigned 32-bit. Returns a predicate over a regs dict."""
    m = COND_RE.fullmatch(text)
    if not m:
        raise ValueError(f"condition must be 'REG OP INT' (r0-r31/pc/lr/ctr/cr/msr/xer, < <= > >= == !=): {text!r}")
    num = m.group(3).lower()
    value = (int(num, 16) if num.startswith("0x") else int(num, 10)) & 0xFFFFFFFF
    reg, op = m.group(1).lower(), OPS[m.group(2)]

    def holds(regs: dict) -> bool:
        cur = regs.get(reg)
        return cur is not None and op(cur & 0xFFFFFFFF, value)

    return holds


_symbols = None


def symbols():
    """mkwdis's symbol table, loaded once; None (with one message) when the game files are missing."""
    global _symbols
    if _symbols is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import mkwdis
            _, _symbols = mkwdis.load_all()
        except (SystemExit, Exception) as e:  # load_all exits when main.dol is missing
            print(f"[dolphin_gdb] no symbols, addresses stay raw: {e}", file=sys.stderr)
            _symbols = False
    return _symbols or None


def describe(addr: int | None) -> str:
    if addr is None:
        return "?"
    syms = symbols()
    return syms.describe(addr) if syms else ""


def hx(v: int | None) -> str:
    return "????????" if v is None else f"{v:08X}"


def print_regs(regs: dict) -> None:
    for row in range(4):
        print("  ".join(f"r{i:<2} {hx(regs[f'r{i}'])}" for i in range(row * 8, row * 8 + 8)))
    print("  ".join(f"{name} {hx(regs[name])}" for name in SPECIAL))


def print_stack(gdb: Gdb, regs: dict) -> None:
    for n, addr in enumerate(gdb.backtrace(regs)):
        print(f"#{n:<2} 0x{hx(addr)} {describe(addr)}")


def parse_addr(text: str) -> int:
    if re.fullmatch(r"(0x)?[0-9a-fA-F]+", text):
        return int(text, 16)
    syms = symbols()
    if not syms:
        fail(f"{text!r} is not hex and there are no symbols to look it up")
    return syms.lookup(text)


def cmd_status(gdb: Gdb, _args) -> None:
    print(f"qSupported: {gdb.supported}")
    print(f"halt reason: {gdb.halt_reason()}")


def cmd_regs(gdb: Gdb, _args) -> None:
    print_regs(gdb.regs())


def cmd_mem(gdb: Gdb, args) -> None:
    addr, n = int(args.addr, 16), int(args.len, 0)
    data = gdb.read_mem(addr, n)
    for off in range(0, len(data), 16):
        print(f"{addr + off:08X}: {data[off:off + 16].hex(' ')}")


def cmd_poke(gdb: Gdb, args) -> None:
    addr, data = int(args.addr, 16), bytes.fromhex(args.data)
    gdb.write_mem(addr, data)
    print(f"{addr:08X}: {gdb.read_mem(addr, len(data)).hex(' ')}")


def cmd_stack(gdb: Gdb, _args) -> None:
    regs = gdb.regs()
    print_regs(regs)
    print_stack(gdb, regs)


def cmd_layout(gdb: Gdb, _args) -> None:
    reply = gdb.read_regs()
    print(f"g reply: {len(reply) // 2} bytes")
    for i in range(0, len(reply), 8):
        print(f"[{i // 8:3}] +0x{i // 2:03X}  {reply[i:i + 8]}")
    for name in SPECIAL:
        print(f"p{REGNUM[name]:x} ({name}): {hx(gdb.read_reg(REGNUM[name]))}")


def cmd_cont(gdb: Gdb, _args) -> None:
    gdb.cont_nowait()
    gdb.running = True
    print("continued; `wait` for the next stop, or `interrupt`")


def cmd_wait(gdb: Gdb, args) -> None:
    try:
        reply = gdb.read_packet(args.timeout)
    except TimeoutError:
        print(f"still running after {args.timeout}s")
        return
    gdb.running = False
    pc = gdb.regs()["pc"]
    print(f"stopped: {reply} at pc 0x{hx(pc)} {describe(pc)}")


def cmd_interrupt(gdb: Gdb, _args) -> None:
    gdb.interrupt()
    reply = gdb.read_packet()
    gdb.running = False
    time.sleep(0.2)  # lets the second stop reply arrive, so the next send drains it
    pc = gdb.regs()["pc"]
    print(f"stopped: {reply} at pc 0x{hx(pc)} {describe(pc)}")


def cmd_bp(gdb: Gdb, args) -> None:
    addr = parse_addr(args.addr)
    gdb.set_bp(addr)
    print(f"breakpoint at 0x{addr:08X} {describe(addr)}")


def cmd_unbp(gdb: Gdb, args) -> None:
    addr = parse_addr(args.addr)
    print(f"z0 0x{addr:08X}: {gdb.clear_bp(addr)}")


def cmd_detach(gdb: Gdb, _args) -> None:
    print(f"detach: {gdb.detach()}")


def cmd_break(gdb: Gdb, args) -> None:
    addr = parse_addr(args.addr)
    try:
        cond = parse_condition(args.cond) if args.cond else None
    except ValueError as e:
        fail(str(e))
    print(f"[break] 0x{addr:08X} {describe(addr)}" + (f" if {args.cond}" if cond else ""))
    gdb.set_bp(addr)
    start = time.monotonic()
    deadline = start + args.timeout
    hits = 0

    def rate() -> str:
        elapsed = time.monotonic() - start
        return f"{hits} hits in {elapsed:.1f}s ({hits / elapsed if elapsed else 0:.1f}/s)"

    try:
        while True:
            reply, timed_out = gdb.cont(max(0.1, deadline - time.monotonic()))
            if timed_out:
                print(f"[break] timeout after {args.timeout}s: {rate()}; stop reply {reply}")
                return
            regs = gdb.regs()
            if regs["pc"] != addr:
                print(f"[break] unexpected stop {reply} at pc 0x{hx(regs['pc'])} {describe(regs['pc'])}: {rate()}")
                print_regs(regs)
                print_stack(gdb, regs)
                return
            hits += 1
            if cond is None or cond(regs):
                print(f"[break] hit: {rate()}")
                print_regs(regs)
                print_stack(gdb, regs)
                return
            if hits >= args.max_hits:
                print(f"[break] --max-hits reached without the condition: {rate()}")
                return
            if hits % 1000 == 0:
                print(f"[break] {rate()}")
    finally:
        try:
            gdb.clear_bp(addr)
        except (GdbError, OSError) as e:
            print(f"[break] could not clear the breakpoint at 0x{addr:08X}: {e}", file=sys.stderr)


WATCH_KINDS = {"write": 2, "read": 3, "access": 4}


def cmd_watch(gdb: Gdb, args) -> None:
    kind = WATCH_KINDS[args.kind]
    ranges = []
    for spec in args.ranges:
        addr, sep, n = spec.partition(":")
        if not sep:
            fail(f"watch range {spec!r}: expected ADDR:LEN")
        ranges.append((parse_addr(addr), int(n, 0)))
    placed = []
    try:
        for addr, n in ranges:
            print(f"[watch] {args.kind} 0x{addr:08X}..0x{addr + n - 1:08X} {describe(addr)}")
            gdb.set_watch(kind, addr, n)
            placed.append((addr, n))
        reply, timed_out = gdb.cont(args.timeout)
        if timed_out:
            print(f"[watch] no {args.kind} in {args.timeout}s; stop reply {reply}")
            return
        print(f"[watch] stopped: {reply}")
        regs = gdb.regs()
        print_regs(regs)
        print_stack(gdb, regs)
    finally:
        for addr, n in placed:
            try:
                gdb.clear_watch(kind, addr, n)
            except (GdbError, OSError) as e:
                print(f"[watch] could not clear the watch at 0x{addr:08X}: {e}", file=sys.stderr)


RUNNING_OK = {"wait", "interrupt", "detach"}  # every other command needs a stopped target


def command_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dolphin_gdb.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("regs", cmd_regs), ("stack", cmd_stack), ("layout", cmd_layout),
                     ("cont", cmd_cont), ("interrupt", cmd_interrupt), ("detach", cmd_detach)):
        sub.add_parser(name).set_defaults(fn=fn)
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("mem")
    p.add_argument("addr", help="hex address")
    p.add_argument("len", help="byte count, decimal or 0x hex")
    p.set_defaults(fn=cmd_mem)
    p = sub.add_parser("poke")
    p.add_argument("addr", help="hex address")
    p.add_argument("data", help="bytes as hex, e.g. 00000002")
    p.set_defaults(fn=cmd_poke)
    for name, fn in (("bp", cmd_bp), ("unbp", cmd_unbp)):
        p = sub.add_parser(name)
        p.add_argument("addr", help="hex address or symbol name")
        p.set_defaults(fn=fn)
    p = sub.add_parser("break")
    p.add_argument("addr", help="hex address or symbol name")
    p.add_argument("--if", dest="cond", metavar="'REG OP INT'", help='e.g. "r5 < 0x80000000" (unsigned 32-bit)')
    p.add_argument("--max-hits", type=int, default=100000)
    p.add_argument("--timeout", type=float, default=300, help="seconds before interrupting and giving up")
    p.set_defaults(fn=cmd_break)
    p = sub.add_parser("watch")
    p.add_argument("ranges", nargs="+", metavar="ADDR:LEN", help="hex address or symbol, and a byte count")
    p.add_argument("--kind", choices=sorted(WATCH_KINDS), default="write")
    p.add_argument("--timeout", type=float, default=300, help="seconds before interrupting and giving up")
    p.set_defaults(fn=cmd_watch)
    return ap


def run_request(gdb: Gdb, argv: list[str]) -> tuple[int, str]:
    """Runs one command against the held connection; returns (exit status, its output)."""
    out = io.StringIO()
    status = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            args = command_parser().parse_args(argv)
            if gdb.running and args.cmd not in RUNNING_OK:
                fail("the target is running; `wait` for its stop or `interrupt` it first")
            args.fn(gdb, args)
        except SystemExit as e:  # fail() and argparse errors
            if e.code not in (None, 0):
                status = 1
                if isinstance(e.code, str):
                    print(e.code)
        except (GdbError, TimeoutError, ValueError) as e:
            status = 1
            print(f"[dolphin_gdb] {e}")
    return status, out.getvalue()


def serve(args) -> None:
    gdb = Gdb(args.host, args.port)
    try:
        gdb.connect(args.connect_timeout)
    except GdbError as e:
        fail(str(e))
    ctl = socket.create_server(("127.0.0.1", args.control_port))
    print(f"[serve] connected to {args.host}:{args.port}; commands on 127.0.0.1:{args.control_port}", flush=True)
    with ctl:
        while True:
            conn, _ = ctl.accept()
            with conn:
                argv = shlex.split(conn.makefile("r", encoding="utf-8").readline())
                print(f"[serve] {shlex.join(argv)}", flush=True)
                try:
                    status, text = run_request(gdb, argv)
                except (ConnectionError, OSError) as e:
                    conn.sendall(f"1\n[dolphin_gdb] the stub connection is gone: {e}\n".encode())
                    return
                try:
                    conn.sendall(f"{status}\n{text}".encode("utf-8", "replace"))
                except OSError:  # the client went away mid-request; the stub connection is still good
                    print("[serve] client gone before the reply", flush=True)
                if argv[:1] == ["detach"]:
                    gdb.close()
                    return


def request(control_port: int, argv: list[str]) -> int:
    try:
        conn = socket.create_connection(("127.0.0.1", control_port), timeout=5)
    except OSError as e:
        fail(f"no `dolphin_gdb.py serve` on control port {control_port}: {e}")
    with conn:
        conn.settimeout(None)  # `break` and `wait` take as long as their own timeouts
        conn.sendall((shlex.join(argv) + "\n").encode("utf-8"))
        data = bytearray()
        while chunk := conn.recv(65536):
            data += chunk
    status, _, text = data.decode("utf-8", "replace").partition("\n")
    sys.stdout.write(text)
    return int(status or 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2345)
    ap.add_argument("--control-port", type=int, default=2346)
    ap.add_argument("--connect-timeout", type=float, default=10, help="seconds `serve` keeps retrying the stub")
    ap.add_argument("command", nargs=argparse.REMAINDER, help="serve, or a command for the running serve")
    args = ap.parse_args()
    if not args.command:
        ap.error("a command is required")
    if args.command == ["serve"]:
        serve(args)
        return
    command_parser().parse_args(args.command)  # usage errors here, not in the server
    sys.exit(request(args.control_port, args.command))


if __name__ == "__main__":
    main()
