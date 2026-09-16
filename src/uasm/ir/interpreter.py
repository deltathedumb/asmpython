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

import math
import struct
import threading
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
    #: Countdown of remaining STATIC reads, for each `T.PTR` register the
    #: analysis will allow `_consume` to retire. A fresh copy per call,
    #: since it counts down as the invocation runs, and RESET per write
    #: from `budget` below, since the count belongs to the value rather
    #: than to the register.
    #:
    #: WHICH REGISTERS ARE IN IT is `Interpreter._analyze`'s answer: all of
    #: them for a function with no loop, and only the block-local ones for
    #: a function with one -- see the long comment there. `None` only for a
    #: `Frame` built outside `_call`, which nothing does; the guards in
    #: `_consume` and `_would_consume` are there so that if something ever
    #: does, it retires nothing rather than crashing.
    remaining: dict[int, int] | None = None
    #: Registers `_consume` must never retire early -- every parameter and
    #: every register ever assigned by `Op.COPY`, which is how a NAMED
    #: Python variable is told from a compiler temporary. Shared across
    #: calls (purely static, from `_analyze`), unlike `remaining`.
    named: frozenset[int] = frozenset()
    #: Argument-buffer address -> the handles this frame took a reference
    #: to on that buffer's behalf. See `Op.STORE`: an `apy_call`-style call
    #: passes its arguments through an `alloca`'d array rather than as call
    #: operands, so the BUFFER is what owns them for the length of the
    #: call, and the register that loaded each one can be retired at the
    #: store. Released by the call that reads the buffer (`Op.CALL`, when
    #: an operand's value IS this address), and at frame teardown for a
    #: buffer no call ever consumed -- which leaks a count rather than
    #: dropping one early, the direction this scheme errs in.
    argbuf: dict = field(default_factory=dict)
    #: `(start, end)` for every `alloca` this invocation has made, so a
    #: store into the middle of an argument array can be attributed to the
    #: array. Per-invocation: the addresses come from `Memory.alloc` and
    #: differ between calls to the same function.
    allocas: list = field(default_factory=list)
    #: `_analyze`'s ORIGINAL read count, to restore a register's budget in
    #: `remaining` each time it is WRITTEN. Without that the count is per
    #: INVOCATION, which is right for a straight-line function and wrong
    #: inside a loop: a block-local temporary written afresh on every
    #: iteration gets a new value each time and has to be allowed its
    #: reads again. Shared across calls -- purely static, like `named`.
    budget: dict = field(default_factory=dict)


