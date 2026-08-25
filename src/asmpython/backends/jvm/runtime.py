"""The methods the generated class needs but the IR does not contain.

Two kinds, and they are here for the same reason:

    memory      `$ld64`, `$st32`, `$blit` ... the IR's flat address space, read
                and written through a byte array
    host        `put_int`, `print_float`, `putchar` ... the functions a frontend
                emits calls to and something has to define

For a machine backend the second kind is C, compiled once and linked in
(`asmpython.objects.support`). There is nothing to link here -- a class file is
the whole program -- so they are written as bytecode, and this module is where
that happens rather than in the middle of the code generator.

EMITTED ON DEMAND. `Runtime.need()` records a name and returns it; only what
was asked for is defined. A class carrying eleven memory helpers a program
never calls is not wrong, but it is what makes generated output tiresome to
read in `javap`, and reading the output is how this backend gets debugged.

WHAT THESE MUST AGREE WITH. `put_float` is not `System.out.print(double)`:
Python's float repr is the shortest decimal that reads back as the same double,
switching to exponent form at a fixed exponent, and Java's is a different rule
with a different switch point. `$repr` is a transcription of `py_repr_double`
in `asmpython.objects.support`, because a program's output must not depend on
which backend compiled it.
"""
from __future__ import annotations

from .classfile import ACC_PRIVATE, ACC_PUBLIC, ACC_STATIC, ClassBuilder, \
    ITEM_DOUBLE, ITEM_INTEGER, ITEM_LONG, MethodBuilder
# The floor's NAMES only. What each does is written in bytecode below, and the
# contracts are in `objects/floor.py` -- one list, so a fourth function cannot
# be satisfied here without being declared there.
from ...objects.floor import FLOOR as _FLOOR

#: Where the IR's memory lives, and its descriptor.
MEM = "$mem"
MEM_DESC = "[B"
#: The alloca stack pointer. A `long` so it is the same type as every other
#: address; it indexes an array, so the low 31 bits are all that is ever used.
SP = "$sp"
SP_DESC = "J"
#: The heap bump pointer, for `plat_heap`. Grows UP from just above the
#: globals, towards the alloca stack growing down from the top -- so the two
#: can only meet after the whole array is used rather than immediately.
BRK = "$brk"
BRK_DESC = "J"

#: How much of `$mem` sits between the globals and the alloca stack, available
#: to `plat_heap`. Zero unless a program actually calls it; see
#: `emit._layout_globals`.
HEAP_BYTES = 1 << 22

#: Bytes of `alloca` space. The IR frees an alloca when its function returns,
#: which this backend implements by restoring a saved stack pointer -- so this
#: is a high-water mark for live frames, not a total, and a megabyte is deep
#: recursion over sizeable frames.
#:
#: HERE RATHER THAN IN `emit.py` because `plat_heap` needs it too: the heap
#: stops where the stack's reserve begins, and the two numbers have to be the
#: same number. `emit.py` imports it.
STACK_BYTES = 1 << 20

_STRING = "java/lang/String"
_SB = "java/lang/StringBuilder"
_PRINTSTREAM = "java/io/PrintStream"
_STR_V = ("object", _STRING)


def _obj(name: str) -> tuple[str, str]:
    return ("object", name)


class Runtime:
    """The support methods one generated class needs.

    Holds no state beyond "which ones were asked for", so the order a code
    generator discovers them in does not matter.
    """

    def __init__(self, cls: ClassBuilder, owner: str) -> None:
        self.cls = cls
        self.owner = owner
        self._needed: list[str] = []

    def need(self, name: str) -> str:
        """Record that `name` is called, and return it.

        Written to be used inline -- `m.invoke("static", owner, rt.need("$ld64"),
        "(J)J")` -- so a call site cannot ask for one helper and emit a call to
        another.
        """
        if name not in _METHODS:
            raise KeyError(f"no runtime method {name!r}")
        if name not in self._needed:
            self._needed.append(name)
            for dep in _METHODS[name][2]:
                self.need(dep)
        return name

    def descriptor(self, name: str) -> str:
        return _METHODS[name][0]

    def wanted(self, name: str) -> bool:
        """Was this method asked for? Read by `<clinit>`, which has to set up
        the handle table only when something calls into Java."""
        return name in self._needed

    def invoke(self, m: MethodBuilder, name: str) -> None:
        """Call a runtime method, marking it needed and getting its descriptor
        from the one table that defines it."""
        m.invoke("static", self.owner, self.need(name), self.descriptor(name))

    @property
    def provides(self) -> frozenset[str]:
        """The host functions this module can define. Anything else external is
        a link error, and this is what the backend checks against."""
        return _HOST_NAMES

    def emit(self) -> None:
        """Define every method that was asked for."""
        # Sorted, so two runs over the same module produce byte-identical
        # classes: the discovery order depends on which instruction was visited
        # first, which is not something anyone should have to reason about when
        # diffing output.
        declared: set[str] = set()
        for name in sorted(self._needed):
            for field, descriptor in _SCRATCH.get(name, ()):
                # Several methods share the handle table, and a field declared
                # twice is a `ClassFormatError` at load rather than anything
                # the writer notices.
                if field in declared:
                    continue
                declared.add(field)
                self.cls.field(field, descriptor)
            descriptor, build, _ = _METHODS[name]
            access = (ACC_PUBLIC | ACC_STATIC if name in _HOST_NAMES
                      else ACC_PRIVATE | ACC_STATIC)
            m = self.cls.method(name, descriptor, access)
            build(self, m)


# ── memory ──────────────────────────────────────────────────────────────────
#
# Addresses are byte indices into `$mem`. A pointer is a `long` because the IR
# says pointers are 64 bits, and it is narrowed to an `int` exactly once per
# access -- at the array index -- rather than being carried as an int and
# widened, so that pointer arithmetic in the IR and pointer arithmetic here
# cannot disagree about wrapping.

def _mem_at(rt: Runtime, m: MethodBuilder, index_slot: int, byte: int) -> None:
    """Push `$mem` and the index of `base + byte`, ready for an array op."""
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("i", index_slot)
    if byte:
        m.push_int(byte)
        m.op("iadd")


