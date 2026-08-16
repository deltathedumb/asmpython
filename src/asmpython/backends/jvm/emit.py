"""The JVM backend: one class, one static method per IR function.

    asmpython build prog.py --backend jvm --java-version 21
    asmpython build prog.py --backend jvm --class-version 75
    java -jar prog.jar

The output is a single class file plus a manifest, packaged into a jar by the
`jar` toolchain. There is nothing to link and no runtime to install: the host
functions a frontend calls are emitted into the same class (`runtime.py`), so
the jar is the whole program.

WHICH CLASS-FILE VERSION IS A COMMAND-LINE OPTION, and `version.py` is the
whole of that decision -- `--class-version` wins over `--java-version` because
one names the number in the header and the other names a JVM that has to load
it. This backend only reads the answer.

THE THREE THINGS THE JVM DOES NOT HAVE
--------------------------------------
*Unsigned arithmetic.* The JVM has signed `int` and `long` and nothing else, so
`u32` and `u64` are carried as the same bit patterns in signed registers and
the operations that actually differ -- divide, remainder, shift-right, compare
-- are the only ones that do anything special. Ordering is fixed by flipping
the sign bit before a signed compare, which is exact and needs no library call.

*Narrow integers.* `i8` and `u16` are computed in `int` and truncated after
every operation that can overflow their width, so what a register holds always
matches what the IR says it holds. Skipping the truncation is invisible until
something compares two values that differ only above the type's width.

*Addresses.* `alloca`, `load`, `store` and `global_addr` need a flat address
space, and the JVM has a heap of objects instead. Memory is one `byte[]` and a
pointer is an index into it -- so pointer arithmetic is integer arithmetic and
the IR's byte offsets mean what they say. Address 0 is left unused so that a
null pointer is still an invalid one.

*Code addresses.* A method is not a value on the JVM and there is no
instruction that calls one by address, so `func_addr` yields a small integer
and `call_ptr` goes through a generated dispatcher that switches on it. See
`_number_functions`.

*A ceiling on method size.* A JVM method holds 65535 bytes of bytecode and
nothing makes one bigger, so a function past that is compiled as several: its
registers move into a `long[]`, each part returns the number of the part to run
next, and an entry method drives them. See `_SplitFunction`. That is measured
after the fact rather than predicted -- the ratio of IR to bytecode is not a
constant -- so an ordinary function pays nothing for it.

WHAT IS NOT HERE. The Python frontend's DYNAMIC path calls a runtime of 258
`apy_*` functions -- the object model: lists, dicts, strings, classes,
generators, exceptions -- which exists only as C. Nothing in a class file can
call it, and it is reported as `BackendUnsupported` naming the symbol, which is
the difference between "this compiler does not do that yet" and a crash. The
statically typed subset needs none of it.
"""
from __future__ import annotations

import os
from pathlib import Path

from ...backend.base import Backend, BackendUnsupported, Option, OptionError, \
    register
from ...diagnostics import warning
from ...ir import types as T
from ...ir.module import Function, Instruction, Module
from ...ir.opcodes import Op
from . import version
from .classfile import ACC_FINAL, ACC_PUBLIC, ACC_STATIC, ACC_SUPER, \
    ClassBuilder, ClassFileLimit, ITEM_DOUBLE, ITEM_FLOAT, ITEM_INTEGER, \
    ITEM_LONG, MAX_METHOD_BYTES, MethodBuilder
from .classpath import ClassFileError, ClassPath, parse_parameters
from .interop import Interop
from .runtime import (BRK, BRK_DESC, HEAP_BYTES, MEM, MEM_DESC, SP, SP_DESC,
                      STACK_BYTES, Runtime, objects_init)

#: Address 0 stays unused so that a null pointer dereference is an index into
#: nothing rather than a read of the first global.
FIRST_ADDRESS = 8

#: Everything in memory is 8-aligned. The IR reports each global's requirement
#: and honouring it exactly would pack better; on a byte array there is nothing
#: to pack against, and one rule is one thing to get wrong.
ALIGN = 8

#: Bytes of global data per constant-pool string. A character above 127 takes
#: two bytes in the class file's modified UTF-8, so this is half of the format's
#: 65535-byte limit, which makes the bound hold for any data at all.
BLOB_CHUNK = 16 * 1024

#: `Long.divideUnsigned` and friends arrived in Java 8. Targeting older than
#: that means running on a JVM that does not have them, so `u64` division is
#: refused there rather than emitting a call that fails at run time.
UNSIGNED_LONG_MATH_FROM = 52

_DESCRIPTOR = {"i": "I", "l": "J", "f": "F", "d": "D", "v": "V"}
_VTYPE = {"i": ITEM_INTEGER, "l": ITEM_LONG, "f": ITEM_FLOAT, "d": ITEM_DOUBLE}
_RETURN = {"i": "ireturn", "l": "lreturn", "f": "freturn", "d": "dreturn",
           "v": "return"}

_ARITH = {Op.ADD: "add", Op.SUB: "sub", Op.MUL: "mul"}
_BITWISE = {Op.AND: "and", Op.OR: "or", Op.XOR: "xor"}
_INT_BRANCH = {Op.EQ: "if_icmpeq", Op.NE: "if_icmpne", Op.LT: "if_icmplt",
               Op.LE: "if_icmple", Op.GT: "if_icmpgt", Op.GE: "if_icmpge"}
_ZERO_BRANCH = {Op.EQ: "ifeq", Op.NE: "ifne", Op.LT: "iflt", Op.LE: "ifle",
                Op.GT: "ifgt", Op.GE: "ifge"}
#: Which of the two float comparisons to use. They differ only in what they
#: answer for NaN -- `fcmpl` pushes -1 and `fcmpg` pushes 1 -- and the choice is
#: made per operator so that every ordered comparison against NaN comes out
#: false, which is what IEEE-754 and Python both require.
_FLOAT_CMP = {Op.EQ: "l", Op.NE: "l", Op.LT: "g", Op.LE: "g",
              Op.GT: "l", Op.GE: "l"}


def _kind(ty: T.Type) -> str:
    """The JVM storage class for an IR type: i, l, f, d or v."""
    if ty.is_void:
        return "v"
    if ty.is_float:
        return "d" if ty is T.F64 else "f"
    return "l" if ty.bits == 64 else "i"


def _descriptor(fn: Function) -> str:
    params = "".join(_DESCRIPTOR[_kind(fn.registers[p])] for p in fn.params)
    return f"({params}){_DESCRIPTOR[_kind(fn.ret)]}"


def _normalized(ty: T.Type, value) -> int:
    """`value` as this backend stores a `ty`.

    Narrow signed types are held sign-extended and narrow unsigned types
    zero-extended, so that a JVM `int` compares and prints as the IR value it
    stands for. At 32 and 64 bits there is no room for either, so the bit
    pattern is reinterpreted as signed -- a `u64` above 2**63 is a negative
    `long` holding the right bits.
    """
    if ty is T.I1:
        return 1 if int(value) & 1 else 0
    bits = ty.bits
    raw = int(value) & ((1 << bits) - 1)
    if (ty.is_signed or bits >= 32) and raw >> (bits - 1):
        raw -= 1 << bits
    return raw


def _kinds_of(params: str) -> list[str]:
    """The kind letters for a descriptor's parameter list.

    Only the primitive descriptors occur -- this backend has no object-typed
    parameters anywhere -- so one character is one parameter.
    """
    return [_KIND_OF[ch] for ch in params]


def _kind_of_descriptor(descriptor: str) -> str:
    return _KIND_OF[descriptor]


#: Descriptor letter -> the kind letter used throughout this module.
_KIND_OF = {"I": "i", "J": "l", "F": "f", "D": "d", "V": "v"}


def _class_name(module_name: str) -> str:
    """A Java class name for a module.

    The file is named after the class, so this decides the artifact's name too.
    Anything that is not an identifier character becomes one, because a module
    is named after a source file and `my-prog.py` is an ordinary thing to call
    one.
    """
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_"
                   for ch in module_name)
    safe = safe.strip("_")
    if not safe or safe[0].isdigit():
        safe = "M" + safe
    return safe[0].upper() + safe[1:]