class Memory:
    """A flat address space. Address 0 is reserved so null always faults.

    TWO ALLOCATORS, GROWING TOWARDS EACH OTHER. Globals and `alloca` bump
    `brk` up from the bottom and a frame's are given back when it returns;
    `plat_heap` takes from the top and NOTHING gives those back. The floor's
    contract says so in as many words -- "The region is never freed" -- and
    one allocator for both breaks it in a way that is invisible until a
    program calls `plat_heap` from inside a function, which is where every
    real `malloc` calls it from: the frame teardown below resets `brk`, so
    the block the caller is still holding is handed out again to the next
    `alloca` and quietly overwritten. That is exactly what happened to a C
    program whose `malloc`ed array came back with four elements replaced by
    the text of its own `printf` buffer.
    """

    def __init__(self, size: int = 1 << 22) -> None:
        self.buf = bytearray(size)
        #: THE STACK POINTER IS PER THREAD, and that is the whole of what a
        #: thread needs from this class. Frames are handed back by resetting
        #: `brk` when a call returns, so two threads sharing one would give
        #: each other's frames away -- which is not a slow leak but an
        #: immediate, silent overwrite. `enter_thread` gives a new thread a
        #: region of its own; everything else here is shared, which is what
        #: threads sharing an address space means.
        self._local = threading.local()
        self._local.brk = 8     # never hand out 0
        #: THE FIRST THREAD'S STACK POINTER, kept beside the thread-local
        #: one because `heap` has to know where the bottom-up allocator has
        #: reached and it may be called from any thread.
        self._main = threading.get_ident()
        self._main_brk = 8
        self.heap_top = len(self.buf)
        #: The heap and the stack regions grow towards each other from the
        #: same end, so handing one out is not two threads' business at once.
        self._lock = threading.Lock()

    @property
    def brk(self) -> int:
        return getattr(self._local, "brk", self._main_brk)

    @brk.setter
    def brk(self, value: int) -> None:
        self._local.brk = value
        if threading.get_ident() == self._main:
            self._main_brk = value

    @property
    def _limit(self) -> int:
        """How far this thread's stack may grow.

        THE MAIN THREAD'S LIMIT IS THE HEAP, which is the arrangement this
        class is built around: the two grow towards each other and meeting
        is what "out of memory" means. A thread other than the first has a
        REGION, taken from the heap once, and its limit is the end of that
        -- so one thread's frames cannot walk into another's.
        """
        got = getattr(self._local, "limit", 0)
        return got or self.heap_top

    def alloc(self, nbytes: int, align: int = 8) -> int:
        addr = (self.brk + align - 1) & ~(align - 1)
        end = addr + max(1, nbytes)
        if end >= self._limit:
            raise Trap(f"out of memory: {nbytes} bytes at {addr:#x}")
        self.brk = end
        return addr

    def heap(self, nbytes: int, align: int = 16) -> int:
        """`plat_heap`. Outlives every frame; see the class docstring."""
        with self._lock:
            addr = (self.heap_top - max(1, nbytes)) & ~(align - 1)
            # AGAINST THE FIRST THREAD'S STACK POINTER, not the calling
            # thread's: another thread's stack is a region that came out of
            # this same heap and is already below `heap_top`.
            if addr <= self._main_brk:
                raise Trap(f"out of memory: {nbytes} bytes from the heap")
            self.heap_top = addr
            return addr

    #: How much stack a thread other than the first one gets. Its frames
    #: come out of the same space `plat_heap` uses and are never given back,
    #: which is what a thread that may still be running requires.
    THREAD_STACK = 1 << 18

    def enter_thread(self) -> None:
        """Give the calling thread a stack region of its own.

        THE FIRST THING A NEW THREAD DOES, before it runs any IR: until this
        has run, `brk` answers the main thread's and a frame would be
        allocated on top of somebody else's.
        """
        base = self.heap(self.THREAD_STACK)
        self._local.brk = base
        self._local.limit = base + self.THREAD_STACK

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
        #: `id(Function)` -> `(has_loop, read_count)`, memoised by
        #: `_analyze`. A function is analysed at most once per run.
        self._fn_analysis: dict[int, tuple[bool, dict[int, int]]] = {}
        #: Which blocks of a function lie on a cycle, by function id -- see
        #: `_looping_blocks`. Kept beside `_fn_analysis` and for the same
        #: reason: it is a static property of the IR and never changes.
        self._fn_loops: dict[int, frozenset] = {}
        for g in module.globals:
            # THE ALIGNMENT IT ASKED FOR, and not the allocator's default.
            # A global carries one because a frontend had a reason -- C's
            # `_Alignas(32)`, an SSE vector, a page -- and a backend that
            # emits one honours it, so an interpreter that packs everything
            # to eight bytes disagrees with every compiled path about what
            # `&v % 32` is. It was right about two thirds of the time by
            # luck, which is the worst way for it to be wrong.
            addr = self.mem.alloc(max(1, g.size), max(8, g.align))
            if g.data:
                self.mem.buf[addr:addr + len(g.data)] = g.data
            self.globals[g.name] = addr
        #: Every global is allocated here, upfront, before any function
        #: runs -- `Op.STORE`'s refcounting hook uses this to tell a
        #: global's address from an `alloca`'s. `self.mem` is a bump
        #: allocator (`Memory.alloc` only ever grows `brk`), so an address
        #: below this boundary is a global FOR THE REST OF THE RUN and one
        #: at or above it never is -- no per-instruction tracking needed.
        self._globals_end = self.mem.brk

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
            from ..objects.host import NOT_MINE, ObjectHost
            if self.objects is None:
                self.objects = ObjectHost(self)
            result = self.objects.call(name, args)
            if result is not NOT_MINE:
                return result
        # THE PLATFORM FLOOR (`objects/floor.py`). The three functions a
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
                return self.mem.heap(n)
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
            # against now prints too (`objects/support.py`'s `py_repr_double`).
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
        # THE HOST SERVICES (`objects/hostsvc.py`), which are the contract every
        # backend implements -- so the interpreter implements them too, and
        # is a backend like any other in that respect. Before the ctypes
        # bindings below, because these are the arrangement those exist to
        # replace and a name should mean the portable one where both exist.
        from ..objects.hostsvc_host import (
            NOT_MINE as _HS_NOT_MINE, call as _hs_call)
        result = _hs_call(self, name, args)
        if result is not _HS_NOT_MINE:
            return result
        # THE NATIVE SYMBOLS A `ctypes` DECLARATION REACHES, last, because a
        # bundled module's declaration is the only thing that puts one in an
        # IR module and the name could otherwise shadow a runtime function.
        # Without these the interpreter cannot run a program that opens a file
        # -- the declaration is resolved by the LINKER, and this path has no
        # linker -- so the oracle would have nothing to say about the compiled
        # behaviour of `pathlib`. See `natives_host.py`.
        from ..objects.natives_host import NOT_MINE, call as native_call
        result = native_call(self, name, args)
        if result is not NOT_MINE:
            return result
        raise Trap(f"call to undefined function {name!r} (no host binding)")

    def _emit(self, s: str) -> None:
        if self.out is None:
            print(s, end="")
        else:
            self.out.write(s)

    def _objects_own(self, name: str) -> bool:
        """Whether the host object runtime implements `name`. See `_call`."""
        from ..objects.host import _TABLE
        return name in _TABLE

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
            got = self._call(fn, args or [])
        except RecursionError:
            # THE INTERPRETER'S OWN STACK, not the program's. A call here is a
            # Python call, so a program a few hundred frames deep exhausts the
            # host interpreter -- which reaches the user as a traceback ending
            # somewhere in `_exec`, reading as a compiler crash. It is a real
            # limit of this execution path and it has a real remedy: the
            # compiled paths have a real stack.
            raise Trap(
                "the reference interpreter ran out of stack; this program "
                "recurses deeper than it can follow. Build it instead of "
                "running it -- a compiled program has the machine's stack"
            ) from None
        except _Exited as done:
            # `plat_exit` unwound every frame. The status is RECORDED as well
            # as returned, because a caller cannot otherwise tell it apart
            # from an ordinary `return 7` -- and the two mean different things
            # to whoever is hosting the interpreter. `driver/cli.py` reads it.
            self.exit_status = done.status
            # SHUTDOWN STILL HAPPENS, because `sys.exit()` is what this is:
            # CPython raises `SystemExit`, unwinds, and then runs its final
            # collection exactly as an ordinary return does. (`os._exit()`
            # is the one that skips it, and nothing lowers to `plat_exit`
            # for that yet.)
            self._shutdown()
            return done.status
        self._shutdown()
        return got

    def _shutdown(self) -> None:
        """CPython's FINAL COLLECTION, which is a thing programs print from.

        A `__del__` still pending when the last statement finishes runs at
        interpreter shutdown there -- `a = Foo()` at module level prints
        `del a` AFTER the program's own output -- and this used to stop
        instead, so everything alive at the end silently never finalized.
        That was the largest of the divergences `docs/STDLIB.md` records,
        and the only one a program could see just by ending.

        TWO PASSES, and the first one is what gets the ORDER right:

        1. Drop the module's globals, in the order they were BOUND. That
           is the order CPython's own shutdown clears a module dict in, and
           it drives every ordinary cascade -- a global list releases its
           elements, an instance its attributes -- through the same
           `decref` the rest of the run uses. So `a = Foo("a"); b =
           Foo("b")` finalizes `a` then `b`, and a `Foo` inside a global
           list finalizes when that list does.

        2. Finalize whatever is still alive, in the order it was created.
           This is where a value reachable only through something whose
           count this scheme deliberately does not cascade from ends up --
           a closure's captured variable, held by a cell held by a
           function (see `objects_host._held_by` on why a `Func` is not
           walked during the run). AND IT IS SAFE ONLY HERE: "finalize it
           anyway" is exactly the wrong answer while a program is running
           and exactly the right one when there is no program left to
           observe the object. CPython's shutdown makes the same trade.
        """
        h = self.objects
        if h is None:
            return
        # A POINTER-SIZED GLOBAL HOLDING A LIVE HANDLE, and nothing else.
        # `module.globals` mixes the program's variables (`gv_x`, eight
        # bytes, a handle) with its string and bytes literals (`__str0`,
        # whatever length, raw data), and `Global` records only a size --
        # there is no flag saying which. Two filters make the question
        # exact rather than probable: a global narrower than a pointer
        # cannot hold one (and reading eight bytes from it would read its
        # NEIGHBOUR's), and a value that is not a key of `_refcount` was
        # never a handle this scheme minted.
        #
        # NOTHING IS WRITTEN BACK. The first version stored 0 over each
        # slot to keep a second pass from decrefing it twice, and that is
        # how the literals got destroyed: `__str0` holds the attribute
        # name `n`, the zero landed on it, and the closure's `__del__`
        # then failed with `'Foo' object has no attribute '\x00'`. The
        # store was never needed -- no program code runs after this, and
        # the sweep below is guarded by `_dead`.
        sized = {g.name: g.size for g in self.module.globals}
        for name, addr in sorted(self.globals.items(), key=lambda kv: kv[1]):
            if sized.get(name, 0) < T.PTR.size:
                continue
            value = int(self.mem.read(addr, T.PTR))
            if value in h._refcount:
                h.decref(value)
        # NEWEST HANDLES ARE STILL LOWEST-FIRST: `_refcount` is insertion
        # ordered and `_new` inserts in creation order, so this walks what
        # is left oldest-first -- which is the order the remaining objects
        # were built in and the order CPython's own sweep tends to find
        # them. `list()` because finalizing cascades and mutates the table.
        for handle in list(h._refcount):
            if handle not in h._dead:
                h._finalize_once(handle)

    def _call(self, fn: Function, args: list):
        if fn.external:
            return self._host(fn.name, args)
        if fn.name.startswith("apy_") and self._objects_own(fn.name):
            # THE HOST OBJECT RUNTIME OWNS EVERY `apy_*` NAME IT CLAIMS, even
            # when the module also DEFINES one -- which it now can, because
            # part of the runtime is compiled from `runtime/*.py` and spliced
            # in (`objects/ir.py`).
            #
            # The two cannot be mixed, and this is the reason rather than a
            # preference: `objects_host.py` represents an `apy_value` as a
            # HANDLE into a Python-side table, and the ported code represents
            # it as an ADDRESS in this interpreter's flat memory. A ported
            # `apy_from_int` therefore hands back something every unported
            # function rejects -- observed as `apy_is: 2248 is not a runtime
            # value handle`, which names neither runtime.
            #
            # So the port is NOT incremental on this path. The interpreter
            # keeps the host runtime until enough is ported to switch all at
            # once, and until then it is the ORACLE the compiled paths are
            # measured against, which is what the corpus is for.
            return self._host(fn.name, args)
        fr = Frame(fn, frame_base=self.mem.brk)
        # NOT GATED ON `self.objects` -- purely static, and cheap, so it
        # runs even for the OUTERMOST call, before the object runtime
        # necessarily exists yet (`self.objects` is lazy: the first
        # `apy_*` call creates it). Gating this on `self.objects is not
        # None` here left the very first frame -- a script's whole
        # top-level body, for the common case -- permanently without a
        # `remaining` table, since that check ran before this frame's
        # first `apy_*` call had a chance to create one. `_consume`
        # guards the actual decref on `self.objects` itself, which by
        # the time anything reaches it, is set.
        has_loop, read_count, named = self._analyze(fn)
        fr.named = named
        # ALWAYS A TABLE NOW. `_analyze` has already narrowed `read_count`
        # to the block-local registers when the function has a loop, so
        # what arrives here is exactly what may be retired -- and a
        # function with a loop is no longer excluded wholesale.
        fr.remaining = dict(read_count)
        fr.budget = read_count
        for reg, val in zip(fn.params, args):
            fr.registers[reg] = val
            # A PARAMETER IS A NEW BINDING, same as `put()` treats any other
            # register write -- the caller's own reference to `val`
            # (whatever slot it came from) is untouched; this is the
            # callee's OWN, additional one.
            if self.objects is not None and fn.registers.get(reg) is T.PTR and val:
                self.objects.incref(val)
        blk = fn.blocks[0]
        prev_brk = self.mem.brk
        returned = None
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
                        returned = res.value
                        return res.value
                if nxt is None:
                    raise Trap(f"{fn.name}/{blk.label}: fell off the end")
                target = fn.block(nxt.label)
                if target is None:
                    raise Trap(f"{fn.name}: no block {nxt.label!r}")
                blk = target
        finally:
            self.mem.brk = prev_brk     # frame allocas die with the frame
            if self.objects is not None:
                # EVERY REGISTER IN THIS FRAME IS GOING OUT OF SCOPE, so
                # every one that held a handle is decref'd -- INCLUDING
                # whatever is being returned, which is the part that took
                # two tries to get right.
                #
                # The first version SKIPPED one occurrence of the returned
                # handle, reasoning that the reference was being handed to
                # the caller rather than dropped. It is not: the caller's
                # own `put()` adds its OWN incref on receipt, so keeping
                # this frame's as well counts the same reference twice,
                # permanently. `def get(x): return x` then `a = get(d)`
                # left `d` one over for the rest of the run, and `del a;
                # del d` never finalized it -- a LEAK, not the "a step too
                # late" the old comment claimed.
                #
                # PINNED, THEN DECREF'D, which is what the skip was really
                # reaching for: the pin (`ObjectHost._protect`) holds off
                # finalization for the window between this frame dropping
                # its reference and the caller's `put()` taking one, so a
                # value whose ONLY reference was this frame's does not
                # finalize in the gap. `put()` lifts the pin through
                # `settle` once the value has a counted home -- the same
                # handoff `_instantiate` uses for a freshly built object,
                # and for the same reason.
                if returned:
                    self.objects.protect_handoff(returned)
                # REVERSED, and it is observable: when several objects
                # die at the same moment their `__del__`s run in some
                # order, and CPython's is the reverse of the order the
                # frame acquired them. Measured against it -- two locals,
                # a list's elements, a comprehension's results -- and
                # forward order disagreed with all three where reverse
                # agrees with all three.
                for reg, ty in reversed(fr.func.registers.items()):
                    if ty is not T.PTR:
                        continue
                    val = fr.registers.get(reg)
                    if not val:
                        continue
                    self.objects.decref(val)
                # A BUFFER NO CALL EVER CLAIMED -- see `Frame.argbuf`.
                # `apy_print`'s array and anything else built the same way
                # but read by a call this does not recognise ends up here
                # rather than never being released at all.
                for held in fr.argbuf.values():
                    for h in held:
                        self.objects.decref(h)
                fr.argbuf.clear()

    def _analyze(self, fn: Function) -> tuple[bool, dict[int, int], frozenset[int]]:
        """Whether `fn` has a loop, how many times each `T.PTR` register is
        READ (an operand of some instruction, anywhere in the function) in
        total, and which registers are NAMED -- see below. Memoised per
        function -- purely static, so it never changes across calls.

        WHAT THIS BUYS: `Op.COPY` and `Op.STORE` -- `x = <expr>` and a
        global/attribute assignment -- read a source register whose value
        they hand to a longer-lived home, but that source register itself
        keeps counting as an owner (`put()`'s own incref when it was
        FIRST written) until something decrefs it. Nothing does, ordinarily,
        until the register's whole FRAME tears down -- which for a
        function is that call's return, and for a module's top-level code
        is the end of the program. `_consume` uses `read_count` to tell
        the LAST static read of a register from an earlier one, so that
        last read can retire the register's own reference right there
        instead of waiting for the frame to end.

        LOOPS ARE EXCLUDED ENTIRELY, for one function-wide reason: a
        register's STATIC read count is not its DYNAMIC one when a read
        sits inside a loop body -- the same instruction fires every
        iteration, and retiring the register's reference after the first
        pass would finalize something a later pass still needs to read.
        Detecting per-register whether a given read is actually inside a
        loop (rather than merely "this function has one somewhere") would
        recover the optimisation for the rest of a loopy function, but
        that is real control-flow analysis; ruling out the whole function
        is the cheap, safe version -- registers in it simply keep waiting
        for frame teardown, exactly as they did before this existed.

        NAMED REGISTERS ARE NEVER RETIRED AT THEIR LAST READ, and this is
        not an optimisation left on the table -- it is the difference
        between a compiler TEMPORARY and a Python VARIABLE, and `_consume`
        finalizing the wrong one is a real bug this analysis exists to
        prevent, not a missed case. `dynamic.py` gives every named local,
        parameter and closure slot ONE PERSISTENT register, written via
        `Op.COPY` for each assignment (`_dyn_store`) -- so `y = x` reads
        `x`'s register as `Op.COPY`'s SOURCE. If `x` happens not to be
        read again after that line, its read count reaches zero right
        there, and retiring it would drop `x`'s OWN reference the moment
        `y = x` runs -- even though `x` is still a live, bound name that
        CPython would not release until reassignment, `del`, or the
        function's return. `print(x.name)` is the same hazard through a
        CALL argument instead of a COPY source. Measured: both finalized
        an object at its last syntactic mention, one and sometimes two
        statements before CPython would have. So a register is NAMED --
        excluded from `_consume` entirely -- if it is ever a PARAMETER, or
        ever the `dst` of an `Op.COPY` anywhere in the function; a true
        temporary (a CALL's raw result, handed to exactly one COPY/STORE/
        CALL argument and never itself assigned INTO) is never `Op.COPY`'s
        destination and keeps the optimisation.
        """
        cached = self._fn_analysis.get(id(fn))
        if cached is not None:
            return cached
        has_loop = self._has_cycle(fn)
        read_count: dict[int, int] = {}
        named: set[int] = set(fn.params)
        # WHERE EACH REGISTER IS WRITTEN AND READ, by block. Only wanted
        # when there IS a loop -- see below -- but counting it always costs
        # one pass over a function that is being walked anyway.
        wrote: dict[int, set] = {}
        read_in: dict[int, set] = {}
        for blk in fn.blocks:
            for ins in blk.instructions:
                for reg in ins.args:
                    if fn.registers.get(reg) is T.PTR:
                        read_count[reg] = read_count.get(reg, 0) + 1
                        read_in.setdefault(reg, set()).add(blk.label)
                if ins.dst is not None:
                    wrote.setdefault(ins.dst, set()).add(blk.label)
                if ins.op is Op.COPY and ins.dst is not None:
                    named.add(ins.dst)
        if has_loop:
            # A LOOP DOES NOT HAVE TO DISABLE THE WHOLE FUNCTION, and it
            # used to. `_consume` retires a register at its last STATIC
            # read, which is the last DYNAMIC one only when no read can
            # happen again -- and a back edge means one can, so every
            # temporary in a function with a loop waited for frame
            # teardown. That is most functions worth caring about.
            #
            # WHAT A BACK EDGE ACTUALLY BREAKS is a register whose value
            # OUTLIVES an iteration: written before the loop, read inside
            # it, retired on the first pass and gone on the second. A
            # register written and read entirely within ONE BASIC BLOCK
            # cannot be that: a block has no branches, so every one of its
            # instructions runs on every visit, in order -- the last static
            # read in the block IS the last dynamic read of the value that
            # block's write produced. The next iteration writes a fresh
            # value, and `put()` restores the read budget when it does (see
            # `Frame.remaining`), so the count is per-value rather than per
            # invocation.
            #
            # Everything else in the function stays excluded, which keeps
            # this in the direction the scheme errs in: a register this
            # cannot prove local simply waits for teardown, as all of them
            # did before.
            # AND A BLOCK OFF THE CYCLE IS NOT IN THE LOOP AT ALL. The
            # rule above is about one block; this is about the rest of the
            # function. A block that lies on no cycle CANNOT BE VISITED
            # TWICE in one invocation -- revisiting it would need a path
            # from it back to itself, which is what being on a cycle
            # means -- so a register written and read only in such blocks
            # has a static read count that IS its dynamic one, exactly as
            # in a function with no loop anywhere.
            #
            # WHAT THIS RECOVERS is most of a loopy function, and the
            # commonest shape it recovers is a MODULE BODY: one `for`
            # anywhere in it used to stop every temporary in the whole
            # module from being retired, so a value passed to a call at
            # module level waited for the end of the program. Measured
            # twice while writing the tests for this file -- both times
            # the loop was three lines away from what was being measured
            # and had nothing to do with it.
            looping = self._looping_blocks(fn)
            read_count = {
                reg: n for reg, n in read_count.items()
                if (wrote.get(reg) and read_in.get(reg)
                    and not (wrote[reg] | read_in[reg]) & looping)
                or (len(wrote.get(reg, ())) == 1
                    and len(read_in.get(reg, ())) == 1
                    and wrote[reg] == read_in[reg])}
        result = (has_loop, read_count, frozenset(named))
        self._fn_analysis[id(fn)] = result
        return result

    def _looping_blocks(self, fn: Function) -> frozenset:
        """Every block of `fn` that lies ON A CYCLE, by label.

        WHAT IT IS FOR: `_analyze` needs to tell a register that can be
        read twice from one that cannot, and "this function has a loop
        somewhere" is far too coarse -- a `for` anywhere in a module body
        stopped every temporary in the whole module from retiring. A block
        off every cycle runs at most once per invocation, so a register
        confined to such blocks is as safe to retire as one in a function
        with no loop at all.

        KOSARAJU, because two ordinary depth-first walks are easier to
        read than one clever one and this runs once per function. The
        first orders the blocks by finish time; the second walks the
        REVERSED edges in that order, and each tree it grows is one
        strongly connected component. A component with more than one
        block is a cycle; a single block is one only if it branches to
        itself.

        MEMOISED with the rest of the analysis -- purely static, so it
        never changes across calls.
        """
        cached = self._fn_loops.get(id(fn))
        if cached is not None:
            return cached
        blocks = {b.label: b for b in fn.blocks}
        forward = {label: [s for s in blk.successors if s in blocks]
                   for label, blk in blocks.items()}
        backward: dict = {label: [] for label in blocks}
        for label, succs in forward.items():
            for one in succs:
                backward[one].append(label)

        def walk(graph, start, seen, out):
            """One iterative depth-first walk, appending on FINISH."""
            stack = [(start, 0)]
            seen.add(start)
            while stack:
                label, i = stack[-1]
                edges = graph[label]
                while i < len(edges) and edges[i] in seen:
                    i += 1
                if i < len(edges):
                    stack[-1] = (label, i + 1)
                    seen.add(edges[i])
                    stack.append((edges[i], 0))
                    continue
                stack.pop()
                out.append(label)

        order: list = []
        seen: set = set()
        for label in blocks:
            if label not in seen:
                walk(forward, label, seen, order)
        looping: set = set()
        seen = set()
        for label in reversed(order):
            if label in seen:
                continue
            group: list = []
            walk(backward, label, seen, group)
            if len(group) > 1:
                looping.update(group)
            elif label in forward.get(label, ()):
                # A BLOCK THAT BRANCHES TO ITSELF is a component of one
                # and is still a loop -- `while True: pass` compiles to
                # exactly that.
                looping.add(label)
        out = frozenset(looping)
        self._fn_loops[id(fn)] = out
        return out

    def _has_cycle(self, fn: Function) -> bool:
        """Does `fn`'s control-flow graph have an actual cycle -- some
        block reachable from itself along a real path -- reachable from
        `fn`'s entry block?

        NOT "does some edge point to an earlier block": this compiler's
        own bound/unbound-variable check (every read of a name that
        might be unassigned) emits an `UNBOUND` block that raises, calls
        `apy_fatal_if_error` (which never returns once an error is
        pending), and only THEN has an unconditional `jump` back to the
        `BOUND` block it belongs to -- a well-formed IR needs a
        terminator there even though control never reaches it. That jump
        points backward in block order on virtually every function that
        reads a possibly-unbound name, which is nearly all of them, and
        a plain "target index <= source index" check flagged it as a
        loop every time, disabling `_analyze`'s optimisation almost
        everywhere it mattered, including a plain top-level script.
        `BOUND` never leads back to that specific `UNBOUND` block, so it
        is not actually part of a cycle -- there is no path from `BOUND`
        back to it -- which is exactly what a real cycle test answers
        and a same-or-earlier-index test does not.

        Standard DFS back-edge test: a GRAY node is one on the current
        path (an ancestor, not yet fully explored); an edge to a GRAY
        node is a genuine back edge, and a graph has a cycle reachable
        from the start iff DFS from it finds one. Iterative, to not
        depend on Python's recursion limit for a function with an
        unusually large block count.
        """
        blocks = {b.label: b for b in fn.blocks}
        if not fn.blocks:
            return False
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {label: WHITE for label in blocks}
        start = fn.blocks[0].label
        # Each stack entry is (label, index of the next successor to try)
        # -- the standard "explicit stack" shape for an iterative DFS
        # that needs to resume a node after each child returns, the
        # same thing the call stack would do for a recursive version.
        succs_of: dict[str, list[str]] = {}
        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = GRAY
        while stack:
            label, idx = stack[-1]
            succs = succs_of.get(label)
            if succs is None:
                blk = blocks.get(label)
                succs = succs_of[label] = blk.successors if blk is not None else []
            advanced = False
            while idx < len(succs):
                nxt = succs[idx]
                idx += 1
                c = color.get(nxt, BLACK)   # an unknown label: nothing to visit
                if c == GRAY:
                    return True
                if c == WHITE:
                    color[nxt] = GRAY
                    stack[-1] = (label, idx)
                    stack.append((nxt, 0))
                    advanced = True
                    break
            if advanced:
                continue
            stack[-1] = (label, idx)
            color[label] = BLACK
            stack.pop()
        return False

    def _would_consume(self, fr: Frame, reg: int) -> bool:
        """Whether `_consume(fr, reg)` would actually retire the register,
        asked WITHOUT spending the read.

        `Op.STORE` into an argument buffer needs this because the transfer
        it does is an incref-then-consume PAIR: the buffer takes the
        reference the register gives up, and the value's total is
        unchanged. If the register is not going to give one up -- it is a
        named variable, or is read again later -- then the incref alone
        is a reference that did not exist before, which `sys.getrefcount`
        reads back and reports. Measured: `x = C(); sys.getrefcount(x)`
        answered 2 where CPython answers 1, purely because the call's own
        argument buffer had claimed one.
        """
        if fr.remaining is None or self.objects is None or reg in fr.named:
            return False
        left = fr.remaining.get(reg)
        return left is not None and left <= 1

    @staticmethod
    def _argbuf_base(fr: Frame, addr: int) -> int | None:
        """Which of this invocation's `alloca`s `addr` falls inside, or
        `None` for an address that is not one of them at all -- a raw
        pointer the program computed, or an interior address of something
        the host allocated. Searched newest-first, since the buffer being
        filled is always the one just made."""
        for start, end in reversed(fr.allocas):
            if start <= addr < end:
                return start
        return None

    def _release_argbuf(self, fr: Frame, values) -> None:
        """Drop what a buffer owned, once the call that read it has
        returned. `values` are the operand values of that call: any of
        them that IS a buffer base names a buffer whose turn is over.

        AFTER THE CALL, never before -- the callee has had its chance to
        take a counted reference of its own (a parameter binding, an
        attribute, a container), so what is released here is only ever
        the buffer's own.
        """
        if not fr.argbuf:
            return
        for one in values:
            # `is int`, because a float operand that happened to equal a
            # buffer's address would otherwise release it -- an address is
            # a small integer and a float register holds whatever the
            # program computed.
            if type(one) is not int or not one:
                continue
            for h in fr.argbuf.pop(one, ()) or ():
                self.objects.decref(h)

    def _interpreted(self, fn: Function) -> bool:
        """Whether `_call` will EXECUTE `fn`'s IR rather than hand it to
        the host object runtime -- the two branches at the top of `_call`,
        asked ahead of time.

        WHY THIS IS THE LINE `_consume` MAY CROSS. An argument temporary
        holds a counted reference that nothing retires until the frame
        ends, so `f(x); del x` left `x` alive for the rest of the run --
        `__del__` never ran at the moment CPython runs it, and
        `weakref.ref(x)` went on answering with the object after its last
        name was gone. Retiring it at the call is only safe if the CALLEE
        took its own counted reference to anything it keeps, and for an
        INTERPRETED function that is guaranteed by construction rather
        than by audit: `_call` increfs every `T.PTR` parameter on entry
        and decrefs it at teardown, so the value is counted for the whole
        call, and anything the body keeps past the return it keeps
        through a path that counts -- an attribute (`_attr_store`), a
        container (`_apy_seq_push`, `_dict_set`), a global (`Op.STORE`),
        a closure's cell (`_apy_cell_new`/`_apy_cell_set`), a default
        (`_apy_func_default`), or by returning it (`protect_handoff`).

        A HOST PRIMITIVE IS NOT COVERED and does not become so here.
        Those keep the rule they had: nothing is retired for them except
        the names on `_NON_RETAINING`, whose bodies have actually been
        read. There are two hundred of them and this is not an audit of
        two hundred functions -- it is the observation that the ONE
        function every interpreted call goes through already does the
        right thing.
        """
        if fn.external:
            return False
        return not (fn.name.startswith("apy_") and self._objects_own(fn.name))

    def _consume(self, fr: Frame, reg: int) -> None:
        """One static read of `reg` -- from `_analyze`'s count -- has just
        been spent. At the last one, this invocation's value for `reg`
        will never be read again (true only because `_analyze` found no
        loop in this function, so the static count IS the dynamic one),
        so the register's own reference is dropped here.

        CALLED AFTER the consuming instruction's OWN `put()`/incref of
        the same handle has already run: `x = t` and `t` are the SAME
        handle at that point, and dropping `t`'s reference FIRST would
        read as zero and finalize an object one line away from gaining
        the new owner `put()` just gave it.
        """
        if fr.remaining is None or self.objects is None:
            # NO OBJECT RUNTIME: a `T.PTR` register in a program that
            # never touches `apy_*` at all is a raw address (see `put()`'s
            # own "one pointer type for both" comment) -- `_analyze` does
            # not know that when it builds `read_count`, since it has no
            # way to tell a handle-shaped program from an address-shaped
            # one ahead of time. Nothing to consume in that case.
            return
        if reg in fr.named:
            # A PYTHON VARIABLE, not a temporary -- see `_analyze`'s own
            # comment on why this register's LAST static read is not
            # when CPython would drop it, and never retired here.
            return
        left = fr.remaining.get(reg)
        if left is None:
            return
        left -= 1
        fr.remaining[reg] = left
        if left <= 0:
            v = fr.registers.get(reg)
            if v:
                self.objects.decref(v)
                # CLEARED, not merely counted down: `_call`'s frame
                # teardown decrefs every `T.PTR` register still holding a
                # value, with no idea a register's reference was already
                # retired here -- left as `v`, it would be decref'd a
                # SECOND time at teardown, one too many, finalizing an
                # object that is (from this frame's point of view) still
                # alive through whatever `reg` was copied/stored into.
                fr.registers[reg] = 0

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
                # SHADOW REFCOUNTING'S ONE CHOKE POINT for every register in
                # the interpreter: every instruction with a `dst` writes it
                # through here, so hooking this one function -- rather than
                # each of the several dozen opcode handlers above and below
                # -- covers every local/temporary's lifecycle uniformly. See
                # `ObjectHost.incref`/`decref` for what happens at each end.
                #
                # `fr.func.registers[dst]`, NOT `ty`: `ty` is documented
                # (module.py) to be the OPERAND type for a comparison, not
                # the result's -- `apy_x == apy_y` has `ty is T.PTR` but
                # writes an `i1`, and treating that `0`/`1` as a handle
                # would decref whatever handle happened to equal 0 or 1.
                # The register's OWN declared type has no such ambiguity:
                # it is fixed for the register's whole life (module.py).
                #
                # KNOWN, ACCEPTED IMPRECISION: a `ptr` register can ALSO
                # hold a raw memory address (an `alloca`, a global's
                # address) rather than an object-runtime handle -- the IR
                # has one pointer type for both, and nothing here tells them
                # apart. `ObjectHost.incref`/`decref` are no-ops for a
                # number that is not a handle THIS RUN has minted, so this
                # is silent almost always; the one way it can go wrong is a
                # raw address numerically coinciding with a handle that
                # genuinely exists right now, which only a program mixing
                # heavy `alloca` use with the object runtime in the same
                # function risks. Every bundled stdlib module -- the
                # differential suite this exists to serve -- is ordinary
                # dynamic Python and never allocas, so this does not reach
                # them.
                if self.objects is not None and fr.func.registers.get(ins.dst) is T.PTR:
                    old = R.get(ins.dst)
                    # NEW BEFORE OLD. `x = x` -- or anything that hands
                    # this register back its own current value, a tuple
                    # swap's `a[i], a[j] = a[j], a[i]` included -- has
                    # `v == old`: decrefing first would pass through a
                    # transient zero and finalize an object that is, by
                    # the end of this one assignment, exactly as alive as
                    # it was at the start.
                    # A NEW VALUE MEANS NEW READS. See `Frame.budget`:
                    # the countdown belongs to the VALUE in the register,
                    # not to the register across the whole invocation, and
                    # a loop writes a fresh one on every pass.
                    if fr.remaining is not None and ins.dst in fr.budget:
                        fr.remaining[ins.dst] = fr.budget[ins.dst]
                    if v:
                        self.objects.incref(v)
                        # LIFTS ANY `_instantiate`-STYLE PIN now that the
                        # register itself is a real reference -- see
                        # `ObjectHost.settle`. A freshly constructed
                        # object's handle rides pinned, not decref'd,
                        # all the way from `_instantiate` up through
                        # every host frame in between (none of which
                        # this interpreter's refcounting hooks touch)
                        # to exactly this store; settling anywhere else
                        # would either release too early (a nested call
                        # still in flight) or never (nothing else calls
                        # it).
                        self.objects.settle(v)
                    if old:
                        self.objects.decref(old)
                R[ins.dst] = v
            elif v and self.objects is not None and ty is T.PTR:
                # NO DESTINATION AT ALL -- a call whose result the program
                # discards, `f()` written as a statement. Nothing will
                # ever store this value, so nothing else would lift the
                # pin `_call`'s teardown put on a returned value, and a
                # pin nothing lifts is an object that never finalizes.
                # Lifted here instead, which is also where CPython
                # finalizes a discarded temporary: at the end of the
                # statement that produced it.
                self.objects.release_handoff(v)
            return None

        if op is Op.CONST:
            return put(float(ins.imm) if ty.is_float else _wrap(int(ins.imm), ty))
        if op is Op.COPY:
            src = ins.args[0]
            res = put(a(0))
            # See `_consume`: retires the SOURCE register's own reference
            # once this was its last static read, now that `put()` above
            # has already given the destination its own.
            self._consume(fr, src)
            return res
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
            res = put(1 if _compare(op, ty, a(0), a(1)) else 0)
            # See `_consume`. SAFE HERE FOR THE REASON `Op.CALL`'S
            # ARGUMENTS WERE NOT: a comparison is executed by this
            # interpreter itself and retains nothing -- it reads two
            # values and writes an `i1` -- so there is no callee whose
            # own storage behaviour would have to be audited first,
            # which is exactly what made the call-argument version of
            # this unsafe. This is the MACHINE-level comparison; a
            # Python-level `x is y` lowers to an `apy_is` CALL instead
            # and is retired through `_NON_RETAINING`, for the same
            # reason arrived at the same way.
            self._consume(fr, ins.args[0])
            if len(ins.args) > 1:
                self._consume(fr, ins.args[1])
            return res

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
            addr = self.mem.alloc(int(ins.imm))
            if self.objects is not None and fr.remaining is not None:
                # ITS EXTENT, so a store into slot 2 of an argument array
                # can be traced back to the array -- `Op.STORE` only ever
                # sees the offset address. Recorded for every `alloca`,
                # not just argument buffers: nothing here can tell them
                # apart, and one that never receives a handle simply never
                # gets an entry in `Frame.argbuf`.
                #
                # THE SAME GATE `Op.STORE`'s buffer branch has. A function
                # with a loop has no `remaining` table, so that branch
                # never runs for it and this list would only grow --
                # one entry per `alloca` per iteration, read by nothing.
                fr.allocas.append((addr, addr + max(1, int(ins.imm))))
            return put(addr)
        if op is Op.LOAD:
            return put(self.mem.read(int(a(0)), ty))
        if op is Op.STORE:
            addr = int(a(1))
            # THE SAME CHOKE POINT AS `put()`, for a `ptr`-typed value
            # landing in MEMORY rather than a register -- a global (`b =
            # Foo(...)` at module scope lowers to `global_addr`+`store`,
            # never through a register `dst`) or an address-taken local.
            # Without this a global holding the only reference to an
            # object never triggers `decref` on reassignment or `del`,
            # so nothing here ever finalizes at the moment a Python
            # program would observe it -- only ever (if at all) whenever
            # something else happens to touch the same handle.
            #
            # `ty`, NOT A DECLARED SLOT TYPE: unlike a register, a raw
            # memory address has no type of its own in this IR (`Global`
            # carries only a byte size -- see `module.py`) -- but unlike
            # `put()`'s `ins.dst` case, `Op.STORE`'s `ty` IS unambiguously
            # the type of the value being stored (`self.mem.write` above
            # already trusts it for that), so there is no equivalent of
            # the comparison-operand ambiguity `put()` has to route
            # around. The same accepted imprecision applies as there: a
            # `ptr` store can be a raw address rather than a handle, and
            # nothing here tells them apart -- see `put()`'s own comment.
            #
            # ONLY A GLOBAL'S ADDRESS (`addr < self._globals_end`), not
            # every `Op.STORE` -- an `alloca`'d address is this opcode's
            # OTHER use, chiefly the argument buffer every `apy_call`-style
            # call builds (`%b = ptr.alloca 8; ptr.store %arg, %b`, then
            # `apy_call(fn, %b, 1)`): that buffer is written once and never
            # read again by anything this scheme tracks, so hooking it
            # here increfed the ARGUMENT on every single call it was
            # passed to, with NOTHING ever decrefing that phantom
            # ownership -- an unbounded leak, not merely a delayed one,
            # confirmed by `sys.getrefcount` climbing on every call
            # touching the same object and by a value passed once as a
            # plain function argument never finalizing at all. Ordinary
            # locals never reach here regardless (`dynamic.py` gives them
            # a register, written via `Op.COPY` -- see `put()`), so
            # narrowing this to globals loses nothing else.
            #
            # THE BUFFER IS AN OWNER TOO, though a short-lived one -- see
            # `Frame.argbuf`, and the branch below. What was wrong before
            # was never the incref; it was that nothing released it.
            if (self.objects is not None and ty is T.PTR
                    and addr >= self._globals_end and fr.remaining is not None):
                v = a(0)
                self.mem.write(addr, ty, v)
                if v and int(v) in self.objects._refcount:
                    # OWNERSHIP MOVES from the register to the buffer, so
                    # the value's count is unchanged across the pair and
                    # cannot dip through zero in between -- which is the
                    # whole reason the incref comes first and the register
                    # is retired second. `apy_call`'s arguments are the
                    # shape this exists for: `weakref.ref(x)`, `C(x)` and
                    # `obj.m(x)` all pass through a buffer, and until this
                    # the register that loaded `x` held a reference until
                    # the enclosing frame ended -- so `del x` afterwards
                    # finalized nothing and the weakref went on answering.
                    base = self._argbuf_base(fr, addr)
                    if base is not None and self._would_consume(fr, ins.args[0]):
                        self.objects.incref(int(v))
                        fr.argbuf.setdefault(base, []).append(int(v))
                        self._consume(fr, ins.args[0])
                return None
            if self.objects is not None and ty is T.PTR and addr < self._globals_end:
                old = self.mem.read(addr, ty)
                v = a(0)
                self.mem.write(addr, ty, v)
                # NEW BEFORE OLD -- see `put()`'s own comment: `old == v`
                # (storing a global's own current value back into it) must
                # not decref through a transient zero before the matching
                # incref lands.
                if v:
                    self.objects.incref(int(v))
                    self.objects.settle(int(v))
                if old:
                    self.objects.decref(int(old))
                # See `_consume`: the SOURCE register (`ins.args[0]`) held
                # its own reference from whenever it was written; now that
                # the address just got its own (above), retire the
                # register's if this was its last static read.
                self._consume(fr, ins.args[0])
                return None
            self.mem.write(addr, ty, a(0))
            return None
        if op is Op.OFFSET:
            return put(int(a(0)) + int(a(1)))

        if op is Op.CALL and ins.sym in _NON_RETAINING:
            callee = self.module.function(ins.sym)
            if callee is None:
                raise Trap(f"call to unknown function {ins.sym!r}")
            args = [R[x] for x in ins.args]
            res = put(self._call(callee, args))
            self._release_argbuf(fr, args)
            # See `_NON_RETAINING`: the one narrow exception to the rule
            # below, for calls whose bodies have been READ and keep
            # nothing.
            for reg in ins.args:
                self._consume(fr, reg)
            return res
        if op is Op.CALL:
            callee = self.module.function(ins.sym)
            if callee is None:
                raise Trap(f"call to unknown function {ins.sym!r}")
            args = [R[x] for x in ins.args]
            res = put(self._call(callee, args))
            if self.objects is not None:
                self._release_argbuf(fr, args)
            if self._interpreted(callee):
                for reg in ins.args:
                    self._consume(fr, reg)
            elif (ins.sym in _CALLEE_FIRST and args
                  and self.objects is not None
                  and self.objects.callee_keeps_nothing(args[0])):
                # THE CALLEE ONLY, and only when the value can be certified
                # -- see `ObjectHost.callee_keeps_nothing`. The other
                # arguments of a dynamic call are an ADDRESS and a COUNT,
                # machine words `_consume` would ignore anyway; the Python
                # arguments live in the argument buffer and are retired by
                # `_release_argbuf` above.
                self._consume(fr, ins.args[0])
            return res
        if op is Op.CALL_PTR:
            idx = int(a(0)) & ~_FUNC_TAG
            callee = self.module.functions[idx]
            args = [R[x] for x in ins.args[1:]]
            res = put(self._call(callee, args))
            if self.objects is not None:
                self._release_argbuf(fr, args)
            # See `_interpreted` -- the same rule as `Op.CALL`, and it is
            # this opcode that carries every call the frontend could not
            # resolve statically.
            if self._interpreted(callee):
                for reg in ins.args[1:]:
                    self._consume(fr, reg)
            return res

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