def _load_bytes(rt: Runtime, m: MethodBuilder, count: int, *,
                as_long: bool) -> None:
    """Assemble `count` little-endian bytes from `$mem` into an int or a long.

    Little-endian because the target says so (`Target.little_endian`), and
    because the interpreter that defines what a program means reads memory the
    same way -- a backend that chose the other order would produce programs
    that disagree with `asmpython run` only for code that stores one width and
    loads another.
    """
    m.load("l", 0)
    m.op("l2i")
    m.store("i", 2)                       # int index = (int) address
    for k in range(count):
        _mem_at(rt, m, 2, k)
        m.op("baload")
        if as_long:
            m.op("i2l")
            m.push_long(0xFF)
            m.op("land")
            if k:
                m.push_int(8 * k)
                m.op("lshl")
                m.op("lor")
        else:
            m.push_int(0xFF)
            m.op("iand")
            if k:
                m.push_int(8 * k)
                m.op("ishl")
                m.op("ior")


def _loader(count: int, *, signed: bool = False, as_long: bool = False):
    def build(rt: Runtime, m: MethodBuilder) -> None:
        m.max_locals = 3
        _load_bytes(rt, m, count, as_long=as_long)
        if signed and count == 1:
            m.op("i2b")
        elif signed and count == 2:
            m.op("i2s")
        m.op("lreturn" if as_long else "ireturn")
    return build


def _storer(count: int, *, from_long: bool):
    def build(rt: Runtime, m: MethodBuilder) -> None:
        value_slot = 2
        index_slot = 4 if from_long else 3
        m.max_locals = index_slot + 1
        m.load("l", 0)
        m.op("l2i")
        m.store("i", index_slot)
        for k in range(count):
            _mem_at(rt, m, index_slot, k)
            if from_long:
                m.load("l", value_slot)
                if k:
                    m.push_int(8 * k)
                    m.op("lushr")
                m.op("l2i")
            else:
                m.load("i", value_slot)
                if k:
                    m.push_int(8 * k)
                    m.op("iushr")
            m.op("i2b")
            m.op("bastore")
        m.op("return")
    return build


def _blit(rt: Runtime, m: MethodBuilder) -> None:
    """Copy a string's characters into memory as bytes.

    How a global's initialised bytes get into `$mem`. The alternative -- a
    `bastore` per byte in `<clinit>` -- is eight bytes of bytecode per byte of
    data and runs into the 64 KB method limit on a program with a few kilobytes
    of string constants. A constant-pool string costs about one byte per byte
    and the loop is three instructions.

    Each character is one byte's value, 0..255; `emit.py` builds the string
    that way and nothing else calls this.
    """
    m.max_locals = 6
    m.frame_locals = [ITEM_LONG, _STR_V, ITEM_INTEGER, ITEM_INTEGER,
                      ITEM_INTEGER]
    m.load("l", 0)
    m.op("l2i")
    m.store("i", 3)                                     # int at = (int) address
    m.load("a", 2)
    m.invoke("virtual", _STRING, "length", "()I")
    m.store("i", 4)                                     # int n = s.length()
    m.push_int(0)
    m.store("i", 5)                                     # int k = 0

    m.mark("loop")
    m.load("i", 5)
    m.load("i", 4)
    m.jump("if_icmpge", "done")
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("i", 3)
    m.load("i", 5)
    m.op("iadd")
    m.load("a", 2)
    m.load("i", 5)
    m.invoke("virtual", _STRING, "charAt", "(I)C")
    m.op("i2b")
    m.op("bastore")
    m.load("i", 5)
    m.push_int(1)
    m.op("iadd")
    m.store("i", 5)
    m.jump("goto", "loop")
    m.mark("done")
    m.op("return")


# ── host functions ──────────────────────────────────────────────────────────

def _stdout(m: MethodBuilder) -> None:
    m.getstatic("java/lang/System", "out", f"L{_PRINTSTREAM};")


def _put_int(rt: Runtime, m: MethodBuilder) -> None:
    m.max_locals = 2
    _stdout(m)
    m.load("l", 0)
    m.invoke("virtual", _PRINTSTREAM, "print", "(J)V")
    m.op("return")


def _newline(m: MethodBuilder) -> None:
    """A single `\\n` byte.

    NOT `println`, which writes the platform's line separator -- so on Windows
    the same program printed `\\r\\n` here and `\\n` from every other backend,
    and a differential test comparing the two saw every line differ.
    """
    _stdout(m)
    m.push_int(ord("\n"))
    m.invoke("virtual", _PRINTSTREAM, "write", "(I)V")


def _print_int(rt: Runtime, m: MethodBuilder) -> None:
    m.max_locals = 2
    _stdout(m)
    m.load("l", 0)
    m.invoke("virtual", _PRINTSTREAM, "print", "(J)V")
    _newline(m)
    m.op("return")


def _put_bool(rt: Runtime, m: MethodBuilder) -> None:
    """`print(True)` is `True`, not `1`.

    A bool is an integer to the machine and is not one to Python, and the
    difference is in the rendering rather than in the value -- so it lives in
    the function that renders, here and in the C runtime both.
    """
    m.max_locals = 3
    m.frame_locals = [ITEM_LONG, _STR_V]
    m.push_string("False")
    m.store("a", 2)
    m.load("l", 0)
    m.push_long(0)
    m.op("lcmp")
    m.jump("ifeq", "done")
    m.push_string("True")
    m.store("a", 2)
    m.mark("done")
    _stdout(m)
    m.load("a", 2)
    m.invoke("virtual", _PRINTSTREAM, "print", f"(L{_STRING};)V")
    m.op("return")


def _put_none(rt: Runtime, m: MethodBuilder) -> None:
    _stdout(m)
    m.push_string("None")
    m.invoke("virtual", _PRINTSTREAM, "print", f"(L{_STRING};)V")
    m.op("return")


# ── the platform floor ──────────────────────────────────────────────────────
# THREE FUNCTIONS, and `objects/floor.py` holds the contracts. Everything above
# this line in this file is a PYTHON operation a backend should not have to
# know -- `put_bool` knows how Python spells a true value -- and everything
# below it knows nothing about any language. Stage 6 of docs/INERT-RUNTIME.md
# deletes the first group and keeps this one.