def _method_name(symbol: str) -> str:
    """A JVM method name for an IR symbol.

    The format forbids `. ; [ / < >` outright. `$` is legal and is stripped
    anyway: `runtime.py` names its helpers with it, and reserving one character
    is cheaper than checking every IR symbol against a list that grows whenever
    a helper is added.
    """
    return "".join("_" if ch in ".;[/<>$" else ch for ch in symbol)


class JvmBackend(Backend):
    """IR to a JVM class file."""

    name = "jvm"
    description = "JVM class files, packaged as a runnable jar"
    default_target = "jvm"
    #: The emitted class carries its own entry point and its own host
    #: functions; there is no runtime object to link beside it.
    self_contained = True

    options = (
        Option("java-version",
               "the Java release the class must load on (8, 11, 17, 21, ...); "
               "resolved to the highest class-file version that release "
               "accepts",
               metavar="RELEASE"),
        Option("class-version",
               "the class-file major version to write, verbatim "
               "(52 is Java 8, 65 is Java 21); takes priority over "
               "--java-version",
               metavar="MAJOR"),
        Option("classpath",
               "jars, .jmod files or directories of class files whose "
               "packages become importable, separated by "
               f"{os.pathsep!r}; `import com.example.thing` then reaches them",
               metavar="PATH"),
    )

    def __init__(self, class_version: version.ClassVersion | None = None,
                 interop: Interop | None = None):
        self.class_version = class_version or version.resolve()
        #: The class path, as importable modules. The SAME object the frontend
        #: was published -- it records which Java operations were named, and
        #: `emit` reads that back to know what to generate.
        self.interop = interop

    @property
    def modules(self) -> dict:
        """What `import` can reach: every package on the class path.

        A property rather than the class attribute the interface documents,
        because this backend's answer depends on `--classpath` and so cannot
        exist until the options have been read.
        """
        return {} if self.interop is None else self.interop.modules()

    def java_classes(self, internal: str):
        """One Java class by its internal name, for an instance method call.

        `modules` answers "what is in this package"; this answers "what can I
        call on a value of this type", which is the question an expression like
        `my_block.setName(...)` asks and which no package listing can.
        """
        if self.interop is None:
            return None
        found = self.interop.classpath.classes.get(internal)
        return None if found is None else self.interop.class_table(found)

    def configure(self, values: dict[str, str], sink) -> Backend:
        """Read the options. Returns the backend to emit with.

        A NEW instance, not `self` mutated: the registry holds one shared
        backend object, and two compilations in one process must not see each
        other's flags.
        """
        try:
            resolved = version.resolve(
                class_version=values.get("class-version"),
                java_version=values.get("java-version"))
        except version.VersionError as exc:
            raise OptionError(str(exc)) from None
        if resolved.note:
            # Said out loud rather than left to be discovered: someone who
            # passes both options and gets the other one's answer has no way to
            # find that out from a class file.
            sink.report(warning("W9200", resolved.note))

        entries = [e for e in (values.get("classpath") or "").split(os.pathsep)
                   if e.strip()]
        interop = None
        if entries:
            path = ClassPath()
            for entry in entries:
                where = Path(entry.strip())
                if not where.exists():
                    raise OptionError(
                        f"--classpath entry {entry.strip()!r} does not exist")
                try:
                    found = path.add(where)
                except ClassFileError as exc:
                    raise OptionError(str(exc)) from None
                if not found:
                    sink.report(warning(
                        "W9201",
                        f"no class files in {entry.strip()!r}"))
            interop = Interop(path)
        return JvmBackend(resolved, interop)

    def emit(self, module: Module, target) -> dict[str, bytes]:
        return _Emitter(module, self.class_version, self.interop).run()