#: Object-runtime calls whose ARGUMENT temporaries `_consume` may retire
#: at their last read. `_interpreted` explains why a call into compiled
#: Python needs no such list; these are the HOST primitives, which do,
#: because each one's body is its own answer.
#:
#: THE RULE FOR ADDING A NAME: read the whole body and confirm that
#: anything it KEEPS from its arguments, it takes its own counted
#: reference to -- or that it keeps nothing at all. Not "keeps nothing":
#: a list that increfs what it appends is exactly as safe, because the
#: value survives on the list's reference once the register drops its
#: own. What is NOT safe is a primitive that stashes a value somewhere
#: uncounted, and retiring the register then finalizes something still
#: reachable -- the one direction this whole scheme refuses to be wrong
#: in. Anything unread stays out.
#:
#: - `apy_is` keeps nothing: it dereferences both handles to validate
#:   them, compares the two handle NUMBERS, and answers a bool. It earns
#:   its place because `x is None` is how a program asks whether
#:   something is still there, and `r() is None` is the entire point of a
#:   `weakref` -- the deref's own result temporary, compared once and
#:   never used again, otherwise pinned the very object being asked
#:   about until the frame ended.
#: - `apy_seq_push`, `apy_set_add` and `apy_dict_set` each incref what
#:   they store (`_apy_seq_push`, `_apy_set_push`, `_dict_set`), which is
#:   what makes `xs = [Foo()]; xs.clear()` finalize the `Foo` at the
#:   `clear()` rather than at the end of the enclosing frame.
#: - `apy_cell_new`/`apy_cell_set` incref what goes in the box, which is
#:   what made retiring a call argument safe at all -- see
#:   `Interpreter._interpreted`.
#:
#: - `apy_setattr` was the notable absentee and is now certified, branch
#:   by branch, because `x.attr = v` is how a program hands a value to an
#:   object and leaving it out made every attribute-held value finalize at
#:   frame teardown instead of at the store. What the audit found:
#:
#:   * a `__setattr__` OVERRIDE calls interpreted Python, which counts its
#:     parameters by construction -- the same argument `_interpreted`
#:     makes for every ordinary call, made once for all of them;
#:   * a DATA DESCRIPTOR's `__set__` is a user method, so the same
#:     argument covers it -- except when the setter is a `Native`, which
#:     no program is known to write and which `_pin_for_native`
#:     (`objects/host.py`) pins rather than certifies;
#:   * an `Instance`, an `Exc`, a `Class` and a `Func` all store through
#:     `_attr_store`, which increfs the new value and decrefs the old,
#:     exactly as `_dict_set` does for a dict;
#:   * `C.__name__ = ...` writes a plain `str` into a field, and a `str`
#:     handle is never tracked at all;
#:   * everything else raises without storing.
#: - THE PURE INSPECTORS, read one body at a time like the rest. Each
#:   answers a fresh value or a machine word FROM its arguments and stores
#:   none of them anywhere: `_apy_truth` and `_apy_len` answer an int,
#:   `_apy_hash` and `_apy_hash_raw_of` a number, `_apy_repr`/`_apy_str`/
#:   `_apy_text_of` a NEW string, and `_cmpop`'s six answer whatever the
#:   comparison came to. Where one consults a user hook -- `__bool__`,
#:   `__len__`, `__hash__`, `__repr__`, `__eq__` -- that is a call into
#:   interpreted Python, which counts its parameters by construction, so
#:   `_interpreted`'s argument covers those the same way it covers every
#:   ordinary call.
#:
#:   WHY THESE AND NOT THE OTHER HUNDRED AND EIGHTY: because these are the
#:   ones a program reaches with a value it is about to stop using.
#:   `repr(g)` on a module-level global minted a temporary the frame never
#:   retired, so `sys.getrefcount` read one high per call and anything
#:   held only that way waited for teardown. A LOCAL never showed it --
#:   the register holding the binding IS the argument -- which is why the
#:   shape hid until a global was measured.
#:
#:   `apy_getattr` IS DELIBERATELY ABSENT from this group though it looks
#:   like one of them: reading a method off an object builds a BOUND
#:   function that holds the receiver, and whether that field is counted is
#:   a separate question from this one.
#: The dynamic-call primitives whose FIRST argument is the callable. They
#: are not on `_NON_RETAINING` -- what a call does with its arguments is the
#: callee's business, not this list's -- but the callee SLOT can be retired
#: on its own when the value in it is one `callee_keeps_nothing` certifies.
_CALLEE_FIRST = frozenset({
    "apy_call", "apy_call_kw", "apy_call_spread", "apy_call_spread_kw",
})