def _plat_write(rt: Runtime, m: MethodBuilder) -> None:
    """`plat_write(fd, buf, n)` -> bytes written, or -1.

    `PrintStream.write(byte[], int, int)` writes the RAW BYTES, which is what
    the contract says and what makes this work for UTF-8: `print(char)` would
    re-encode, so a runtime emitting UTF-8 a buffer at a time would produce
    mojibake for everything above 127. The same reason `putchar` above uses
    `write(int)`.

    The explicit `flush` matches the C's `fflush`. Without it the interleaving
    of stdout and stderr depends on whether output is going to a terminal or a
    pipe, and a differential test against another backend sees the lines
    arrive in a different order.
    """
    m.max_locals = 7
    m.frame_locals = [ITEM_LONG, ITEM_LONG, ITEM_LONG, _obj(_PRINTSTREAM)]
    # THE STREAM IS STORED BEFORE THE FIRST BRANCH, not where it is chosen.
    # One stack map describes every label in a method, so a local it mentions
    # must be live at every branch TARGET -- and `bad` is reached from the
    # checks below. Assigning it later verified as "top is not assignable to
    # PrintStream", which names the slot rather than the jump that skipped it.
    _stdout(m)
    m.store("a", 6)
    # A BAD ADDRESS IS A FAILED WRITE, NOT A CRASH: the contract says -1, and
    # an out-of-range length would otherwise be an
    # ArrayIndexOutOfBoundsException -- which reaches the user as a Java stack
    # trace naming a method they never wrote.
    m.load("l", 4)                                      # n < 0
    m.push_long(0)
    m.op("lcmp")
    m.jump("iflt", "bad")
    m.load("l", 2)                                      # buf < 0
    m.push_long(0)
    m.op("lcmp")
    m.jump("iflt", "bad")
    m.load("l", 2)                                      # buf + n > mem.length
    m.load("l", 4)
    m.op("ladd")
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.op("arraylength")
    m.op("i2l")
    m.op("lcmp")
    m.jump("ifgt", "bad")

    # THE STREAM GOES IN A LOCAL rather than staying on the stack across the
    # branch: a label needs a stack map, and the builder writes one for an
    # EMPTY stack. Every jump target here is reached with nothing on it.
    m.load("l", 0)                                      # fd == 2 -> stderr
    m.push_long(2)
    m.op("lcmp")
    m.jump("ifne", "have")
    m.getstatic("java/lang/System", "err", f"L{_PRINTSTREAM};")
    m.store("a", 6)
    m.mark("have")

    m.load("a", 6)
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("l", 2)
    m.op("l2i")
    m.load("l", 4)
    m.op("l2i")
    m.invoke("virtual", _PRINTSTREAM, "write", "([BII)V")
    m.load("a", 6)
    m.invoke("virtual", _PRINTSTREAM, "flush", "()V")
    m.load("l", 4)
    m.op("lreturn")

    m.mark("bad")
    m.push_long(-1)
    m.op("lreturn")


def _plat_exit(rt: Runtime, m: MethodBuilder) -> None:
    """`plat_exit(code)`, which does not return.

    `System.exit` really does not return, so the contract is met exactly here
    -- but the `return` after it is still emitted, because the verifier
    requires every path out of a method to end in one and it cannot see that
    `System.exit` never comes back.
    """
    m.max_locals = 2
    _stdout(m)
    m.invoke("virtual", _PRINTSTREAM, "flush", "()V")
    m.load("l", 0)
    m.op("l2i")
    m.invoke("static", "java/lang/System", "exit", "(I)V")
    m.op("return")


def _plat_heap(rt: Runtime, m: MethodBuilder) -> None:
    """`plat_heap(n)` -> a fresh region of n bytes, or null.

    A bump pointer into the same `byte[]` everything else lives in, between
    the globals and the alloca stack -- which is why the contract says regions
    need not be CONTIGUOUS but says nothing about them being separate
    allocations: here they happen to be adjacent, and an allocator that
    assumed that would break on the C backend, where each one is a `malloc`.

    Eight-byte aligned, because the caller will store an i64 at the base and
    the IR's loads and stores do not promise anything about unaligned access.
    """
    m.max_locals = 6
    m.frame_locals = [ITEM_LONG, ITEM_LONG, ITEM_LONG]
    # BOTH LOCALS LIVE BEFORE THE FIRST BRANCH, for the reason spelled out in
    # `_plat_write`: one stack map covers every label, so a slot it names must
    # hold a long at each jump target and `null` is reached from the test below.
    m.push_long(0)
    m.store("l", 2)
    m.push_long(0)
    m.store("l", 4)

    m.load("l", 0)
    m.push_long(0)
    m.op("lcmp")
    m.jump("ifle", "null")

    m.load("l", 0)                                      # want = align8(n)
    m.push_long(7)
    m.op("ladd")
    m.push_long(-8)
    m.op("land")
    m.store("l", 2)
    m.getstatic(rt.owner, BRK, BRK_DESC)                # at = $brk
    m.store("l", 4)

    m.load("l", 4)                                      # at + want > the
    m.load("l", 2)                                      # alloca stack's floor?
    m.op("ladd")
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.op("arraylength")
    m.op("i2l")
    m.push_long(STACK_BYTES)
    m.op("lsub")
    m.op("lcmp")
    m.jump("ifgt", "null")

    m.load("l", 4)
    m.load("l", 2)
    m.op("ladd")
    m.putstatic(rt.owner, BRK, BRK_DESC)
    m.load("l", 4)
    m.op("lreturn")

    m.mark("null")
    m.push_long(0)
    m.op("lreturn")


def _putchar(rt: Runtime, m: MethodBuilder) -> None:
    """Write one byte and return it, as C's does.

    `PrintStream.write(int)` rather than `print(char)`: the IR's `putchar`
    carries a byte, and printing it as a character would re-encode it -- so a
    frontend writing UTF-8 one byte at a time would get mojibake for anything
    above 127.
    """
    m.max_locals = 2
    _stdout(m)
    m.load("l", 0)
    m.op("l2i")
    m.push_int(0xFF)
    m.op("iand")
    m.invoke("virtual", _PRINTSTREAM, "write", "(I)V")
    m.load("l", 0)
    m.op("lreturn")