class _Emitter:
    """One module to one class file."""

    def __init__(self, module: Module, class_version: version.ClassVersion,
                 interop: Interop | None = None):
        self.module = module
        self.version = class_version
        #: The class path this module was analysed against, or None. Holds the
        #: Java operations the frontend named, which is what tells `_call`
        #: which externals are Java rather than host functions.
        self.interop = interop
        self.owner = _class_name(module.name)
        self.cls = ClassBuilder(self.owner, class_version.major)
        self.rt = Runtime(self.cls, self.owner)
        #: Global name -> byte offset in `$mem`.
        self.addresses: dict[str, int] = {}
        self.mem_size = 0
        #: Function name -> the integer a `func_addr` yields for it. Numbered
        #: from 1 so that 0 stays an invalid pointer, like address 0 does.
        self.function_ids: dict[str, int] = {}
        #: JVM descriptor -> the dispatcher method that calls through a
        #: pointer of that shape. Built on demand; see `_dispatcher`.
        self.dispatchers: dict[str, str] = {}

    # ── the whole class ─────────────────────────────────────────────────────
    def run(self) -> dict[str, bytes]:
        self._check_externals()
        self._number_functions()
        self._layout_globals()
        self.cls.field(MEM, MEM_DESC)
        self.cls.field(SP, SP_DESC)
        self.cls.field(BRK, BRK_DESC)

        for fn in self.module.defined_functions():
            _emit_function(self, fn)

        entry = self._entry_point()
        self._emit_dispatchers()
        # `<clinit>` LAST, though it runs first: what it has to initialise
        # depends on what the rest of the class turned out to need, and the
        # handle table is only there if something called into Java.
        self._clinit()
        self.rt.emit()

        try:
            artifacts = {f"{self.owner}.class": self.cls.serialize()}
        except ClassFileLimit as exc:
            # A ceiling of the format, not a bug: the program is valid and this
            # backend cannot express it, which is exactly what
            # BackendUnsupported means -- and it reaches the user as a
            # diagnostic naming the method rather than as a traceback out of a
            # serializer.
            raise BackendUnsupported(str(exc)) from None
        if entry:
            artifacts["META-INF/MANIFEST.MF"] = _manifest(self.owner)
        return artifacts

    def _check_externals(self) -> None:
        """Every imported symbol must be one this backend can define.

        Checked up front, before a single instruction is emitted, so that a
        program calling a function with no JVM implementation is told which
        function -- rather than producing a class that loads and then dies with
        a `NoSuchMethodError` naming a method the user never wrote.
        """
        interop = self.interop
        for fn in self.module.functions:
            if not fn.external:
                continue
            if interop is not None and fn.name.startswith("jvm$")                     and interop.lookup(fn.name) is not None:
                self._check_java_signature(fn, interop)
                continue
            if fn.name not in self.rt.provides:
                raise BackendUnsupported(
                    f"nothing defines {fn.name!r} on the JVM"
                    + ("\nthe Python frontend's dynamic path needs a runtime "
                       "this backend does not have yet; compile with "
                       "--backend c"
                       if fn.name.startswith("apy_") else
                       "\nonly the host functions in backends/jvm/runtime.py "
                       "are available"))
            expected = self.rt.descriptor(fn.name)
            got = _descriptor(fn)
            if expected != got:
                raise BackendUnsupported(
                    f"{fn.name} is declared {got} here and the JVM runtime "
                    f"defines it as {expected}")

    def _number_functions(self) -> None:
        """Give every address-taken function an integer, which is its address.

        THE JVM HAS NO ADDRESSABLE CODE. A method is not a value, there is no
        instruction that calls "whatever is at this address", and the nearest
        equivalents -- a MethodHandle, a lambda -- need a class-file version
        this backend does not want to require and an interface type per
        signature. An integer and a switch need neither, cost one comparison
        chain at the call site, and work at class-file version 45.

        Only functions whose address is TAKEN get a number: an id is a promise
        that some dispatcher can call it, and numbering the rest would grow
        every dispatcher with arms nothing can reach.
        """
        taken: list[str] = []
        for fn in self.module.functions:
            for _, ins in fn.instructions():
                if ins.op is Op.FUNC_ADDR and ins.sym not in taken:
                    taken.append(ins.sym)
        # Ordered by the MODULE, not by discovery: two builds of one program
        # should give a function the same address.
        order = {fn.name: i for i, fn in enumerate(self.module.functions)}
        for name in sorted(taken, key=lambda n: order.get(n, len(order))):
            self.function_ids[name] = len(self.function_ids) + 1

    def _dispatcher(self, descriptor: str) -> str:
        """The method that calls through a pointer of shape `descriptor`.

        One per signature rather than one per call site: the arms are the same
        for every call of that shape, and a program that calls through a
        pointer once tends to do it in a loop.
        """
        if descriptor not in self.dispatchers:
            mangled = descriptor.replace("(", "").replace(")", "_")
            self.dispatchers[descriptor] = f"$call${mangled}"
        return self.dispatchers[descriptor]

    def _emit_dispatchers(self) -> None:
        for descriptor, name in sorted(self.dispatchers.items()):
            self._emit_dispatcher(descriptor, name)

    def _emit_dispatcher(self, descriptor: str, name: str) -> None:
        """`(long fp, args...) -> result`: switch on the pointer, call, return.

        The arguments sit in the dispatcher's own locals, so the operand stack
        is empty at every arm -- which is what lets the stack map stay a
        constant here as everywhere else.
        """
        params = descriptor[1:descriptor.index(")")]
        ret = descriptor[descriptor.index(")") + 1:]
        m = self.cls.method(name, descriptor, ACC_STATIC)

        slots: list[tuple[str, int]] = []
        at, frame = 0, []
        for kind in _kinds_of(params):
            slots.append((kind, at))
            frame.append(_VTYPE[kind])
            at += 2 if kind in ("l", "d") else 1
        m.max_locals = at
        m.frame_locals = frame
        m.max_stack = 8 + at

        # The pointer is the first parameter, and it is a long because every
        # pointer is. Narrowing to an int here is safe for exactly the values
        # this backend hands out; anything else falls to the default arm.
        wanted = descriptor[descriptor.index(")") + 1:]
        arms = []
        for fn_name, fid in self.function_ids.items():
            fn = self.module.function(fn_name)
            if fn is None:
                continue
            target = (self.rt.descriptor(fn.name) if fn.external
                      else _descriptor(fn))
            if target == f"({params[1:]}){wanted}":
                arms.append((fid, f"a{fid}", fn))

        m.load("l", 0)
        m.op("l2i")
        m.lookupswitch("bad", [(fid, label) for fid, label, _ in arms])
        for fid, label, fn in arms:
            m.mark(label)
            for kind, slot in slots[1:]:
                m.load(kind, slot)
            if fn.external:
                self.rt.invoke(m, fn.name)
            else:
                m.invoke("static", self.owner, _method_name(fn.name),
                         _descriptor(fn))
            m.op(_RETURN[_kind_of_descriptor(ret)])

        # A pointer nobody handed out, or one of the wrong shape. Both are the
        # program's bug, and both have to be a definite failure rather than a
        # fall-through into whichever arm happens to be next.
        m.mark("bad")
        m.new("java/lang/IllegalStateException")
        m.op("dup")
        m.push_string(
            f"call through a pointer that is not a {descriptor} function")
        m.invoke("special", "java/lang/IllegalStateException", "<init>",
                 "(Ljava/lang/String;)V")
        m.op("athrow")

    def _check_java_signature(self, fn: Function, interop: Interop) -> None:
        """The IR's declaration of a Java call must match the method it names.

        Both sides come from `interop`, so a mismatch means the IR was written
        against a different class path than the one being compiled with --
        which is exactly what happens when `--emit-ir` output is rebuilt later
        against a newer jar, and it is far better caught here than as a
        `NoSuchMethodError` from a class that loaded.
        """
        want_params, want_ret = interop.signature_of(fn.name)
        got_params = [fn.registers[p].name for p in fn.params]
        got_ret = fn.ret.name
        if got_params != want_params or got_ret != want_ret:
            raise BackendUnsupported(
                f"{fn.name} is declared ({', '.join(got_params)}) -> {got_ret} "
                f"but the class path says ({', '.join(want_params)}) -> "
                f"{want_ret}"
                f"\nthe IR was built against a different class path")

    def _layout_globals(self) -> None:
        offset = FIRST_ADDRESS
        for g in self.module.globals:
            align = max(g.align or ALIGN, 1)
            offset = (offset + align - 1) // align * align
            self.addresses[g.name] = offset
            offset += max(g.size, len(g.data or b""), 1)
        # THE HEAP SITS BETWEEN the globals and the alloca stack, and only when
        # something asks for it: `plat_heap` is the only way to reach it, so a
        # program that never calls it should not carry four megabytes of array
        # it can never address. Read off the IR rather than off `Runtime.wanted`
        # because the layout is decided before any function body is emitted.
        self.heap_start = (offset + ALIGN - 1) // ALIGN * ALIGN
        wants_heap = any(ins.sym == "plat_heap"
                         for f in self.module.functions for b in f.blocks
                         for ins in b.instructions)
        self.mem_size = (self.heap_start
                         + (HEAP_BYTES if wants_heap else 0) + STACK_BYTES)

    def _clinit(self) -> None:
        """Allocate memory, load the globals into it, and set the stack top."""
        m = self.cls.method("<clinit>", "()V", ACC_STATIC)
        m.push_int(self.mem_size)
        m.newarray_byte()
        m.putstatic(self.owner, MEM, MEM_DESC)
        for g in self.module.globals:
            if not g.data:
                continue                    # a zero-filled region already is
            at = self.addresses[g.name]
            for start in range(0, len(g.data), BLOB_CHUNK):
                chunk = g.data[start:start + BLOB_CHUNK]
                m.push_long(at + start)
                m.push_string("".join(chr(b) for b in chunk))
                self.rt.invoke(m, "$blit")
        # The alloca stack grows DOWN from the top of memory, so it and the
        # globals can only meet after a megabyte of frames rather than
        # immediately.
        m.push_long(self.mem_size)
        m.putstatic(self.owner, SP, SP_DESC)
        # The heap bump pointer starts just above the globals and grows the
        # other way. Always initialised even when nothing calls `plat_heap`:
        # a field that exists but holds zero is a pointer into the reserved
        # first eight bytes, and a conditional `<clinit>` is how a
        # NoSuchFieldError gets written.
        m.push_long(self.heap_start)
        m.putstatic(self.owner, BRK, BRK_DESC)
        if self.rt.wanted("$reg") or self.rt.wanted("$deref"):
            objects_init(self.rt, m)                 # ends in `return`
            return
        m.op("return")

    def _entry_point(self) -> bool:
        """`public static void main(String[])`, if the module has an entry.

        Only for a `main` that takes no arguments. One taking parameters has
        nothing to be passed -- a JVM hands its entry point a `String[]` and
        the IR wants integers -- and inventing zeros would make a program that
        reads its arguments quietly compute the wrong thing.
        """
        fn = self.module.function("main")
        if fn is None or fn.external or fn.params:
            return False

        m = self.cls.method("main", "([Ljava/lang/String;)V",
                            ACC_PUBLIC | ACC_STATIC)
        m.max_locals = 2
        m.invoke("static", self.owner, _method_name("main"), _descriptor(fn))
        kind = _kind(fn.ret)
        if kind == "l":
            m.op("l2i")
        elif kind in ("f", "d"):
            m.op("pop2")                    # an exit status this is not
            m.push_int(0)
        elif kind == "v":
            m.push_int(0)
        m.store("i", 1)

        # `System.exit` does NOT flush, and `System.out` is buffered. Without
        # this, a program whose last output has no trailing newline loses it.
        m.getstatic("java/lang/System", "out", "Ljava/io/PrintStream;")
        m.invoke("virtual", "java/io/PrintStream", "flush", "()V")
        m.load("i", 1)
        m.invoke("static", "java/lang/System", "exit", "(I)V")
        m.op("return")
        return True


def _manifest(main_class: str) -> bytes:
    """The jar manifest. `java -jar` reads exactly this to find the entry."""
    return (f"Manifest-Version: 1.0\r\n"
            f"Created-By: asmpython\r\n"
            f"Main-Class: {main_class}\r\n\r\n").encode("utf-8")


