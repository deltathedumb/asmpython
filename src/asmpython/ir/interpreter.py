"""A reference interpreter for the IR.

Two jobs, and the second is the important one.

First, it is the executable specification. When the meaning of an opcode is
ambiguous, this file is the answer -- prose in `ops.py` describes intent, this
decides. `SHR` on `u32` is a logical shift because `_shr` says so.

Second, it makes a backend testable the moment it exists. Run a module here,
run the same module through a backend, compare the output: any difference is
the backend's bug, localised to one program. Without this, testing a new
backend means trusting that a Python program, a frontend, a runtime and a code
generator are all simultaneously correct, and a mismatch tells you nothing
about which one is wrong.

It is deliberately slow and obvious. Nothing here is optimised, because the one
property it must have is being right, and the second is being readable enough
that a disagreement with it can be adjudicated by reading it.

MEMORY is one flat bytearray. Address 0 is never handed out, so a null pointer
faults instead of aliasing a real object. Globals are laid out first, then
frames grow upward via `alloca`. There is no free: a program that allocas in a
loop will exhaust the arena, which is a correct diagnosis of the program.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import types as T
from .module import Function, Instruction, Module
from .opcodes import Op


class Trap(Exception):
    """The program did something undefined: divide by zero, bad address, ..."""


class _Exited(Exception):
    """`plat_exit` was called. Carries the status; caught only by `run`.

    An exception rather than a return, because the floor's contract is that
    `plat_exit` DOES NOT RETURN -- and the honest way to not return from a
    tree-walking interpreter is to unwind every frame at once. Returning a
    sentinel and checking for it at each call site is the version that works
    until one call site forgets.
    """

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


@dataclass
class Frame:
    func: Function
    registers: dict[int, int | float] = field(default_factory=dict)
    frame_base: int = 0


class Memory:
    """A flat address space. Address 0 is reserved so null always faults."""

    def __init__(self, size: int = 1 << 22) -> None:
        self.buf = bytearray(size)
        self.brk = 8            # never hand out 0

    def alloc(self, nbytes: int, align: int = 8) -> int:
        addr = (self.brk + align - 1) & ~(align - 1)
        end = addr + max(1, nbytes)
        if end >= len(self.buf):
            raise Trap(f"out of memory: {nbytes} bytes at {addr:#x}")
        self.brk = end
        return addr

    def _check(self, addr: int, n: int) -> None:
        if addr <= 0 or addr + n > len(self.buf):
            raise Trap(f"invalid address {addr:#x} (+{n} bytes)")

    def read(self, addr: int, ty: T.Type) -> int | float:
        n = ty.size
        self._check(addr, n)
        raw = bytes(self.buf[addr:addr + n])
        if ty.is_float:
            return struct.unpack("<f" if n == 4 else "<d", raw)[0]
        return int.from_bytes(raw, "little", signed=ty.is_signed and ty is not T.I1)

    def write(self, addr: int, ty: T.Type, value: int | float) -> None:
        n = ty.size
        self._check(addr, n)
        if ty.is_float:
            raw = struct.pack("<f" if n == 4 else "<d", float(value))
        else:
            raw = (_wrap(int(value), ty)).to_bytes(
                n, "little", signed=ty.is_signed and ty is not T.I1)
        self.buf[addr:addr + n] = raw


def _wrap(v: int, ty: T.Type) -> int:
    """Truncate to the type's width, honouring signedness. Integers wrap."""
    if ty.is_ptr:
        return v & 0xFFFFFFFFFFFFFFFF
    bits = ty.bits
    v &= (1 << bits) - 1
    if ty.is_signed and bits > 1 and v >= (1 << (bits - 1)):
        v -= 1 << bits
    return v