def _print_str(rt: Runtime, m: MethodBuilder) -> None:
    """Write the NUL-terminated bytes at an address.

    The bytes go out unchanged rather than through a decode/encode round trip:
    a frontend that put UTF-8 in memory gets UTF-8 out, on a JVM whose default
    charset is anything at all.
    """
    m.max_locals = 4
    m.frame_locals = [ITEM_LONG, ITEM_INTEGER, _obj(MEM_DESC)]
    m.push_int(0)
    m.store("i", 2)                                     # int n = 0
    m.op("aconst_null")
    m.store("a", 3)

    m.mark("scan")
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("l", 0)
    m.op("l2i")
    m.load("i", 2)
    m.op("iadd")
    m.op("baload")
    m.jump("ifeq", "found")
    m.load("i", 2)
    m.push_int(1)
    m.op("iadd")
    m.store("i", 2)
    m.jump("goto", "scan")

    m.mark("found")
    m.load("i", 2)
    m.newarray_byte()
    m.store("a", 3)
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("l", 0)
    m.op("l2i")
    m.load("a", 3)
    m.push_int(0)
    m.load("i", 2)
    m.invoke("static", "java/lang/System", "arraycopy",
             "(Ljava/lang/Object;ILjava/lang/Object;II)V")
    _stdout(m)
    m.load("a", 3)
    m.push_int(0)
    m.load("i", 2)
    m.invoke("virtual", _PRINTSTREAM, "write", "([BII)V")
    m.op("return")


def _put_float(rt: Runtime, m: MethodBuilder) -> None:
    m.max_locals = 2
    _stdout(m)
    m.load("d", 0)
    rt.invoke(m, "$repr")
    m.invoke("virtual", _PRINTSTREAM, "print", f"(L{_STRING};)V")
    m.op("return")


def _print_float(rt: Runtime, m: MethodBuilder) -> None:
    m.max_locals = 2
    _stdout(m)
    m.load("d", 0)
    rt.invoke(m, "$repr")
    m.invoke("virtual", _PRINTSTREAM, "print", f"(L{_STRING};)V")
    _newline(m)
    m.op("return")


def _format(m: MethodBuilder, precision_slot: int, suffix: str, *,
            less_one: bool = False) -> None:
    """`String.format(ROOT, "%." + precision + suffix, v)` for the double in 0.

    `less_one` because `%e`'s precision counts digits AFTER the point while the
    search counts SIGNIFICANT digits, so the two differ by one. Getting that
    wrong does not fail: the round-trip check simply succeeds a digit early,
    and every printed float comes out one digit short -- 1e+16 as `1.0e+16` and
    0.35000000000000003 as `0.3500000000000000`.

    `Locale.ROOT` and not the default: a JVM started in a locale that writes
    `3,5` would print numbers a Python program never could, and the failure
    would only appear on the machine with that locale set.
    """
    m.getstatic("java/util/Locale", "ROOT", "Ljava/util/Locale;")
    m.new(_SB)
    m.op("dup")
    m.invoke("special", _SB, "<init>", "()V")
    m.push_string("%.")
    m.invoke("virtual", _SB, "append", f"(L{_STRING};)L{_SB};")
    m.load("i", precision_slot)
    if less_one:
        m.push_int(1)
        m.op("isub")
    m.invoke("virtual", _SB, "append", f"(I)L{_SB};")
    m.push_string(suffix)
    m.invoke("virtual", _SB, "append", f"(L{_STRING};)L{_SB};")
    m.invoke("virtual", _SB, "toString", f"()L{_STRING};")
    m.push_int(1)
    m.anewarray("java/lang/Object")
    m.op("dup")
    m.push_int(0)
    m.load("d", 0)
    m.invoke("static", "java/lang/Double", "valueOf", "(D)Ljava/lang/Double;")
    m.op("aastore")
    m.invoke("static", _STRING, "format",
             f"(Ljava/util/Locale;L{_STRING};[Ljava/lang/Object;)L{_STRING};")