#: The array a split function keeps its registers in.
_LONG_ARRAY = "[J"

#: IR instructions per part of a split function. A part must fit in 65535
#: bytes of bytecode with room to spare, and array-backed registers cost more
#: per instruction than locals do -- roughly twenty bytes for the worst of
#: them, so this is a factor of forty under the limit.
PART_INSTRUCTIONS = 800


def _obj_vtype(internal_name: str) -> tuple[str, str]:
    return ("object", internal_name)


class _Part:
    """One method's worth of a split function."""

    def __init__(self, index: int, instructions: list,
                 part_of: dict[str, int], result_slot: int):
        self.index = index
        self.instructions = instructions
        #: IR block label -> the part that begins with it.
        self.part_of = part_of
        #: Where a `ret` leaves the function's value.
        self.result_slot = result_slot
        #: The part to continue into when this one has no terminator, or None.
        self.falls_through: int | None = None


def _emit_function(parent: "_Emitter", fn: Function) -> None:
    """One IR function, as one method if it fits and several if it does not.

    Measured rather than predicted. The relationship between IR instructions
    and bytecode is not a constant -- an `alloca` is six instructions and a
    `u64` divide is a call -- so the only honest test of "does this fit in a
    JVM method" is to build it and look.
    """
    first = len(parent.cls.methods)
    emitter = _FunctionEmitter(parent, fn)
    emitter.run()
    if len(emitter.m.code) <= MAX_METHOD_BYTES:
        return
    # Discard the attempt and start again. Constants it interned stay in the
    # pool; a few unreferenced entries cost bytes and nothing else.
    del parent.cls.methods[first:]
    _SplitFunction(parent, fn).run()


class _SplitFunction:
    """A function too large for one JVM method.

    A method holds 65535 bytes of bytecode and there is no way to make one
    bigger, so a function past that has to become SEVERAL methods -- which the
    JVM cannot fall between, because each has its own frame and its own locals.

    So the registers move into a `long[]` that every part receives, each part
    returns the number of the part to run next, and an entry method loops:

        long[] r = new long[N];        r[0..] = the parameters
        int at = 0;
        while (at >= 0) at = switch (at) { case 0 -> part0(r); ... };
        return r[result];

    Slower than locals by an array access per operand, and it is the difference
    between compiling the function and refusing it.
    """

    def __init__(self, parent: "_Emitter", fn: Function):
        self.parent = parent
        self.fn = fn
        self.owner = parent.owner
        self.slots = {reg: i for i, reg in enumerate(sorted(fn.registers))}
        self.result_slot = len(self.slots)
        self.sp_slot = self.result_slot + 1
        self.size = self.sp_slot + 1
        self.uses_alloca = any(ins.op is Op.ALLOCA
                               for _, ins in fn.instructions())

    def run(self) -> None:
        parts = self._plan()
        self._trampoline(len(parts))
        for part in parts:
            emitter = _FunctionEmitter(
                self.parent, self.fn,
                method=self.parent.cls.method(
                    f"{_method_name(self.fn.name)}$p{part.index}",
                    f"({_LONG_ARRAY})I", ACC_STATIC))
            emitter.slots = self.slots
            emitter.sp_slot = self.sp_slot if self.uses_alloca else None
            emitter.run_part(part)

    def _plan(self) -> list[_Part]:
        """Cut the function into parts. Every block begins one.

        A block boundary is the only place another part can jump TO, so a part
        that began in the middle of a block would be unreachable. Blocks longer
        than `PART_INSTRUCTIONS` are chained: each runs off its end into the
        next, which is a fallthrough and needs no label.
        """
        part_of: dict[str, int] = {}
        chunks: list[tuple[str | None, list]] = []
        for block in self.fn.blocks:
            part_of[block.label] = len(chunks)
            instructions = list(block.instructions)
            first = True
            while instructions:
                take, instructions = (instructions[:PART_INSTRUCTIONS],
                                      instructions[PART_INSTRUCTIONS:])
                chunks.append((block.label if first else None, take))
                first = False
        parts = [_Part(i, body, part_of, self.result_slot)
                 for i, (_, body) in enumerate(chunks)]
        for i, part in enumerate(parts):
            last = part.instructions[-1] if part.instructions else None
            if last is None or not last.is_terminator:
                part.falls_through = i + 1
        return parts

    def _trampoline(self, count: int) -> None:
        """The entry method: set the array up, then drive the parts."""
        m = self.parent.cls.method(_method_name(self.fn.name),
                                   _descriptor(self.fn),
                                   ACC_PUBLIC | ACC_STATIC)
        frame: list = []
        at = 0
        param_slots = []
        for reg in self.fn.params:
            kind = _kind(self.fn.registers[reg])
            param_slots.append((reg, kind, at))
            frame.append(_VTYPE[kind])
            at += 2 if kind in ("l", "d") else 1
        array_slot, state_slot, scratch = at, at + 1, at + 2
        frame += [_obj_vtype(_LONG_ARRAY), ITEM_INTEGER, ITEM_LONG]
        m.max_locals = scratch + 2
        m.max_stack = 8

        # The scratch is zeroed even when nothing below writes it. A method
        # with no parameters and no alloca never stores it before the
        # dispatch loop, and the frame there declares it a long -- so the
        # verifier saw `top` where the stack map promised `long`, and refused
        # the class. Same invariant as everywhere else: write every local
        # before the first branch.
        m.push_long(0)
        m.store("l", scratch)
        m.push_int(self.size)
        m.u1(0xBC)                                        # newarray
        m.u1(11)                                          # T_LONG
        m.store("a", array_slot)
        for reg, kind, slot in param_slots:
            m.load(kind, slot)
            _to_bits(m, kind)
            m.store("l", scratch)
            m.load("a", array_slot)
            m.push_int(self.slots[reg])
            m.load("l", scratch)
            m.op("lastore")
        if self.uses_alloca:
            m.getstatic(self.owner, SP, SP_DESC)
            m.store("l", scratch)
            m.load("a", array_slot)
            m.push_int(self.sp_slot)
            m.load("l", scratch)
            m.op("lastore")
        m.push_int(0)
        m.store("i", state_slot)
        m.frame_locals = frame

        m.mark("dispatch")
        m.load("i", state_slot)
        m.lookupswitch("done", [(i, f"p{i}") for i in range(count)])
        for i in range(count):
            m.mark(f"p{i}")
            m.load("a", array_slot)
            m.invoke("static", self.owner,
                     f"{_method_name(self.fn.name)}$p{i}",
                     f"({_LONG_ARRAY})I")
            m.store("i", state_slot)
            m.jump("goto", "check")
        m.mark("check")
        m.load("i", state_slot)
        m.jump("iflt", "done")
        m.jump("goto", "dispatch")

        m.mark("done")
        kind = _kind(self.fn.ret)
        if kind == "v":
            m.op("return")
            return
        m.load("a", array_slot)
        m.push_int(self.result_slot)
        m.op("laload")
        _from_bits(m, kind)
        m.op(_RETURN[kind])


def _to_bits(m: MethodBuilder, kind: str) -> None:
    """Convert the value on the stack to the `long` a register slot holds."""
    if kind == "i":
        m.op("i2l")
    elif kind == "f":
        m.invoke("static", "java/lang/Float", "floatToRawIntBits", "(F)I")
        m.op("i2l")
    elif kind == "d":
        m.invoke("static", "java/lang/Double", "doubleToRawLongBits", "(D)J")


def _from_bits(m: MethodBuilder, kind: str) -> None:
    if kind == "i":
        m.op("l2i")
    elif kind == "f":
        m.op("l2i")
        m.invoke("static", "java/lang/Float", "intBitsToFloat", "(I)F")
    elif kind == "d":
        m.invoke("static", "java/lang/Double", "longBitsToDouble", "(J)D")