_NON_RETAINING = frozenset({
    "apy_is",
    "apy_seq_push", "apy_set_add", "apy_dict_set",
    "apy_cell_new", "apy_cell_set",
    "apy_setattr",
    "apy_truth", "apy_len", "apy_raw_len",
    "apy_hash", "apy_hash_raw_of",
    "apy_repr", "apy_str", "apy_text_of",
    "apy_eq", "apy_ne", "apy_lt", "apy_le", "apy_gt", "apy_ge",
    "apy_eq_raw_of",
})


def _arith(op: Op, ty: T.Type, x, y):
    if ty.is_float:
        if op is Op.ADD: return x + y
        if op is Op.SUB: return x - y
        if op is Op.MUL: return x * y
        if op is Op.DIV:
            # IEEE, WHICH MEANS DIVIDING BY ZERO IS AN ANSWER. The opcode
            # table says "IEEE on f*" and every backend's `/` on a double
            # already produces an infinity or a NaN, so trapping here made the
            # interpreter the one execution path that disagreed -- and the
            # suite compares the paths against each other. A language whose
            # `x / 0.0` raises lowers a CHECK before the divide; that is the
            # frontend's business and not this opcode's.
            if y == 0:
                if x != x or x == 0:
                    return float("nan")
                sign = -1.0 if (x < 0) != (math.copysign(1.0, y) < 0) else 1.0
                return sign * float("inf")
            return x / y
        if op is Op.REM:
            if y == 0:
                return float("nan")
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