def _repr(rt: Runtime, m: MethodBuilder) -> None:
    """Python's `repr` of a double: the shortest decimal that reads back equal.

    A transcription of `py_repr_double` in `asmpython.objects.support`, and it has
    to stay one. `Double.toString` is also shortest-round-trip but formats to
    Java's rule -- `1.0E16` where Python writes `1e+16`, and plain notation out
    to 1e7 where Python goes to 1e16 -- so using it would make a program's
    output depend on which backend built it.

    The search asks for one significant digit, then two, until the result
    parses back to the same double. That loop also absorbs the one real
    difference between the formatters: Java's rounds halves away from zero and
    C's rounds them to even, and a digit count that rounds differently simply
    fails to round-trip and the loop takes another digit.

        0 .. 1   v          (double)
        2        buf        (String)  the `%e` form; later the exponent's sign
        3        digits     (int)
        4        epos       (int)
        5        exp10      (int)
        6        scratch    (int)     `dec`, then the exponent's magnitude
        7        out        (String)
        8        pad        (String)

    EVERY LOCAL IS WRITTEN HERE AT ENTRY and the operand stack is empty at
    every label below. Those are the two invariants `classfile.py` relies on to
    write a stack map without a dataflow analysis; a `StringBuilder` left on
    the stack across a branch breaks the second one, and the symptom is a
    `VerifyError` about a frame nobody wrote.
    """
    m.max_locals = 9
    m.frame_locals = [ITEM_DOUBLE, _STR_V, ITEM_INTEGER, ITEM_INTEGER,
                      ITEM_INTEGER, ITEM_INTEGER, _STR_V, _STR_V]
    for slot in (3, 4, 5, 6):
        m.push_int(0)
        m.store("i", slot)
    for slot in (2, 7, 8):
        m.push_string("")
        m.store("a", slot)

    # nan and inf have no decimal form, and Python spells them in lower case.
    m.load("d", 0)
    m.invoke("static", "java/lang/Double", "isNaN", "(D)Z")
    m.jump("ifeq", "finite?")
    m.push_string("nan")
    m.op("areturn")

    m.mark("finite?")
    m.load("d", 0)
    m.invoke("static", "java/lang/Double", "isInfinite", "(D)Z")
    m.jump("ifeq", "search")
    m.push_string("inf")
    m.store("a", 7)
    m.load("d", 0)
    m.push_double(0.0)
    m.op("dcmpg")
    m.jump("ifge", "sign?")
    m.push_string("-inf")
    m.store("a", 7)
    m.mark("sign?")
    m.load("a", 7)
    m.op("areturn")

    # digits = 1; while (!roundtrips(digits) && digits < 17) digits++
    m.mark("search")
    m.push_int(1)
    m.store("i", 3)
    m.mark("try")
    _format(m, 3, "e", less_one=True)
    m.store("a", 2)
    m.load("a", 2)
    m.invoke("static", "java/lang/Double", "parseDouble",
             f"(L{_STRING};)D")
    m.load("d", 0)
    m.op("dcmpl")
    m.jump("ifeq", "shortest")
    m.load("i", 3)
    m.push_int(17)
    m.jump("if_icmpge", "shortest")
    m.load("i", 3)
    m.push_int(1)
    m.op("iadd")
    m.store("i", 3)
    m.jump("goto", "try")

    # `%e` writes `d.dddde[+-]dd`, so the exponent starts two characters past
    # the `e` and its sign is the character between.
    m.mark("shortest")
    m.load("a", 2)
    m.push_int(ord("e"))
    m.invoke("virtual", _STRING, "indexOf", "(I)I")
    m.store("i", 4)
    m.load("a", 2)
    m.load("i", 4)
    m.push_int(2)
    m.op("iadd")
    m.invoke("virtual", _STRING, "substring", f"(I)L{_STRING};")
    m.invoke("static", "java/lang/Integer", "parseInt", f"(L{_STRING};)I")
    m.store("i", 5)
    m.load("a", 2)
    m.load("i", 4)
    m.push_int(1)
    m.op("iadd")
    m.invoke("virtual", _STRING, "charAt", "(I)C")
    m.push_int(ord("-"))
    m.jump("if_icmpne", "positive")
    m.load("i", 5)
    m.op("ineg")
    m.store("i", 5)
    m.mark("positive")

    # Python's switch to exponent form is at a FIXED exponent, which is where
    # the obvious `%g` implementation of all this goes wrong: `%g` switches
    # when the exponent reaches the PRECISION, so it moves with however many
    # digits the value happened to need.
    m.load("i", 5)
    m.push_int(-4)
    m.jump("if_icmplt", "exponent")
    m.load("i", 5)
    m.push_int(16)
    m.jump("if_icmpge", "exponent")

    # dec = digits - 1 - exp10, floored at zero
    m.load("i", 3)
    m.push_int(1)
    m.op("isub")
    m.load("i", 5)
    m.op("isub")
    m.store("i", 6)
    m.load("i", 6)
    m.jump("ifge", "fixed")
    m.push_int(0)
    m.store("i", 6)
    m.mark("fixed")
    _format(m, 6, "f")
    m.store("a", 7)
    m.load("a", 7)
    m.push_int(ord("."))
    m.invoke("virtual", _STRING, "indexOf", "(I)I")
    m.jump("ifge", "return")
    # A float always looks like one: `2.0`, never `2`.
    m.new(_SB)
    m.op("dup")
    m.invoke("special", _SB, "<init>", "()V")
    m.load("a", 7)
    m.invoke("virtual", _SB, "append", f"(L{_STRING};)L{_SB};")
    m.push_string(".0")
    m.invoke("virtual", _SB, "append", f"(L{_STRING};)L{_SB};")
    m.invoke("virtual", _SB, "toString", f"()L{_STRING};")
    m.store("a", 7)
    m.mark("return")
    m.load("a", 7)
    m.op("areturn")

    # mantissa + "e" + sign + at least two exponent digits. Two, because
    # Python writes `1e-05`; and not three, because the C library mingw links
    # against writes `1e-005` and the two disagree.
    #
    # Every piece is settled into a local BEFORE the builder is created, so
    # that no branch happens with anything on the operand stack.
    m.mark("exponent")
    m.load("a", 2)
    m.push_int(0)
    m.load("i", 4)
    m.invoke("virtual", _STRING, "substring", f"(II)L{_STRING};")
    m.store("a", 7)                                     # out = mantissa

    m.load("i", 5)
    m.store("i", 6)
    m.load("i", 5)
    m.jump("ifge", "magnitude")
    m.load("i", 5)
    m.op("ineg")
    m.store("i", 6)                                     # scratch = |exp10|
    m.mark("magnitude")

    m.push_string("e+")
    m.store("a", 2)
    m.load("i", 5)
    m.jump("ifge", "signed")
    m.push_string("e-")
    m.store("a", 2)
    m.mark("signed")

    m.push_string("")
    m.store("a", 8)
    m.load("i", 6)
    m.push_int(10)
    m.jump("if_icmpge", "padded")
    m.push_string("0")
    m.store("a", 8)
    m.mark("padded")

    m.new(_SB)
    m.op("dup")
    m.invoke("special", _SB, "<init>", "()V")
    for slot in (7, 2, 8):
        m.load("a", slot)
        m.invoke("virtual", _SB, "append", f"(L{_STRING};)L{_SB};")
    m.load("i", 6)
    m.invoke("virtual", _SB, "append", f"(I)L{_SB};")
    m.invoke("virtual", _SB, "toString", f"()L{_STRING};")
    m.op("areturn")


# ── correctly-rounded integer powers ────────────────────────────────────────
#
# `x ** n` in DOUBLE-DOUBLE: each value is an unevaluated sum of two doubles,
# giving ~106 bits of significand, so exponentiation by squaring keeps the bits
# an ordinary double would drop and only the final add rounds. `Math.pow` is
# not this: it is allowed a whole ulp of error, and CPython's is correctly
# rounded, so a program doing `x ** 3` would print a different last digit here
# than under `asmpython run` or the C backend.
#
# A transcription of `POW_INT_C` in `asmpython.objects.support`, down to the
# guards -- every one of them is there because something returned a nan
# without it, and the comments there say which case.
#
# C returns the second half of each pair through a pointer. There is no such
# thing here, so each helper returns its high half and leaves the low half in a
# static field, read by the caller before anything can overwrite it. One field
# per helper rather than one shared: `$dd_mul` calls both of the others, and a
# single field would make the order of two reads load-bearing.