class _FunctionEmitter:
    """One IR function to one static method.

    Every register gets its own local slot and every local is written before
    the first branch. That is what makes the stack map in `classfile.py` a
    constant instead of an analysis -- see the note there before changing how
    slots are handed out.
    """

    def __init__(self, parent: _Emitter, fn: Function,
                 method: MethodBuilder | None = None):
        self.parent = parent
        self.fn = fn
        self.rt = parent.rt
        self.owner = parent.owner
        self.module = parent.module
        self.version = parent.version
        self.slots: dict[int, int] = {}
        self.sp_slot: int | None = None
        self._labels = 0
        #: None for a whole function in one method; a `_Part` when the function
        #: was too large and its registers live in an array instead of locals.
        self.part: "_Part | None" = None
        self.m: MethodBuilder = method or parent.cls.method(
            _method_name(fn.name), _descriptor(fn), ACC_PUBLIC | ACC_STATIC)

    # ── setup ───────────────────────────────────────────────────────────────
    def run(self) -> None:
        self._assign_slots()
        self.m.max_stack = self._stack_depth()
        self._prologue()
        for block in self.fn.blocks:
            self.m.mark(_block(block.label))
            for ins in block.instructions:
                self._instruction(ins)

    def run_part(self, part: "_Part") -> None:
        """Emit one part of a split function. See `_SplitFunction`."""
        self.part = part
        self.m.max_stack = self._stack_depth()
        self.m.max_locals = 3                    # the array, and a long scratch
        self.m.frame_locals = [_obj_vtype(_LONG_ARRAY), ITEM_LONG]
        # The scratch is a local like any other and the frames name it, so it
        # is written before anything can branch -- a part whose first
        # instruction is a comparison would otherwise reach a frame with
        # nothing in that slot.
        self.m.push_long(0)
        self.m.store("l", 1)
        for ins in part.instructions:
            self._instruction(ins)
        if part.falls_through is not None:
            # The part ran off its end rather than ending in a terminator, so
            # it hands control to the next one exactly as a fallthrough would.
            self.m.push_int(part.falls_through)
            self.m.op("ireturn")

    def _stack_depth(self) -> int:
        """An upper bound on the operand stack this method needs.

        The verifier rejects an UNDERestimate and ignores an over-estimate, so
        this is deliberately loose: a fixed allowance for the longest sequence
        any single instruction expands to, plus room for the widest call's
        arguments, which is the one part that has no fixed bound.

        A constant on its own was wrong and only for calls: eight `i64`
        parameters is sixteen stack slots before the invoke, which is exactly
        the allowance -- so a ninth produced a class the verifier rejected, in
        a program with nothing unusual about it.
        """
        widest = 0
        for _, ins in self.fn.instructions():
            if ins.op not in (Op.CALL, Op.CALL_PTR):
                continue
            widest = max(widest, sum(
                2 if _kind(self._type(a)) in ("l", "d") else 1
                for a in ins.args))
        return 16 + widest

    def _assign_slots(self) -> None:
        """Registers to local slots. Parameters first, in order.

        Not an arbitrary order for the parameters: the JVM puts the arguments
        in the lowest slots in declaration order, and a method whose parameters
        live anywhere else reads whatever the caller passed as something else.
        """
        frame: list = []
        at = 0

        def take(reg: int) -> None:
            nonlocal at
            kind = _kind(self.fn.registers[reg])
            self.slots[reg] = at
            frame.append(_VTYPE[kind])
            at += 2 if kind in ("l", "d") else 1

        for reg in self.fn.params:
            take(reg)
        for reg in sorted(self.fn.registers):
            if reg not in self.slots:
                take(reg)

        if any(ins.op is Op.ALLOCA for _, ins in self.fn.instructions()):
            self.sp_slot = at
            frame.append(ITEM_LONG)
            at += 2

        self.m.max_locals = max(at, 1)
        self.m.frame_locals = frame

    def _prologue(self) -> None:
        """Give every non-parameter local a value.

        The verifier will not accept a branch to a point where a slot's type
        depends on the path taken, and the IR's registers are mutable and
        assigned inside loops. Zeroing here settles every slot's type at entry
        and costs two instructions per register.
        """
        for reg in sorted(self.fn.registers):
            if reg in self.fn.params:
                continue
            ty = self.fn.registers[reg]
            self._zero(ty)
            self.m.store(_kind(ty), self.slots[reg])
        if self.sp_slot is not None:
            self.m.getstatic(self.owner, SP, SP_DESC)
            self.m.store("l", self.sp_slot)

    def _zero(self, ty: T.Type) -> None:
        kind = _kind(ty)
        if kind == "i":
            self.m.push_int(0)
        elif kind == "l":
            self.m.push_long(0)
        elif kind == "f":
            self.m.push_float(0.0)
        else:
            self.m.push_double(0.0)

    # ── operands ────────────────────────────────────────────────────────────
    def _type(self, reg: int) -> T.Type:
        return self.fn.register_type(reg)

    def push(self, reg: int) -> None:
        ty = self._type(reg)
        if self.part is None:
            self.m.load(_kind(ty), self.slots[reg])
            return
        self._load_spilled(self.slots[reg], _kind(ty))

    def pop_to(self, reg: int | None, ty: T.Type) -> None:
        if reg is None:
            self.m.op("pop2" if _kind(ty) in ("l", "d") else "pop")
            return
        if self.part is None:
            self.m.store(_kind(ty), self.slots[reg])
            return
        self._store_spilled(self.slots[reg], _kind(ty))

    # ── array-backed registers, for a split function ────────────────────────
    #
    # Parts are separate methods and a JVM frame does not outlive one, so a
    # register cannot be a local: it lives in a `long[]` the trampoline passes
    # along. Floats travel as their bit patterns, which is exact -- storing
    # them as `double` would need a second array, and narrowing an `f32`
    # through a `double` is not a round trip.

    def _load_spilled(self, index: int, kind: str) -> None:
        self.m.load("a", 0)
        self.m.push_int(index)
        self.m.op("laload")
        if kind == "i":
            self.m.op("l2i")
        elif kind == "f":
            self.m.op("l2i")
            self.m.invoke("static", "java/lang/Float", "intBitsToFloat",
                          "(I)F")
        elif kind == "d":
            self.m.invoke("static", "java/lang/Double", "longBitsToDouble",
                          "(J)D")

    def _store_spilled(self, index: int, kind: str) -> None:
        if kind == "i":
            self.m.op("i2l")
        elif kind == "f":
            self.m.invoke("static", "java/lang/Float", "floatToRawIntBits",
                          "(F)I")
            self.m.op("i2l")
        elif kind == "d":
            self.m.invoke("static", "java/lang/Double", "doubleToRawLongBits",
                          "(D)J")
        # The value is already on the stack and `lastore` wants the array and
        # the index UNDER it. Parking it costs a store and a load; the
        # alternative is emitting the array first, which would mean every
        # instruction knowing its destination before it computes anything.
        self.m.store("l", 1)
        self.m.load("a", 0)
        self.m.push_int(index)
        self.m.load("l", 1)
        self.m.op("lastore")

    def _narrow(self, ty: T.Type) -> None:
        """Truncate an `int` on the stack to what `ty` can hold.

        Applied after every operation that can carry a value past its type's
        width. Leaving it out is invisible in most programs and produces a
        `u8` holding 256 in the one that adds 255 and 1.
        """
        if ty is T.I1:
            self.m.push_int(1)
            self.m.op("iand")
        elif ty is T.I8:
            self.m.op("i2b")
        elif ty is T.U8:
            self.m.push_int(0xFF)
            self.m.op("iand")
        elif ty is T.I16:
            self.m.op("i2s")
        elif ty is T.U16:
            self.m.op("i2c")
        # i32/u32 fill an int exactly; the long family fills a long exactly.

    def _label(self, hint: str) -> str:
        self._labels += 1
        return f"${hint}{self._labels}"

    # ── one instruction ─────────────────────────────────────────────────────
    def _instruction(self, ins: Instruction) -> None:
        op, ty = ins.op, ins.ty
        m = self.m

        if op is Op.CONST:
            self._const(ty, ins.imm)
            self.pop_to(ins.dst, ty)
        elif op is Op.COPY:
            self.push(ins.args[0])
            self.pop_to(ins.dst, ty)
        elif op is Op.GLOBAL_ADDR:
            m.push_long(self.parent.addresses[ins.sym])
            self.pop_to(ins.dst, ty)
        elif op in _ARITH:
            self._binary(ins, _ARITH[op])
        elif op in (Op.DIV, Op.REM):
            self._divide(ins)
        elif op in _BITWISE:
            self._binary(ins, _BITWISE[op])
        elif op is Op.NEG:
            self.push(ins.args[0])
            m.op(_kind(ty) + "neg")
            self._narrow(ty)
            self.pop_to(ins.dst, ty)
        elif op is Op.NOT:
            self._complement(ins)
        elif op in (Op.SHL, Op.SHR):
            self._shift(ins)
        elif op in _INT_BRANCH:
            self._compare(ins)
        elif op in (Op.TRUNC, Op.EXTEND, Op.FTOI, Op.ITOF, Op.FTOF,
                    Op.BITCAST):
            self._convert(ins)
        elif op is Op.ALLOCA:
            self._alloca(ins)
        elif op is Op.LOAD:
            self._load(ins)
        elif op is Op.STORE:
            self._store(ins)
        elif op is Op.OFFSET:
            self.push(ins.args[0])
            self._widen_to_long(ins.args[1])
            m.op("ladd")
            self.pop_to(ins.dst, ty)
        elif op is Op.FUNC_ADDR:
            self._func_addr(ins)
        elif op is Op.CALL:
            self._call(ins)
        elif op is Op.CALL_PTR:
            self._call_ptr(ins)
        elif op is Op.JUMP:
            self._leave(ins.labels[0])
        elif op is Op.BRANCH:
            self.push(ins.args[0])
            if self.part is None:
                m.jump("ifne", _block(ins.labels[0]))
                m.jump("goto", _block(ins.labels[1]))
            else:
                taken = self._label("taken")
                m.jump("ifne", taken)
                self._leave(ins.labels[1])
                m.mark(taken)
                self._leave(ins.labels[0])
        elif op is Op.SWITCH:
            self._switch(ins)
        elif op is Op.RET:
            self._return(ins)
        elif op is Op.UNREACHABLE:
            self._unreachable()
        else:
            # Never a silent fallthrough. A new opcode fails here, at build
            # time, rather than emitting nothing and producing a class that is
            # wrong in a way no test names.
            raise NotImplementedError(
                f"the jvm backend has no rule for {op.value!r}")

    # ── constants ───────────────────────────────────────────────────────────
    def _const(self, ty: T.Type, imm) -> None:
        kind = _kind(ty)
        if kind == "i":
            self.m.push_int(_normalized(ty, imm))
        elif kind == "l":
            self.m.push_long(_normalized(ty, imm))
        elif kind == "f":
            self.m.push_float(float(imm))
        else:
            self.m.push_double(float(imm))

    # ── arithmetic ──────────────────────────────────────────────────────────
    def _binary(self, ins: Instruction, name: str) -> None:
        ty = ins.ty
        self.push(ins.args[0])
        self.push(ins.args[1])
        self.m.op(_kind(ty) + name)
        self._narrow(ty)
        self.pop_to(ins.dst, ty)

    def _complement(self, ins: Instruction) -> None:
        ty = ins.ty
        self.push(ins.args[0])
        if _kind(ty) == "l":
            self.m.push_long(-1)
            self.m.op("lxor")
        else:
            self.m.op("iconst_m1")
            self.m.op("ixor")
        self._narrow(ty)
        self.pop_to(ins.dst, ty)

    def _shift(self, ins: Instruction) -> None:
        ty = ins.ty
        kind = _kind(ty)
        self.push(ins.args[0])
        self.push(ins.args[1])
        if kind == "l":
            # `lshl` and friends take a LONG value and an INT distance. The IR
            # gives both operands the same type, so the distance is narrowed
            # here; shifting by more than the width is undefined in the IR and
            # the JVM's own masking is as good an answer as any.
            self.m.op("l2i")
        if ins.op is Op.SHL:
            self.m.op(kind + "shl")
        else:
            self.m.op((kind + "shr") if ty.is_signed else (kind + "ushr"))
        self._narrow(ty)
        self.pop_to(ins.dst, ty)

    def _divide(self, ins: Instruction) -> None:
        """Divide and remainder, which are where signedness stops being free.

        `idiv` is signed, always. For the narrow unsigned types the operands
        are held zero-extended, so widening to `long` and dividing there is
        both correct and shorter than any alternative. `u64` has no room for
        that trick and uses the library's unsigned division.
        """
        ty = ins.ty
        kind = _kind(ty)
        name = "div" if ins.op is Op.DIV else "rem"

        if ty.is_float:
            self.push(ins.args[0])
            self.push(ins.args[1])
            self.m.op(kind + name)
        elif ty.is_signed:
            self.push(ins.args[0])
            self.push(ins.args[1])
            self.m.op(kind + name)
        elif kind == "i":
            for arg in ins.args:
                self.push(arg)
                self.m.op("i2l")
                self.m.push_long(0xFFFFFFFF)
                self.m.op("land")
            self.m.op("l" + name)
            self.m.op("l2i")
        else:
            if self.version.major < UNSIGNED_LONG_MATH_FROM:
                raise BackendUnsupported(
                    f"unsigned 64-bit {name} needs Long."
                    f"{'divideUnsigned' if name == 'div' else 'remainderUnsigned'},"
                    f" which arrived in Java 8"
                    f"\nthis class targets version {self.version.major} "
                    f"({self.version.runs_on}); pass --java-version 8 or later")
            self.push(ins.args[0])
            self.push(ins.args[1])
            self.m.invoke("static", "java/lang/Long",
                          "divideUnsigned" if name == "div"
                          else "remainderUnsigned", "(JJ)J")
        self._narrow(ty)
        self.pop_to(ins.dst, ty)

    # ── comparison ──────────────────────────────────────────────────────────
    def _compare(self, ins: Instruction) -> None:
        """A comparison, materialised as 0 or 1 through a branch.

        The two arms each STORE and then meet, rather than meeting with the
        answer on the stack. That keeps the operand stack empty at both labels,
        which is the invariant that makes the stack map a constant.
        """
        ty = ins.ty
        kind = _kind(ty)
        true_label = self._label("true")
        end_label = self._label("end")

        if kind == "i":
            # Sign-flipping puts unsigned values in signed order. u8 and u16
            # are held zero-extended and are already in order, so only u32
            # needs it -- and it needs it for values above 2**31 only, which is
            # exactly the range that would otherwise compare as negative.
            flip = not ty.is_signed and ty.bits == 32
            for arg in ins.args:
                self.push(arg)
                if flip:
                    self.m.push_int(-0x80000000)
                    self.m.op("ixor")
            self.m.jump(_INT_BRANCH[ins.op], true_label)
        elif kind == "l":
            flip = not ty.is_signed          # u64 and ptr
            for arg in ins.args:
                self.push(arg)
                if flip:
                    self.m.push_long(-(1 << 63))
                    self.m.op("lxor")
            self.m.op("lcmp")
            self.m.jump(_ZERO_BRANCH[ins.op], true_label)
        else:
            self.push(ins.args[0])
            self.push(ins.args[1])
            self.m.op(kind + "cmp" + _FLOAT_CMP[ins.op])
            self.m.jump(_ZERO_BRANCH[ins.op], true_label)

        self.m.push_int(0)
        self.pop_to(ins.dst, T.I1)
        self.m.jump("goto", end_label)
        self.m.mark(true_label)
        self.m.push_int(1)
        self.pop_to(ins.dst, T.I1)
        self.m.mark(end_label)

    # ── conversion ──────────────────────────────────────────────────────────
    def _widen_to_long(self, reg: int) -> None:
        """Push a register as a `long`, respecting its signedness."""
        ty = self._type(reg)
        self.push(reg)
        if _kind(ty) == "l":
            return
        self.m.op("i2l")
        if not ty.is_signed and ty.bits == 32:
            # `i2l` sign-extends. A u32 above 2**31 is a negative int holding
            # the right bits, and sign-extending it gives a long that is
            # 4294967296 too small.
            self.m.push_long(0xFFFFFFFF)
            self.m.op("land")

    def _convert(self, ins: Instruction) -> None:
        dst, src = ins.ty, self._type(ins.args[0])
        dk, sk = _kind(dst), _kind(src)
        m = self.m

        if ins.op is Op.BITCAST:
            self._bitcast(ins, dst, src, dk, sk)
            return

        if ins.op is Op.ITOF and sk == "l" and not src.is_signed:
            self._unsigned_to_float(ins, dst)
            return

        if ins.op is Op.FTOI:
            self._float_to_int(ins, dst, sk, dk)
            return

        if ins.op is Op.EXTEND and dk == "l" and sk == "i":
            self._widen_to_long(ins.args[0])
        elif ins.op is Op.ITOF and sk == "i" and not src.is_signed \
                and src.bits == 32:
            self._widen_to_long(ins.args[0])
            m.op("l2" + dk)
        else:
            self.push(ins.args[0])
            if sk != dk:
                m.op(f"{sk}2{dk}")
        self._narrow(dst)
        self.pop_to(ins.dst, dst)

    def _float_to_int(self, ins: Instruction, dst: T.Type, sk: str,
                      dk: str) -> None:
        """Float to integer, always through a `long`.

        The direct route, `d2i`, is wrong for a value the destination cannot
        hold: the JVM SATURATES -- 1e10 to `i32` is 2147483647 -- while the
        reference interpreter wraps, giving 1410065408. The IR calls the
        out-of-range case undefined, so neither is a violation; the
        interpreter is what `asmpython run` prints and what every differential
        test compares against, so it is the one to agree with.

        Going through `long` and narrowing gets that for every value a `long`
        can hold. Beyond about 9.2e18 the `d2l` itself saturates and the two
        part company again -- reaching that needs a float larger than any
        64-bit integer, where "wrap the exact mathematical value" is a
        statement about a number the machine cannot represent.
        """
        self.push(ins.args[0])
        self.m.op(f"{sk}2l")
        if dk == "i":
            self.m.op("l2i")
        self._narrow(dst)
        self.pop_to(ins.dst, dst)

    def _bitcast(self, ins, dst, src, dk, sk) -> None:
        """Reinterpret the bits. Only float/int crossings emit anything.

        Between two integer types of one width the bits are already what they
        will be; what changes is how this backend HOLDS them, so `i8` to `u8`
        re-normalises rather than doing nothing -- -1 and 255 are the same byte
        and the second is how a `u8` is carried.
        """
        self.push(ins.args[0])
        if sk in ("f", "d") and dk in ("i", "l"):
            self.m.invoke("static",
                          "java/lang/Double" if sk == "d" else "java/lang/Float",
                          "doubleToRawLongBits" if sk == "d"
                          else "floatToRawIntBits",
                          "(D)J" if sk == "d" else "(F)I")
        elif sk in ("i", "l") and dk in ("f", "d"):
            self.m.invoke("static",
                          "java/lang/Double" if dk == "d" else "java/lang/Float",
                          "longBitsToDouble" if dk == "d"
                          else "intBitsToFloat",
                          "(J)D" if dk == "d" else "(I)F")
        elif (sk, dk) in (("f", "d"), ("d", "f")):
            # `bitcast` is defined as same-width, so this is unreachable from
            # verified IR -- and it is checked rather than assumed, because
            # the alternative is silently emitting `f2d`, which CONVERTS.
            raise BackendUnsupported(
                f"bitcast between {src} and {dst} changes the width")
        else:
            self._narrow(dst)
        self.pop_to(ins.dst, dst)

    def _unsigned_to_float(self, ins: Instruction, dst: T.Type) -> None:
        """`u64` to a float, which `l2d` gets wrong above 2**63.

        `l2d` reads its operand as signed, so every value with the top bit set
        comes out negative. Halving before the conversion moves the top bit
        into range, and the discarded low bit is added back afterwards.
        """
        dk = _kind(dst)
        positive = self._label("upos")
        end = self._label("uend")
        m = self.m

        self.push(ins.args[0])
        m.push_long(0)
        m.op("lcmp")
        m.jump("ifge", positive)
        self.push(ins.args[0])
        m.push_int(1)
        m.op("lushr")
        m.op("l2" + dk)
        if dk == "d":
            m.push_double(2.0)
        else:
            m.push_float(2.0)
        m.op(dk + "mul")
        self.push(ins.args[0])
        m.push_long(1)
        m.op("land")
        m.op("l2" + dk)
        m.op(dk + "add")
        self.pop_to(ins.dst, dst)
        m.jump("goto", end)
        m.mark(positive)
        self.push(ins.args[0])
        m.op("l2" + dk)
        self.pop_to(ins.dst, dst)
        m.mark(end)

    # ── memory ──────────────────────────────────────────────────────────────
    def _alloca(self, ins: Instruction) -> None:
        m = self.m
        m.getstatic(self.owner, SP, SP_DESC)
        m.push_long(max(int(ins.imm or 0), 1))
        m.op("lsub")
        m.push_long(-ALIGN)                  # round down to an 8-byte boundary
        m.op("land")
        m.op("dup2")
        m.putstatic(self.owner, SP, SP_DESC)
        self.pop_to(ins.dst, ins.ty)

    #: IR type -> the runtime helper that reads it, and any conversion after.
    def _load(self, ins: Instruction) -> None:
        ty = ins.ty
        self.push(ins.args[0])
        helper = _ACCESS[ty][0]
        self.rt.invoke(self.m, helper)
        if ty is T.F32:
            self.m.invoke("static", "java/lang/Float", "intBitsToFloat",
                          "(I)F")
        elif ty is T.F64:
            self.m.invoke("static", "java/lang/Double", "longBitsToDouble",
                          "(J)D")
        else:
            self._narrow(ty)
        self.pop_to(ins.dst, ty)

    def _store(self, ins: Instruction) -> None:
        ty = ins.ty
        self.push(ins.args[1])               # address first: the helper is
        self.push(ins.args[0])               # (address, value)
        if ty is T.F32:
            self.m.invoke("static", "java/lang/Float", "floatToRawIntBits",
                          "(F)I")
        elif ty is T.F64:
            self.m.invoke("static", "java/lang/Double", "doubleToRawLongBits",
                          "(D)J")
        self.rt.invoke(self.m, _ACCESS[ty][1])

    # ── calls and control flow ──────────────────────────────────────────────
    def _call(self, ins: Instruction) -> None:
        callee = self.module.function(ins.sym)
        if callee is None:
            raise BackendUnsupported(
                f"call to {ins.sym!r}, which the module never declares")
        interop = self.parent.interop
        if callee.external and interop is not None \
                and callee.name in interop.operations:
            self._java_call(ins, interop.operations[callee.name])
            return
        for arg in ins.args:
            self.push(arg)
        if callee.external:
            self.rt.invoke(self.m, callee.name)
        else:
            self.m.invoke("static", self.owner, _method_name(callee.name),
                          _descriptor(callee))
        if not ins.ty.is_void:
            self.pop_to(ins.dst, ins.ty)

    # ── calling Java ────────────────────────────────────────────────────────
    #
    # A Java call is emitted INLINE rather than through a generated wrapper.
    # The wrapper would need a descriptor of its own and would be one more
    # place for the argument conversions to be written down; inline, the
    # conversion happens once, next to the invoke it belongs to.

    def _java_call(self, ins: Instruction, op) -> None:
        m = self.m
        if op.kind == "str":
            # The address of the UTF-8 bytes is the only argument; the runtime
            # method reads them and builds the String.
            self.push(ins.args[0])
            self.rt.invoke(m, "jvm$str")
            self.pop_to(ins.dst, ins.ty)
            return
        params = parse_parameters(op.descriptor)

        if op.kind == "new":
            m.new(op.owner)
            m.op("dup")
            self._java_args(ins.args, params)
            m.invoke("special", op.owner, "<init>", op.descriptor)
            self.rt.invoke(m, "$reg")
            self.pop_to(ins.dst, ins.ty)
            return

        if op.kind == "call":
            # The receiver arrives as a handle and has to become the reference
            # it stands for. The cast is not decoration: `$deref` is typed
            # `Object`, and `invokevirtual` on a `Block` method needs a `Block`.
            self.push(ins.args[0])
            self.rt.invoke(m, "$deref")
            m.checkcast(op.owner)
            self._java_args(ins.args[1:], params)
            if self._is_interface(op.owner):
                m.invoke_interface(op.owner, op.method, op.descriptor)
            else:
                m.invoke("virtual", op.owner, op.method, op.descriptor)
        else:
            self._java_args(ins.args, params)
            m.invoke("static", op.owner, op.method, op.descriptor)

        self._java_result(ins, op.descriptor[op.descriptor.index(")") + 1:])

    def _is_interface(self, internal: str) -> bool:
        classpath = self.parent.interop.classpath
        found = classpath.classes.get(internal)
        return bool(found and found.is_interface)

    def _java_args(self, args, params: list[str]) -> None:
        """Push each IR operand as the Java type the method declares.

        Every integral value travels as `i64` and every float as `f64`, so the
        narrowing happens here -- and it narrows rather than truncating
        silently: a `char` parameter given 70000 gets what `(char) 70000` gives
        in Java, which is what the same call written in Java would do.
        """
        for arg, want in zip(args, params):
            self.push(arg)
            if want == "J":
                continue
            if want == "D":
                continue
            if want == "F":
                self.m.op("d2f")
            elif want in ("Z", "I"):
                self.m.op("l2i")
            elif want == "B":
                self.m.op("l2i")
                self.m.op("i2b")
            elif want == "C":
                self.m.op("l2i")
                self.m.op("i2c")
            elif want == "S":
                self.m.op("l2i")
                self.m.op("i2s")
            else:
                self.rt.invoke(self.m, "$deref")
                # An array descriptor IS its own class name; a class one has
                # the `L` and `;` around it that a cast must not include.
                self.m.checkcast(want if want.startswith("[")
                                 else want[1:-1])

    def _java_result(self, ins: Instruction, returns: str) -> None:
        m = self.m
        if returns == "V":
            if ins.dst is not None:
                # A void method cannot produce the value the IR asked for.
                raise BackendUnsupported(
                    f"{ins.sym} returns void and the call wants a value")
            return
        if returns in ("Z", "B", "C", "S", "I"):
            m.op("i2l")
        elif returns == "F":
            m.op("f2d")
        elif returns in ("J", "D"):
            pass
        else:
            self.rt.invoke(m, "$reg")
        self.pop_to(ins.dst, ins.ty)

    def _func_addr(self, ins: Instruction) -> None:
        """A function's address, which here is the integer standing for it."""
        fid = self.parent.function_ids.get(ins.sym)
        if fid is None:
            # `_number_functions` walked every instruction, so a symbol
            # reaching here without a number means the module names a function
            # it never declares -- which the verifier would have caught, and
            # which must not become a silent zero.
            raise BackendUnsupported(
                f"func_addr of {ins.sym!r}, which the module never declares")
        self.m.push_long(fid)
        self.pop_to(ins.dst, ins.ty)

    def _call_ptr(self, ins: Instruction) -> None:
        """Call through a pointer, via the dispatcher for its signature."""
        args = ins.args[1:]
        params = "J" + "".join(
            _DESCRIPTOR[_kind(self._type(a))] for a in args)
        descriptor = f"({params}){_DESCRIPTOR[_kind(ins.ty)]}"
        name = self.parent._dispatcher(descriptor)
        self.push(ins.args[0])
        for arg in args:
            self.push(arg)
        self.m.invoke("static", self.owner, name, descriptor)
        if not ins.ty.is_void:
            self.pop_to(ins.dst, ins.ty)

    def _leave(self, label: str) -> None:
        """Transfer control to an IR block.

        A jump when the whole function is one method; a RETURN OF THE PART'S
        NUMBER when it is not, because the trampoline is what actually
        branches. Every terminator goes through here so that the two shapes
        cannot drift.
        """
        if self.part is None:
            self.m.jump("goto", _block(label))
        else:
            self.m.push_int(self.part.part_of[label])
            self.m.op("ireturn")

    def _switch(self, ins: Instruction) -> None:
        if _kind(ins.ty) == "i":
            self.push(ins.args[0])
            if self.part is None:
                self.m.lookupswitch(
                    _block(ins.labels[0]),
                    [(v, _block(l)) for v, l in ins.cases])
                return
            # In a part every arm has to LEAVE rather than branch, so each gets
            # a landing label of its own that does so.
            arms = [(v, self._label("arm")) for v, _ in ins.cases]
            default = self._label("arm")
            self.m.lookupswitch(default, arms)
            for (_, label), (_, target) in zip(arms, ins.cases):
                self.m.mark(label)
                self._leave(target)
            self.m.mark(default)
            self._leave(ins.labels[0])
            return
        # `lookupswitch` dispatches on an int and the IR permits a 64-bit
        # selector. Narrowing it would merge cases that differ above 32 bits,
        # so the comparisons are written out instead -- longer, and it cannot
        # send two different values to the same arm.
        for value, label in ins.cases:
            self.push(ins.args[0])
            self.m.push_long(_normalized(ins.ty, value))
            self.m.op("lcmp")
            hit = self._label("hit")
            self.m.jump("ifeq", hit)
            skip = self._label("skip")
            self.m.jump("goto", skip)
            self.m.mark(hit)
            self._leave(label)
            self.m.mark(skip)
        self._leave(ins.labels[0])

    def _return(self, ins: Instruction) -> None:
        if self.sp_slot is not None:
            # Every alloca in this frame is freed here, which is what the IR
            # promises: "freed when the function returns".
            if self.part is None:
                self.m.load("l", self.sp_slot)
            else:
                self._load_spilled(self.sp_slot, "l")
            self.m.putstatic(self.owner, SP, SP_DESC)
        if self.part is not None:
            # A part cannot return the function's value -- it returns an int
            # saying where to go next. The value goes to the slot the
            # trampoline reads, and -1 means "stop".
            if ins.args:
                self.push(ins.args[0])
                self._store_spilled(self.part.result_slot,
                                    _kind(self._type(ins.args[0])))
            self.m.push_int(-1)
            self.m.op("ireturn")
            return
        if ins.args:
            self.push(ins.args[0])
            self.m.op(_RETURN[_kind(self._type(ins.args[0]))])
        else:
            self.m.op("return")

    def _unreachable(self) -> None:
        self.m.new("java/lang/IllegalStateException")
        self.m.op("dup")
        self.m.push_string("unreachable")
        self.m.invoke("special", "java/lang/IllegalStateException", "<init>",
                      "(Ljava/lang/String;)V")
        self.m.op("athrow")


#: IR type -> (load helper, store helper). Signedness decides how a narrow
#: value arrives in a 32-bit register: `i8` sign-extended, `u8` zero-extended,
#: which is the representation everything else here assumes.
_ACCESS: dict[T.Type, tuple[str, str]] = {
    T.I1: ("$ld8u", "$st8"),
    T.I8: ("$ld8", "$st8"),
    T.U8: ("$ld8u", "$st8"),
    T.I16: ("$ld16", "$st16"),
    T.U16: ("$ld16u", "$st16"),
    T.I32: ("$ld32", "$st32"),
    T.U32: ("$ld32", "$st32"),
    T.I64: ("$ld64", "$st64"),
    T.U64: ("$ld64", "$st64"),
    T.PTR: ("$ld64", "$st64"),
    T.F32: ("$ld32", "$st32"),
    T.F64: ("$ld64", "$st64"),
}


def _block(label: str) -> str:
    """A bytecode label for an IR block label.

    Prefixed so that it cannot collide with the synthetic labels `_label`
    hands out. IR block labels come from a frontend or from hand-written IR,
    and nothing stops one being called `true1`.
    """
    return "b:" + label


register(JvmBackend())