class Interpreter:
    """Executes a verified module. `run(entry)` returns the entry's result."""

    def __init__(self, module: Module, *, out=None) -> None:
        self.module = module
        self.mem = Memory()
        self.out = out
        self.globals: dict[str, int] = {}
        self.steps = 0
        self.max_steps = 50_000_000
        #: The dynamic object runtime's state -- the handle table and the
        #: error flag -- created on the first `apy_*` call. Lazy because a
        #: statically typed program never makes one, and because the module
        #: imports this one back for `Trap`.
        self.objects = None
        #: Bytes `plat_write` received that do not yet form whole characters,
        #: per descriptor. See `_plat_write`.
        self._plat_pending: dict[int, bytes] = {}
        #: The status `plat_exit` was called with, or None if it never was.
        #: A host uses this to end the way a compiled program would.
        self.exit_status: int | None = None
        for g in module.globals:
            addr = self.mem.alloc(max(1, g.size))
            if g.data:
                self.mem.buf[addr:addr + len(g.data)] = g.data
            self.globals[g.name] = addr

    # ── host functions ──────────────────────────────────────────────────────
    # The IR has no I/O opcodes -- printing is not a machine operation. A
    # frontend calls a named function and the host provides it, exactly as a
    # real target would resolve it in a runtime library.
    def _host(self, name: str, args: list) -> int | float | None:
        # The dynamic object runtime first, because it is now most of what a
        # compiled program calls. It lives in its own module -- it is as big
        # as this file and it is one subject, the meaning of a Python VALUE,
        # rather than the meaning of an opcode. `NOT_MINE` keeps the fallthrough
        # explicit: a name it does not claim reaches the bindings below and,
        # failing those, the trap at the end.
        if name.startswith("apy_"):
            from .objects_host import NOT_MINE, ObjectHost
            if self.objects is None:
                self.objects = ObjectHost(self)
            result = self.objects.call(name, args)
            if result is not NOT_MINE:
                return result
        # THE PLATFORM FLOOR (`link/platform.py`). The three functions a
        # backend must supply that are not IR, implemented here so the IR
        # interpreter runs the same runtime a backend runs -- which is the
        # whole point of docs/INERT-RUNTIME.md and what the corpus checks by
        # comparing this path against the C one.
        if name == "plat_write":
            return self._plat_write(int(args[0]), int(args[1]), int(args[2]))
        if name == "plat_exit":
            raise _Exited(int(args[0]) & 0xFF)
        if name == "plat_heap":
            n = int(args[0])
            if n <= 0:
                return 0
            try:
                return self.mem.alloc(n)
            except Trap:
                return 0            # null, by contract -- not a crash
        if name == "putchar":
            self._emit(chr(int(args[0]) & 0xFF))
            return 0
        if name == "put_int":
            self._emit(str(int(args[0])))
            return None
        if name == "put_float":
            self._emit(repr(float(args[0])))
            return None
        if name == "put_bool":
            self._emit("True" if int(args[0]) else "False")
            return None
        if name == "put_none":
            self._emit("None")
            return None
        if name == "print_int":
            self._emit(f"{int(args[0])}\n")
            return None
        if name == "py_pow_int":
            # Python's own `**`, which is the oracle every path is measured
            # against. The compiled runtime reaches the same answer the hard
            # way (double-double squaring, because the platform's libm `pow` is
            # a ulp off here); this is the same answer, cheaply.
            return float(args[0]) ** int(args[1])
        if name in ("pow", "powf"):
            # libm's, matching what a compiled binary links against and what
            # CPython's `**` calls. Python's float ** is this function; a
            # reimplementation here would disagree in the last bit.
            import math
            return math.pow(float(args[0]), float(args[1]))
        if name in ("fmod", "fmodf"):
            # The same arrangement as `pow`, for the same reason. Every
            # backend lowers a float REM to a call here rather than to an
            # instruction -- there is no float remainder in the ISA and
            # computing it from a division loses precision once the quotient
            # is large -- so a module that came back FROM one of them calls
            # `fmod` where the IR it was built from used the opcode.
            #
            # `_arith` already answers REM with `math.fmod`, so binding the
            # symbol to the same function is what keeps those two paths
            # agreeing. Without it, IR that round-trips through a backend
            # traps here on a program the original ran.
            import math
            return math.fmod(float(args[0]), float(args[1]))
        if name == "print_float":
            # Python's repr, which is what the runtime the compiled paths link
            # against now prints too (`link/runtime.py`'s `py_repr_double`).
            # This used to be C's `%f` -- `32.000000` for `32.0` -- and the
            # comment here recorded that divergence as deliberate. It was, and
            # it also meant every float a compiled program printed disagreed
            # with CPython, which is precisely what the conformance suite
            # measures. What must never differ is THIS and a compiled binary,
            # so the two moved together.
            self._emit(repr(float(args[0])) + "\n")
            return None
        if name == "print_str":
            addr = int(args[0])
            end = self.mem.buf.index(0, addr)
            self._emit(bytes(self.mem.buf[addr:end]).decode("utf-8", "replace"))
            return None
        raise Trap(f"call to undefined function {name!r} (no host binding)")

    def _emit(self, s: str) -> None:
        if self.out is None:
            print(s, end="")
        else:
            self.out.write(s)

    def _plat_write(self, fd: int, addr: int, n: int) -> int:
        """`plat_write(fd, buf, n)`. Returns bytes written, or -1.

        THE OUTPUT HERE IS TEXT AND THE CONTRACT IS BYTES, so a multi-byte
        character split across two calls has to survive the split. It will
        happen: a runtime that formats into a fixed buffer and flushes when it
        is full has no idea where the character boundaries are. So incomplete
        trailing bytes are HELD until the next call rather than replaced, and
        only a sequence that is still invalid once it is complete becomes U+FFFD.
        """
        if n < 0:
            return -1
        if n == 0:
            return 0
        try:
            self.mem._check(addr, n)
        except Trap:
            return -1               # a bad address is a failed write, not a crash
        pending = self._plat_pending.get(fd, b"") + bytes(
            self.mem.buf[addr:addr + n])
        # Split off any incomplete sequence at the end. A UTF-8 lead byte says
        # how many continuations follow, so at most three bytes are ever held.
        cut = len(pending)
        for back in range(1, min(4, len(pending)) + 1):
            b = pending[-back]
            if b < 0x80:
                break
            if b >= 0xC0:           # a lead byte
                need = (2 if b < 0xE0 else 3 if b < 0xF0 else 4)
                if back < need:
                    cut = len(pending) - back
                break
        self._plat_pending[fd] = pending[cut:]
        text = pending[:cut].decode("utf-8", errors="replace")
        if fd == 2:
            import sys
            sys.stderr.write(text)
        else:
            self._emit(text)
        return n

    # ── execution ───────────────────────────────────────────────────────────
    def run(self, entry: str = "main", args: list | None = None):
        fn = self.module.function(entry)
        if fn is None:
            raise Trap(f"no function named {entry!r}")
        try:
            return self._call(fn, args or [])
        except _Exited as done:
            # `plat_exit` unwound every frame. The status is RECORDED as well
            # as returned, because a caller cannot otherwise tell it apart
            # from an ordinary `return 7` -- and the two mean different things
            # to whoever is hosting the interpreter. `driver/cli.py` reads it.
            self.exit_status = done.status
            return done.status

    def _call(self, fn: Function, args: list):
        if fn.external:
            return self._host(fn.name, args)
        fr = Frame(fn, frame_base=self.mem.brk)
        for reg, val in zip(fn.params, args):
            fr.registers[reg] = val
        blk = fn.blocks[0]
        prev_brk = self.mem.brk
        try:
            while True:
                nxt = None
                for ins in blk.instructions:
                    self.steps += 1
                    if self.steps > self.max_steps:
                        raise Trap("step limit exceeded (infinite loop?)")
                    res = self._exec(fr, ins)
                    if isinstance(res, _Jump):
                        nxt = res
                        break
                    if isinstance(res, _Return):
                        return res.value
                if nxt is None:
                    raise Trap(f"{fn.name}/{blk.label}: fell off the end")
                target = fn.block(nxt.label)
                if target is None:
                    raise Trap(f"{fn.name}: no block {nxt.label!r}")
                blk = target
        finally:
            self.mem.brk = prev_brk     # frame allocas die with the frame

    def _exec(self, fr: Frame, ins: Instruction):
        op, ty = ins.op, ins.ty
        R = fr.registers

        def a(i: int):
            try:
                return R[ins.args[i]]
            except KeyError:
                raise Trap(
                    f"{fr.func.name}: read of uninitialised register "
                    f"%{ins.args[i]}"
                ) from None

        def put(v):
            if ins.dst is not None:
                R[ins.dst] = v
            return None

        if op is Op.CONST:
            return put(float(ins.imm) if ty.is_float else _wrap(int(ins.imm), ty))
        if op is Op.COPY:
            return put(a(0))
        if op is Op.GLOBAL_ADDR:
            return put(self.globals[ins.sym])
        if op is Op.FUNC_ADDR:
            return put(_FUNC_TAG | self.module.functions.index(self.module.function(ins.sym)))

        if op in _ARITH:
            return put(_arith(op, ty, a(0), a(1)))
        if op is Op.NEG:
            return put(-a(0) if ty.is_float else _wrap(-a(0), ty))
        if op is Op.NOT:
            return put(_wrap(~int(a(0)), ty))
        if op in (Op.SHL, Op.SHR):
            return put(_shift(op, ty, int(a(0)), int(a(1))))
        if op in _CMP:
            return put(1 if _compare(op, ty, a(0), a(1)) else 0)

        if op is Op.TRUNC:
            return put(_wrap(int(a(0)), ty))
        if op is Op.EXTEND:
            return put(_wrap(int(a(0)), ty))
        if op is Op.FTOI:
            return put(_wrap(int(a(0)), ty))
        if op is Op.ITOF:
            return put(float(a(0)))
        if op is Op.FTOF:
            v = float(a(0))
            return put(struct.unpack("<f", struct.pack("<f", v))[0]
                       if ty is T.F32 else v)
        if op is Op.BITCAST:
            return put(_bitcast(a(0), fr.func.register_type(ins.args[0]), ty))

        if op is Op.ALLOCA:
            return put(self.mem.alloc(int(ins.imm)))
        if op is Op.LOAD:
            return put(self.mem.read(int(a(0)), ty))
        if op is Op.STORE:
            self.mem.write(int(a(1)), ty, a(0))
            return None
        if op is Op.OFFSET:
            return put(int(a(0)) + int(a(1)))

        if op is Op.CALL:
            callee = self.module.function(ins.sym)
            if callee is None:
                raise Trap(f"call to unknown function {ins.sym!r}")
            return put(self._call(callee, [R[x] for x in ins.args]))
        if op is Op.CALL_PTR:
            idx = int(a(0)) & ~_FUNC_TAG
            return put(self._call(self.module.functions[idx],
                                  [R[x] for x in ins.args[1:]]))

        if op is Op.JUMP:
            return _Jump(ins.labels[0])
        if op is Op.BRANCH:
            return _Jump(ins.labels[0] if a(0) else ins.labels[1])
        if op is Op.SWITCH:
            v = int(a(0))
            for cv, lbl in ins.cases:
                if cv == v:
                    return _Jump(lbl)
            return _Jump(ins.labels[0])
        if op is Op.RET:
            return _Return(a(0) if ins.args else None)
        if op is Op.UNREACHABLE:
            raise Trap(f"{fr.func.name}: reached `unreachable`")

        raise Trap(f"interpreter has no rule for {op.value!r}")