#: 2**27 + 1, the Veltkamp splitting constant.
_SPLIT = 134217729.0
#: Above this the split itself overflows; see the C source.
_SPLIT_LIMIT = 1.3e300


def _not_finite(m: MethodBuilder, slot: int, label: str) -> None:
    """Jump to `label` if the double in `slot` is infinite or nan.

    `x - x != 0` rather than a library call: it is what the C does, and it
    keeps this working on a JVM whose `Double.isFinite` (Java 8) postdates the
    class-file version someone asked for.
    """
    m.load("d", slot)
    m.load("d", slot)
    m.op("dsub")
    m.push_double(0.0)
    m.op("dcmpl")                       # nan compares as -1, which is != 0
    m.jump("ifne", label)


def _two_sum(rt: Runtime, m: MethodBuilder) -> None:
    """Exact sum: returns the rounded sum, leaves the error in `$tsE`."""
    m.max_locals = 8
    m.frame_locals = [ITEM_DOUBLE] * 4
    m.push_double(0.0)
    m.store("d", 4)
    m.push_double(0.0)
    m.store("d", 6)
    m.load("d", 0)
    m.load("d", 2)
    m.op("dadd")
    m.store("d", 4)                                     # S = a + b
    _not_finite(m, 4, "overflowed")
    m.load("d", 4)
    m.load("d", 0)
    m.op("dsub")
    m.store("d", 6)                                     # bb = S - a
    m.load("d", 0)
    m.load("d", 4)
    m.load("d", 6)
    m.op("dsub")
    m.op("dsub")                                        # a - (S - bb)
    m.load("d", 2)
    m.load("d", 6)
    m.op("dsub")                                        # b - bb
    m.op("dadd")
    m.putstatic(rt.owner, "$tsE", "D")
    m.load("d", 4)
    m.op("dreturn")
    # Once the sum is infinite, `S - a` is inf minus inf and the error term is
    # a nan that then travels as the low half of the pair.
    m.mark("overflowed")
    m.push_double(0.0)
    m.putstatic(rt.owner, "$tsE", "D")
    m.load("d", 4)
    m.op("dreturn")


def _two_prod(rt: Runtime, m: MethodBuilder) -> None:
    """Exact product: returns the rounded product, error in `$tpE`."""
    m.max_locals = 16
    m.frame_locals = [ITEM_DOUBLE] * 8
    for slot in (4, 6, 8, 10, 12, 14):
        m.push_double(0.0)
        m.store("d", slot)
    m.load("d", 0)
    m.load("d", 2)
    m.op("dmul")
    m.store("d", 4)                                     # P = a * b

    # The split overflows for a large operand, and what came out was not an
    # infinity but a NAN -- which then contaminated the high half too.
    for slot in (0, 2):
        m.load("d", slot)
        m.push_double(_SPLIT_LIMIT)
        m.op("dcmpl")
        m.jump("ifgt", "plain")
        m.load("d", slot)
        m.push_double(-_SPLIT_LIMIT)
        m.op("dcmpg")
        m.jump("iflt", "plain")
    _not_finite(m, 4, "plain")

    for value, high, low in ((0, 8, 10), (2, 12, 14)):
        m.push_double(_SPLIT)
        m.load("d", value)
        m.op("dmul")
        m.store("d", 6)                                 # c = SPLIT * a
        m.load("d", 6)
        m.load("d", 6)
        m.load("d", value)
        m.op("dsub")
        m.op("dsub")
        m.store("d", high)                              # ah = c - (c - a)
        m.load("d", value)
        m.load("d", high)
        m.op("dsub")
        m.store("d", low)                               # al = a - ah

    m.load("d", 8)
    m.load("d", 12)
    m.op("dmul")
    m.load("d", 4)
    m.op("dsub")                                        # ah*bh - P
    m.load("d", 8)
    m.load("d", 14)
    m.op("dmul")
    m.op("dadd")                                        # + ah*bl
    m.load("d", 10)
    m.load("d", 12)
    m.op("dmul")
    m.op("dadd")                                        # + al*bh
    m.load("d", 10)
    m.load("d", 14)
    m.op("dmul")
    m.op("dadd")                                        # + al*bl
    m.putstatic(rt.owner, "$tpE", "D")
    m.load("d", 4)
    m.op("dreturn")

    m.mark("plain")
    m.push_double(0.0)
    m.putstatic(rt.owner, "$tpE", "D")
    m.load("d", 4)
    m.op("dreturn")


def _dd_mul(rt: Runtime, m: MethodBuilder) -> None:
    """Multiply two double-doubles. High half returned, low half in `$ddL`."""
    m.max_locals = 12
    m.frame_locals = [ITEM_DOUBLE] * 6
    for slot in (8, 10):
        m.push_double(0.0)
        m.store("d", slot)
    m.load("d", 0)
    m.load("d", 4)
    rt.invoke(m, "$two_prod")
    m.store("d", 8)                                     # p
    m.getstatic(rt.owner, "$tpE", "D")
    m.store("d", 10)                                    # e

    # Once the high product has overflowed there is no correction left to
    # carry, and computing one anyway is how a nan gets in: the cross terms are
    # `inf * 0` as soon as one half is infinite and the other's low word is an
    # honest zero.
    _not_finite(m, 8, "overflowed")

    m.load("d", 10)
    m.load("d", 0)
    m.load("d", 6)
    m.op("dmul")                                        # ah * bl
    m.op("dadd")
    m.load("d", 2)
    m.load("d", 4)
    m.op("dmul")                                        # al * bh
    m.op("dadd")
    m.store("d", 10)
    m.load("d", 8)
    m.load("d", 10)
    rt.invoke(m, "$two_sum")
    m.getstatic(rt.owner, "$tsE", "D")
    m.putstatic(rt.owner, "$ddL", "D")
    m.op("dreturn")

    m.mark("overflowed")
    m.push_double(0.0)
    m.putstatic(rt.owner, "$ddL", "D")
    m.load("d", 8)
    m.op("dreturn")