_FUNC_TAG = 1 << 40


@dataclass(slots=True)
class _Jump:
    label: str


@dataclass(slots=True)
class _Return:
    value: object


_ARITH = (Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.REM, Op.AND, Op.OR, Op.XOR)
_CMP = (Op.EQ, Op.NE, Op.LT, Op.LE, Op.GT, Op.GE)


def _arith(op: Op, ty: T.Type, x, y):
    if ty.is_float:
        if op is Op.ADD: return x + y
        if op is Op.SUB: return x - y
        if op is Op.MUL: return x * y
        if op is Op.DIV:
            if y == 0:
                raise Trap("float division by zero")
            return x / y
        if op is Op.REM:
            if y == 0:
                raise Trap("float remainder by zero")
            import math
            return math.fmod(x, y)
        raise Trap(f"{op.value} is not defined on {ty}")
    x, y = int(x), int(y)
    if op is Op.ADD: return _wrap(x + y, ty)
    if op is Op.SUB: return _wrap(x - y, ty)
    if op is Op.MUL: return _wrap(x * y, ty)
    if op is Op.AND: return _wrap(x & y, ty)
    if op is Op.OR:  return _wrap(x | y, ty)
    if op is Op.XOR: return _wrap(x ^ y, ty)
    if y == 0:
        raise Trap("integer division by zero")
    # Truncating division, like C and every machine -- NOT Python's floor
    # division. A frontend whose language floors must lower it to more than
    # one opcode; see ops.py on REM.
    q = abs(x) // abs(y)
    if (x < 0) != (y < 0):
        q = -q
    if op is Op.DIV:
        return _wrap(q, ty)
    return _wrap(x - q * y, ty)