def _pow_int(rt: Runtime, m: MethodBuilder) -> None:
    """`base ** n`, correctly rounded, for an integral `n` of either sign.

        0 .. 1   base      4 .. 5   rh      8 .. 9   bh     12 .. 13  m
        2 .. 3   n         6 .. 7   rl     10 .. 11  bl     14 .. 15  scratch
    """
    m.max_locals = 16
    m.frame_locals = [ITEM_DOUBLE, ITEM_LONG, ITEM_DOUBLE, ITEM_DOUBLE,
                      ITEM_DOUBLE, ITEM_DOUBLE, ITEM_LONG, ITEM_DOUBLE]
    m.push_double(1.0)
    m.store("d", 4)                                     # rh = 1.0
    m.push_double(0.0)
    m.store("d", 6)                                     # rl = 0.0
    m.load("d", 0)
    m.store("d", 8)                                     # bh = base
    m.push_double(0.0)
    m.store("d", 10)                                    # bl = 0.0
    m.push_double(0.0)
    m.store("d", 14)

    # The magnitude as UNSIGNED. Negating Long.MIN_VALUE gives itself, whose
    # unsigned reading is 2**63 -- which is exactly what the C computes, and
    # exactly why the C says so rather than negating a signed value.
    m.load("l", 2)
    m.store("l", 12)
    m.load("l", 2)
    m.push_long(0)
    m.op("lcmp")
    m.jump("ifge", "loop")
    m.load("l", 2)
    m.op("lneg")
    m.store("l", 12)

    # `x ** 0` is 1.0 for every x including nan, which is what an empty loop
    # gives; CPython agrees, and it is the one case where nan is not contagious.
    m.mark("loop")
    m.load("l", 12)
    m.push_long(0)
    m.op("lcmp")
    m.jump("ifeq", "collapse")
    m.load("l", 12)
    m.push_long(1)
    m.op("land")
    m.push_long(0)
    m.op("lcmp")
    m.jump("ifeq", "shift")
    m.load("d", 4)
    m.load("d", 6)
    m.load("d", 8)
    m.load("d", 10)
    rt.invoke(m, "$dd_mul")
    m.store("d", 4)
    m.getstatic(rt.owner, "$ddL", "D")
    m.store("d", 6)
    m.mark("shift")
    m.load("l", 12)
    m.push_int(1)
    m.op("lushr")                                       # unsigned: m >>= 1
    m.store("l", 12)
    m.load("l", 12)
    m.push_long(0)
    m.op("lcmp")
    m.jump("ifeq", "loop")
    m.load("d", 8)
    m.load("d", 10)
    m.load("d", 8)
    m.load("d", 10)
    rt.invoke(m, "$dd_mul")
    m.store("d", 8)
    m.getstatic(rt.owner, "$ddL", "D")
    m.store("d", 10)
    m.jump("goto", "loop")

    m.mark("collapse")
    m.load("l", 2)
    m.push_long(0)
    m.op("lcmp")
    m.jump("iflt", "reciprocal")
    m.load("d", 4)
    m.load("d", 6)
    m.op("dadd")
    m.op("dreturn")

    # A negative exponent is the reciprocal, and taking it as `1.0 / (rh + rl)`
    # rounds TWICE -- once collapsing the pair, once dividing. That second
    # rounding put 348 of 4000 random cases one ulp from CPython, which is the
    # whole reason this computes in double-double at all. So divide first and
    # correct with the exact residual.
    m.mark("reciprocal")
    m.load("d", 4)
    m.push_double(0.0)
    m.op("dcmpl")
    m.jump("ifeq", "plain")
    _not_finite(m, 4, "plain")
    m.load("d", 4)
    m.push_double(_SPLIT_LIMIT)
    m.op("dcmpl")
    m.jump("ifgt", "plain")
    m.load("d", 4)
    m.push_double(-_SPLIT_LIMIT)
    m.op("dcmpg")
    m.jump("iflt", "plain")

    m.push_double(1.0)
    m.load("d", 4)
    m.op("ddiv")
    m.store("d", 14)                                    # q = 1.0 / rh
    m.load("d", 4)
    m.load("d", 14)
    rt.invoke(m, "$two_prod")
    m.store("d", 8)                                     # p, reusing bh
    m.getstatic(rt.owner, "$tpE", "D")
    m.store("d", 10)                                    # e, reusing bl
    m.push_double(1.0)
    m.load("d", 8)
    m.op("dsub")
    m.load("d", 10)
    m.op("dsub")                                        # (1 - p) - e
    m.load("d", 6)
    m.load("d", 14)
    m.op("dmul")
    m.op("dsub")                                        # - rl * q
    m.load("d", 14)
    m.op("dmul")
    m.load("d", 14)
    m.op("dadd")                                        # q + q * r
    m.op("dreturn")

    # Zero, infinite, nan, or merely large enough that the split would
    # overflow. All four make the correction step produce a nan out of an
    # inf-minus-inf, so the plain divide stands.
    m.mark("plain")
    m.push_double(1.0)
    m.load("d", 4)
    m.load("d", 6)
    m.op("dadd")
    m.op("ddiv")
    m.op("dreturn")


# ── Java object handles ─────────────────────────────────────────────────────
#
# The IR has no reference type: its `ptr` is an index into a byte array, and a
# Java reference cannot be one. So a reference travels as a HANDLE -- an index
# into a list the class keeps -- and `$reg` and `$deref` are the two ends of
# that.
#
# NOTHING IS EVER RELEASED. A handle is live for as long as the program is, so
# a mod that creates objects in a loop keeps every one of them. That is a real
# limit and it is the honest one for a compiler with no notion of lifetime in
# its IR: the alternative is a reference count the source language cannot
# express. Index 0 is `null`, so a zero handle is as invalid as a zero address.

_OBJECTS = "$objs"
_OBJECTS_DESC = "Ljava/util/ArrayList;"
_ARRAYLIST = "java/util/ArrayList"


def objects_init(rt: Runtime, m: MethodBuilder) -> None:
    """Create the handle table. Called from `<clinit>`."""
    m.new(_ARRAYLIST)
    m.op("dup")
    m.invoke("special", _ARRAYLIST, "<init>", "()V")
    m.putstatic(rt.owner, _OBJECTS, _OBJECTS_DESC)
    m.getstatic(rt.owner, _OBJECTS, _OBJECTS_DESC)
    m.op("aconst_null")
    m.invoke("virtual", _ARRAYLIST, "add", "(Ljava/lang/Object;)Z")
    m.op("pop")                                     # handle 0 is null
    m.op("return")


def _reg(rt: Runtime, m: MethodBuilder) -> None:
    """Store a reference and return the handle standing for it."""
    m.max_locals = 1
    m.getstatic(rt.owner, _OBJECTS, _OBJECTS_DESC)
    m.load("a", 0)
    m.invoke("virtual", _ARRAYLIST, "add", "(Ljava/lang/Object;)Z")
    m.op("pop")
    m.getstatic(rt.owner, _OBJECTS, _OBJECTS_DESC)
    m.invoke("virtual", _ARRAYLIST, "size", "()I")
    m.push_int(1)
    m.op("isub")
    m.op("i2l")
    m.op("lreturn")


def _jstring(rt: Runtime, m: MethodBuilder) -> None:
    """A Java `String` from NUL-terminated UTF-8 at an address.

    The literal travels as BYTES IN A GLOBAL rather than inside the symbol
    naming this call, so a string survives `--emit-ir` and a rebuild -- and so
    that a symbol, which may hold only identifier characters, never has to hold
    arbitrary text.
    """
    m.max_locals = 4
    m.frame_locals = [ITEM_LONG, ITEM_INTEGER, _obj(MEM_DESC)]
    m.push_int(0)
    m.store("i", 2)
    m.op("aconst_null")
    m.store("a", 3)

    m.mark("scan")
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("l", 0)
    m.op("l2i")
    m.load("i", 2)
    m.op("iadd")
    m.op("baload")
    m.jump("ifeq", "found")
    m.load("i", 2)
    m.push_int(1)
    m.op("iadd")
    m.store("i", 2)
    m.jump("goto", "scan")

    m.mark("found")
    m.load("i", 2)
    m.newarray_byte()
    m.store("a", 3)
    m.getstatic(rt.owner, MEM, MEM_DESC)
    m.load("l", 0)
    m.op("l2i")
    m.load("a", 3)
    m.push_int(0)
    m.load("i", 2)
    m.invoke("static", "java/lang/System", "arraycopy",
             "(Ljava/lang/Object;ILjava/lang/Object;II)V")
    m.new(_STRING)
    m.op("dup")
    m.load("a", 3)
    m.getstatic("java/nio/charset/StandardCharsets", "UTF_8",
                "Ljava/nio/charset/Charset;")
    m.invoke("special", _STRING, "<init>",
             "([BLjava/nio/charset/Charset;)V")
    rt.invoke(m, "$reg")
    m.op("lreturn")


def _deref(rt: Runtime, m: MethodBuilder) -> None:
    """The reference a handle stands for."""
    m.max_locals = 2
    m.getstatic(rt.owner, _OBJECTS, _OBJECTS_DESC)
    m.load("l", 0)
    m.op("l2i")
    m.invoke("virtual", _ARRAYLIST, "get", "(I)Ljava/lang/Object;")
    m.op("areturn")


#: Static scratch a runtime method needs, as {method: [(field, descriptor)]}.
_SCRATCH: dict[str, list[tuple[str, str]]] = {
    "$reg": [(_OBJECTS, _OBJECTS_DESC)],
    "$deref": [(_OBJECTS, _OBJECTS_DESC)],
    "jvm$str": [(_OBJECTS, _OBJECTS_DESC)],
    "$two_sum": [("$tsE", "D")],
    "$two_prod": [("$tpE", "D")],
    "$dd_mul": [("$ddL", "D")],
}

#: name -> (descriptor, builder, dependencies)
_METHODS: dict[str, tuple[str, object, tuple[str, ...]]] = {
    "$ld8":   ("(J)I", _loader(1, signed=True), ()),
    "$ld8u":  ("(J)I", _loader(1), ()),
    "$ld16":  ("(J)I", _loader(2, signed=True), ()),
    "$ld16u": ("(J)I", _loader(2), ()),
    "$ld32":  ("(J)I", _loader(4), ()),
    "$ld64":  ("(J)J", _loader(8, as_long=True), ()),
    "$st8":   ("(JI)V", _storer(1, from_long=False), ()),
    "$st16":  ("(JI)V", _storer(2, from_long=False), ()),
    "$st32":  ("(JI)V", _storer(4, from_long=False), ()),
    "$st64":  ("(JJ)V", _storer(8, from_long=True), ()),
    "$blit":  (f"(JL{_STRING};)V", _blit, ()),
    "$repr":  (f"(D)L{_STRING};", _repr, ()),
    "put_int":     ("(J)V", _put_int, ()),
    "print_int":   ("(J)V", _print_int, ()),
    "put_bool":    ("(J)V", _put_bool, ()),
    "put_none":    ("()V", _put_none, ()),
    "put_float":   ("(D)V", _put_float, ("$repr",)),
    "print_float": ("(D)V", _print_float, ("$repr",)),
    "print_str":   ("(J)V", _print_str, ()),
    "putchar":     ("(J)J", _putchar, ()),
    "plat_write":  ("(JJJ)J", _plat_write, ()),
    "plat_exit":   ("(J)V", _plat_exit, ()),
    "plat_heap":   ("(J)J", _plat_heap, ()),
    "$two_sum":    ("(DD)D", _two_sum, ()),
    "$two_prod":   ("(DD)D", _two_prod, ()),
    "$dd_mul":     ("(DDDD)D", _dd_mul, ("$two_prod", "$two_sum")),
    "$reg":        ("(Ljava/lang/Object;)J", _reg, ()),
    "$deref":      ("(J)Ljava/lang/Object;", _deref, ()),
    "jvm$str":     ("(J)J", _jstring, ("$reg",)),
    "py_pow_int":  ("(DJ)D", _pow_int, ("$dd_mul", "$two_prod")),
}

#: The external symbols this module can satisfy. A CALL to anything else has no
#: definition anywhere in a class file, so the backend rejects it at compile
#: time rather than emitting a class that fails at load with a message naming a
#: method the user never wrote.
_HOST_NAMES = frozenset({
    "put_int", "print_int", "put_bool", "put_none", "put_float",
    "print_float", "print_str", "putchar", "py_pow_int",
}) | set(_FLOOR)

HOST_NAMES = _HOST_NAMES