def _shift(op: Op, ty: T.Type, x: int, n: int):
    if n < 0 or n >= ty.bits:
        raise Trap(f"shift by {n} is undefined for {ty}")
    if op is Op.SHL:
        return _wrap(x << n, ty)
    if ty.is_signed:
        return _wrap(x >> n, ty)                      # arithmetic
    return _wrap((x & ((1 << ty.bits) - 1)) >> n, ty)  # logical


def _compare(op: Op, ty: T.Type, x, y) -> bool:
    if op is Op.EQ: return x == y
    if op is Op.NE: return x != y
    if op is Op.LT: return x < y
    if op is Op.LE: return x <= y
    if op is Op.GT: return x > y
    return x >= y


def _bitcast(v, src: T.Type, dst: T.Type):
    if src.is_float and not dst.is_float:
        raw = struct.pack("<f" if src.size == 4 else "<d", float(v))
        return int.from_bytes(raw, "little", signed=dst.is_signed)
    if dst.is_float and not src.is_float:
        raw = _wrap(int(v), src).to_bytes(src.size, "little",
                                          signed=src.is_signed)
        return struct.unpack("<f" if dst.size == 4 else "<d", raw)[0]
    return _wrap(int(v), dst)


def run(module: Module, entry: str = "main", args: list | None = None, *, out=None):
    """Convenience: execute `module` and return the entry function's result."""
    return Interpreter(module, out=out).run(entry, args)
