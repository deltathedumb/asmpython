"""Writing a class file: constant pool, methods, bytecode, stack maps.

Nothing here knows about asmpython's IR. This is the container format and the
instruction encoder; `emit.py` is the translation into it. Keeping them apart
is what makes the format testable on its own -- a class this module writes can
be handed to `javap` with no compiler in the picture.

THE STACK MAP IS THE PART THAT BITES. From class-file version 50 the verifier
reads `StackMapTable` instead of inferring frames, and from 51 a method with a
branch must carry one. Building a correct one in general means a dataflow
analysis. This module does not do one, because it does not have to: `emit.py`
holds two invariants, and together they make every frame in a method identical.

    1. every local is written before the first branch, so no slot ever
       changes type
    2. the operand stack is empty at every branch target

So a frame is "the same locals, nothing on the stack", repeated at each target,
and `MethodBuilder.stack_map_table` writes exactly that. Any code added here
that violates either invariant will not fail visibly -- it will produce a class
the verifier rejects with a message about a frame you never wrote.
"""
from __future__ import annotations

import struct

CLASS_VERSION_MINOR = 0

# ── access flags ────────────────────────────────────────────────────────────
ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_SUPER = 0x0020

# ── constant pool tags ──────────────────────────────────────────────────────
CONSTANT_Utf8 = 1
CONSTANT_Integer = 3
CONSTANT_Float = 4
CONSTANT_Long = 5
CONSTANT_Double = 6
CONSTANT_Class = 7
CONSTANT_String = 8
CONSTANT_Fieldref = 9
CONSTANT_Methodref = 10
CONSTANT_InterfaceMethodref = 11
CONSTANT_NameAndType = 12

# ── verification types, for StackMapTable ───────────────────────────────────
ITEM_TOP = 0
ITEM_INTEGER = 1
ITEM_FLOAT = 2
ITEM_DOUBLE = 3
ITEM_LONG = 4
ITEM_NULL = 5
ITEM_OBJECT = 7

#: A slot's type in a frame: one of the ITEM_* tags, or ("object", name).
VType = "int | tuple[str, str]"

#: Zero-operand instructions, by mnemonic. Anything taking an operand gets a
#: method below instead, so a caller can never emit an opcode and forget its
#: argument -- the encoding and the operand are written by the same line.
SIMPLE: dict[str, int] = {
    "nop": 0x00, "aconst_null": 0x01,
    "iconst_m1": 0x02, "iconst_0": 0x03, "iconst_1": 0x04, "iconst_2": 0x05,
    "iconst_3": 0x06, "iconst_4": 0x07, "iconst_5": 0x08,
    "lconst_0": 0x09, "lconst_1": 0x0a,
    "fconst_0": 0x0b, "fconst_1": 0x0c, "fconst_2": 0x0d,
    "dconst_0": 0x0e, "dconst_1": 0x0f,
    "iaload": 0x2e, "laload": 0x2f, "faload": 0x30, "daload": 0x31,
    "aaload": 0x32, "baload": 0x33, "caload": 0x34, "saload": 0x35,
    "iastore": 0x4f, "lastore": 0x50, "fastore": 0x51, "dastore": 0x52,
    "aastore": 0x53, "bastore": 0x54, "castore": 0x55, "sastore": 0x56,
    "pop": 0x57, "pop2": 0x58, "dup": 0x59, "dup_x1": 0x5a, "dup_x2": 0x5b,
    "dup2": 0x5c, "swap": 0x5f,
    "iadd": 0x60, "ladd": 0x61, "fadd": 0x62, "dadd": 0x63,
    "isub": 0x64, "lsub": 0x65, "fsub": 0x66, "dsub": 0x67,
    "imul": 0x68, "lmul": 0x69, "fmul": 0x6a, "dmul": 0x6b,
    "idiv": 0x6c, "ldiv": 0x6d, "fdiv": 0x6e, "ddiv": 0x6f,
    "irem": 0x70, "lrem": 0x71, "frem": 0x72, "drem": 0x73,
    "ineg": 0x74, "lneg": 0x75, "fneg": 0x76, "dneg": 0x77,
    "ishl": 0x78, "lshl": 0x79, "ishr": 0x7a, "lshr": 0x7b,
    "iushr": 0x7c, "lushr": 0x7d,
    "iand": 0x7e, "land": 0x7f, "ior": 0x80, "lor": 0x81,
    "ixor": 0x82, "lxor": 0x83,
    "i2l": 0x85, "i2f": 0x86, "i2d": 0x87,
    "l2i": 0x88, "l2f": 0x89, "l2d": 0x8a,
    "f2i": 0x8b, "f2l": 0x8c, "f2d": 0x8d,
    "d2i": 0x8e, "d2l": 0x8f, "d2f": 0x90,
    "i2b": 0x91, "i2c": 0x92, "i2s": 0x93,
    "lcmp": 0x94, "fcmpl": 0x95, "fcmpg": 0x96,
    "dcmpl": 0x97, "dcmpg": 0x98,
    "ireturn": 0xac, "lreturn": 0xad, "freturn": 0xae, "dreturn": 0xaf,
    "areturn": 0xb0, "return": 0xb1,
    "arraylength": 0xbe, "athrow": 0xbf,
}

#: Branch instructions, by mnemonic. All take a 16-bit signed offset from the
#: opcode itself; `goto_w` is the exception and is emitted only if a jump does
#: not fit, which `finish` checks rather than assumes.
BRANCHES: dict[str, int] = {
    "ifeq": 0x99, "ifne": 0x9a, "iflt": 0x9b, "ifge": 0x9c,
    "ifgt": 0x9d, "ifle": 0x9e,
    "if_icmpeq": 0x9f, "if_icmpne": 0xa0, "if_icmplt": 0xa1,
    "if_icmpge": 0xa2, "if_icmpgt": 0xa3, "if_icmple": 0xa4,
    "if_acmpeq": 0xa5, "if_acmpne": 0xa6,
    "goto": 0xa7,
}

_LOAD = {"i": 0x15, "l": 0x16, "f": 0x17, "d": 0x18, "a": 0x19}
_STORE = {"i": 0x36, "l": 0x37, "f": 0x38, "d": 0x39, "a": 0x3a}
_WIDE = 0xc4

_LDC, _LDC_W, _LDC2_W = 0x12, 0x13, 0x14
_LOOKUPSWITCH = 0xab
_GETSTATIC, _PUTSTATIC = 0xb2, 0xb3
_INVOKEVIRTUAL, _INVOKESPECIAL, _INVOKESTATIC = 0xb6, 0xb7, 0xb8
_INVOKEINTERFACE = 0xb9
_CHECKCAST = 0xc0
_NEW, _NEWARRAY, _ANEWARRAY = 0xbb, 0xbc, 0xbd

#: `newarray` array-type codes. Only the one this backend uses.
T_BYTE = 8

#: The format's ceiling on one method's bytecode. `Code.code_length` is a u4 and
#: the JVM specification then requires it to be under 65536, so a longer method
#: is not a large class file -- it is one nothing will load.
MAX_METHOD_BYTES = 65535


class ClassFileLimit(ValueError):
    """A limit of the class-file format, reached.

    Its own type because these are the errors that are NOT bugs: the format has
    hard ceilings on method size and branch distance, a big enough input
    reaches them, and the caller turns this into a diagnostic naming the
    method rather than letting a `struct.error` out of a serializer.
    """


class ConstantPool:
    """Interned constant pool.

    Longs and doubles occupy TWO entries each -- a quirk of the format that is
    easy to forget and that produces a class rejected with a message pointing
    at an unrelated constant, so the width is handled here rather than at the
    thirty call sites.
    """

    def __init__(self) -> None:
        self._entries: list[bytes] = []
        self._index: dict[tuple, int] = {}

    def _add(self, key: tuple, payload: bytes, width: int = 1) -> int:
        existing = self._index.get(key)
        if existing is not None:
            return existing
        index = len(self._entries) + 1
        self._entries.append(payload)
        for _ in range(width - 1):
            self._entries.append(b"")      # the unusable slot after long/double
        self._index[key] = index
        return index

    def utf8(self, text: str) -> int:
        raw = _modified_utf8(text)
        if len(raw) > 0xFFFF:
            raise ValueError(
                f"constant pool string is {len(raw)} bytes; the format allows "
                f"65535")
        return self._add(("utf8", text),
                         bytes([CONSTANT_Utf8]) + struct.pack(">H", len(raw))
                         + raw)

    def integer(self, value: int) -> int:
        return self._add(("int", value),
                         bytes([CONSTANT_Integer]) + struct.pack(">i", value))

    def float_(self, value: float) -> int:
        return self._add(("float", struct.pack(">f", value)),
                         bytes([CONSTANT_Float]) + struct.pack(">f", value))

    def long(self, value: int) -> int:
        return self._add(("long", value),
                         bytes([CONSTANT_Long]) + struct.pack(">q", value),
                         width=2)

    def double(self, value: float) -> int:
        # Keyed on the BITS, not the value: 0.0 and -0.0 compare equal in
        # Python and are different constants to the JVM, and nan != nan would
        # add a fresh entry for every mention.
        bits = struct.pack(">d", value)
        return self._add(("double", bits),
                         bytes([CONSTANT_Double]) + bits, width=2)

    def string(self, text: str) -> int:
        return self._add(("str", text), bytes([CONSTANT_String])
                         + struct.pack(">H", self.utf8(text)))

    def class_ref(self, internal_name: str) -> int:
        return self._add(("class", internal_name), bytes([CONSTANT_Class])
                         + struct.pack(">H", self.utf8(internal_name)))

    def name_and_type(self, name: str, descriptor: str) -> int:
        return self._add(
            ("nat", name, descriptor),
            bytes([CONSTANT_NameAndType])
            + struct.pack(">HH", self.utf8(name), self.utf8(descriptor)))

    def methodref(self, owner: str, name: str, descriptor: str) -> int:
        return self._add(
            ("method", owner, name, descriptor),
            bytes([CONSTANT_Methodref])
            + struct.pack(">HH", self.class_ref(owner),
                          self.name_and_type(name, descriptor)))

    def interface_methodref(self, owner: str, name: str,
                            descriptor: str) -> int:
        """An interface method is a DIFFERENT constant tag from a class one,
        and using the wrong tag is an `IncompatibleClassChangeError` at the
        call rather than anything the writer notices."""
        return self._add(
            ("imethod", owner, name, descriptor),
            bytes([CONSTANT_InterfaceMethodref])
            + struct.pack(">HH", self.class_ref(owner),
                          self.name_and_type(name, descriptor)))

    def fieldref(self, owner: str, name: str, descriptor: str) -> int:
        return self._add(
            ("field", owner, name, descriptor),
            bytes([CONSTANT_Fieldref])
            + struct.pack(">HH", self.class_ref(owner),
                          self.name_and_type(name, descriptor)))

    def serialize(self) -> bytes:
        return (struct.pack(">H", len(self._entries) + 1)
                + b"".join(self._entries))


def _modified_utf8(text: str) -> bytes:
    """The class file's string encoding, which is not UTF-8.

    Two differences, and both are reachable from ordinary input: U+0000 is
    written as the two bytes C0 80 rather than a NUL, and a character outside
    the basic multilingual plane is written as its two surrogates encoded
    separately rather than as one four-byte sequence. Python's `encode("utf-8")`
    does neither, so a global containing a zero byte or a program mentioning an
    emoji produced a class file rejected at load with `ClassFormatError`.
    """
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code == 0:
            out += b"\xc0\x80"
        elif code < 0x80:
            out.append(code)
        elif code < 0x800:
            out.append(0xC0 | (code >> 6))
            out.append(0x80 | (code & 0x3F))
        elif code < 0x10000:
            out.append(0xE0 | (code >> 12))
            out.append(0x80 | ((code >> 6) & 0x3F))
            out.append(0x80 | (code & 0x3F))
        else:
            code -= 0x10000
            for half in (0xD800 | (code >> 10), 0xDC00 | (code & 0x3FF)):
                out.append(0xE0 | (half >> 12))
                out.append(0x80 | ((half >> 6) & 0x3F))
                out.append(0x80 | (half & 0x3F))
    return bytes(out)


class MethodBuilder:
    """Bytecode for one method, with labels resolved at the end.

    Labels are names, not offsets: a forward branch is written with a
    placeholder and patched by `finish`, so a caller never computes an offset
    and never has to know how long an instruction encodes to.
    """

    def __init__(self, pool: ConstantPool, name: str, descriptor: str,
                 access: int = ACC_STATIC) -> None:
        self.pool = pool
        self.name = name
        self.descriptor = descriptor
        self.access = access
        self.code = bytearray()
        #: Every local's verification type, in SLOT order, one entry per
        #: variable -- a long or double takes two slots and contributes one
        #: entry. Filled in by the caller before `serialize`; empty means "emit
        #: no frames", which is only correct below class version 50.
        self.frame_locals: list = []
        self.max_locals = 0
        #: A ceiling, not a high-water mark. Computing the true depth needs the
        #: flow analysis this module exists to avoid, and the verifier rejects
        #: an UNDERestimate only -- so this is set high enough for the longest
        #: sequence anything here emits and left alone.
        self.max_stack = 16
        self._labels: dict[str, int] = {}
        self._patches: list[tuple[int, str, int, int]] = []
        #: Offsets needing a frame though nothing branches to them: code after
        #: an unconditional transfer is unreachable by fallthrough, and the
        #: verifier demands a frame there too.
        self._frame_points: set[int] = set()

    # ── raw ─────────────────────────────────────────────────────────────────
    def u1(self, value: int) -> None:
        self.code.append(value & 0xFF)

    def u2(self, value: int) -> None:
        self.code += struct.pack(">H", value & 0xFFFF)

    def here(self) -> int:
        return len(self.code)

    # ── labels ──────────────────────────────────────────────────────────────
    def mark(self, label: str) -> None:
        """Bind `label` to the current offset, and require a frame there."""
        self._labels[label] = len(self.code)
        self._frame_points.add(len(self.code))

    def jump(self, mnemonic: str, label: str) -> None:
        base = len(self.code)
        self.u1(BRANCHES[mnemonic])
        self._patches.append((len(self.code), label, base, 2))
        self.u2(0)

    # ── instructions ────────────────────────────────────────────────────────
    def op(self, mnemonic: str) -> None:
        """One zero-operand instruction, by name."""
        try:
            self.u1(SIMPLE[mnemonic])
        except KeyError:
            raise KeyError(f"{mnemonic!r} is not a zero-operand instruction; "
                           f"use the method that writes its operand") from None

    def load(self, kind: str, slot: int) -> None:
        """`kind` is one of i l f d a -- the JVM's own prefix letters."""
        self._slot_op(_LOAD[kind], slot)

    def store(self, kind: str, slot: int) -> None:
        self._slot_op(_STORE[kind], slot)

    def _slot_op(self, opcode: int, slot: int) -> None:
        # The compact `iload_0..3` forms save three bytes a method and cost a
        # branch on every emission; the `wide` prefix is not optional past slot
        # 255, and a method with 256 locals is reachable from an ordinary
        # program. Only the second one is written.
        if slot > 0xFF:
            self.u1(_WIDE)
            self.u1(opcode)
            self.u2(slot)
        else:
            self.u1(opcode)
            self.u1(slot)

    def push_int(self, value: int) -> None:
        """An `int` constant, in the shortest encoding that holds it."""
        if -1 <= value <= 5:
            self.op(["iconst_m1", "iconst_0", "iconst_1", "iconst_2",
                     "iconst_3", "iconst_4", "iconst_5"][value + 1])
        elif -0x80 <= value <= 0x7F:
            self.u1(0x10)                                   # bipush
            self.u1(value & 0xFF)
        elif -0x8000 <= value <= 0x7FFF:
            self.u1(0x11)                                   # sipush
            self.u2(value & 0xFFFF)
        else:
            self._ldc(self.pool.integer(_wrap(value, 32)))

    def push_long(self, value: int) -> None:
        if value in (0, 1):
            self.op("lconst_0" if value == 0 else "lconst_1")
        else:
            self.u1(_LDC2_W)
            self.u2(self.pool.long(_wrap(value, 64)))

    def push_float(self, value: float) -> None:
        self._ldc(self.pool.float_(value))

    def push_double(self, value: float) -> None:
        self.u1(_LDC2_W)
        self.u2(self.pool.double(value))

    def push_string(self, text: str) -> None:
        self._ldc(self.pool.string(text))

    def _ldc(self, index: int) -> None:
        # `ldc` indexes the pool with ONE byte. Past 255 entries it silently
        # addresses the wrong constant rather than failing, which is the worst
        # available outcome, so the width is chosen from the index every time.
        if index <= 0xFF:
            self.u1(_LDC)
            self.u1(index)
        else:
            self.u1(_LDC_W)
            self.u2(index)

    def getstatic(self, owner: str, name: str, descriptor: str) -> None:
        self.u1(_GETSTATIC)
        self.u2(self.pool.fieldref(owner, name, descriptor))

    def putstatic(self, owner: str, name: str, descriptor: str) -> None:
        self.u1(_PUTSTATIC)
        self.u2(self.pool.fieldref(owner, name, descriptor))

    def invoke(self, kind: str, owner: str, name: str, descriptor: str) -> None:
        self.u1({"static": _INVOKESTATIC, "virtual": _INVOKEVIRTUAL,
                 "special": _INVOKESPECIAL}[kind])
        self.u2(self.pool.methodref(owner, name, descriptor))

    def invoke_interface(self, owner: str, name: str, descriptor: str) -> None:
        """`invokeinterface`, which is encoded unlike every other invoke.

        It carries the argument count and a zero byte after the pool index --
        a relic, and one the verifier checks: a wrong count is a class that
        does not load. The count is slots, so a long or a double is two.
        """
        from .classpath import parse_parameters
        count = 1 + sum(2 if p in ("J", "D") else 1
                        for p in parse_parameters(descriptor))
        self.u1(_INVOKEINTERFACE)
        self.u2(self.pool.interface_methodref(owner, name, descriptor))
        self.u1(count)
        self.u1(0)

    def checkcast(self, internal_name: str) -> None:
        self.u1(_CHECKCAST)
        self.u2(self.pool.class_ref(internal_name))

    def new(self, internal_name: str) -> None:
        self.u1(_NEW)
        self.u2(self.pool.class_ref(internal_name))

    def newarray_byte(self) -> None:
        self.u1(_NEWARRAY)
        self.u1(T_BYTE)

    def anewarray(self, internal_name: str) -> None:
        self.u1(_ANEWARRAY)
        self.u2(self.pool.class_ref(internal_name))

    def lookupswitch(self, default: str, cases: list[tuple[int, str]]) -> None:
        """A multi-way branch. `cases` need not be sorted; the format needs it.

        `lookupswitch` is padded to a four-byte boundary MEASURED FROM THE
        START OF THE METHOD, which is why this cannot be assembled in isolation
        and then spliced -- the padding depends on where it lands.
        """
        base = len(self.code)
        self.u1(_LOOKUPSWITCH)
        while len(self.code) % 4:
            self.u1(0)
        self._patches.append((len(self.code), default, base, 4))
        self.code += b"\0\0\0\0"
        pairs = sorted(cases)
        self.code += struct.pack(">i", len(pairs))
        for value, label in pairs:
            self.code += struct.pack(">i", _wrap(value, 32))
            self._patches.append((len(self.code), label, base, 4))
            self.code += b"\0\0\0\0"

    # ── finishing ───────────────────────────────────────────────────────────
    def finish(self) -> None:
        # Checked before the offsets rather than after: past this size the
        # class is unloadable whatever the branches say, and "the method is too
        # large" is the answer to both questions.
        if len(self.code) > MAX_METHOD_BYTES:
            raise ClassFileLimit(
                f"{self.name} compiles to {len(self.code)} bytes of bytecode, "
                f"past the {MAX_METHOD_BYTES} a JVM method may hold"
                f"\nsplit the function up; a class file has no equivalent of "
                f"an assembler's long jump for this")
        for offset, label, base, width in self._patches:
            if label not in self._labels:
                raise KeyError(
                    f"{self.name}: branch to undefined label {label!r}")
            delta = self._labels[label] - base
            if width == 2 and not -0x8000 <= delta <= 0x7FFF:
                # Reachable from a single very large function. Saying so beats
                # writing a truncated offset, which produces a class that
                # verifies and jumps into the middle of an instruction.
                raise ClassFileLimit(
                    f"{self.name}: branch to {label!r} is {delta} bytes away, "
                    f"past the 32767 a 16-bit offset holds -- the method is "
                    f"too large for this backend")
            struct.pack_into(">h" if width == 2 else ">i", self.code, offset,
                             delta)

    def stack_map_table(self) -> bytes | None:
        """A frame at every branch target, or None if none is needed.

        Every frame is the same: all locals as declared, empty operand stack.
        That is only true because of the two invariants in the module
        docstring; see there before changing anything here.
        """
        targets = sorted(
            {self._labels[label] for _, label, _, _ in self._patches
             if label in self._labels}
            | {p for p in self._frame_points if 0 < p < len(self.code)})
        if not targets:
            return None

        locals_blob = struct.pack(">H", len(self.frame_locals))
        for item in self.frame_locals:
            locals_blob += _verification_type(self.pool, item)

        entries = b""
        previous = -1
        for offset in targets:
            entries += bytes([255])                          # full_frame
            entries += struct.pack(">H", offset - previous - 1)
            entries += locals_blob
            entries += struct.pack(">H", 0)                  # empty stack
            previous = offset
        return struct.pack(">H", len(targets)) + entries

    def serialize(self, emit_frames: bool) -> bytes:
        self.finish()
        attributes, count = b"", 0
        if emit_frames:
            table = self.stack_map_table()
            if table is not None:
                attributes += struct.pack(
                    ">HI", self.pool.utf8("StackMapTable"), len(table)) + table
                count = 1

        code = struct.pack(">HHI", self.max_stack, self.max_locals,
                           len(self.code))
        code += bytes(self.code)
        code += struct.pack(">H", 0)                         # no handlers
        code += struct.pack(">H", count) + attributes

        body = struct.pack(">HHHH", self.access, self.pool.utf8(self.name),
                           self.pool.utf8(self.descriptor), 1)
        body += struct.pack(">HI", self.pool.utf8("Code"), len(code)) + code
        return body


def _verification_type(pool: ConstantPool, item) -> bytes:
    if isinstance(item, tuple):
        return bytes([ITEM_OBJECT]) + struct.pack(">H", pool.class_ref(item[1]))
    return bytes([item])


def _wrap(value: int, bits: int) -> int:
    """Two's-complement `value` in `bits`, as a signed Python int.

    The IR permits a constant the target type cannot hold -- `u64` values above
    2**63 arrive as positive Python ints -- and `struct.pack(">q", ...)` raises
    on those. Wrapping is what the machine does with them.
    """
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


class ClassBuilder:
    """A class of static methods and static fields."""

    def __init__(self, internal_name: str, class_version: int,
                 superclass: str = "java/lang/Object") -> None:
        self.pool = ConstantPool()
        self.internal_name = internal_name
        self.superclass = superclass
        self.class_version = class_version
        self.methods: list[MethodBuilder] = []
        self.fields: list[tuple[str, str, int]] = []
        self.access = ACC_PUBLIC | ACC_SUPER | ACC_FINAL

    @property
    def needs_stack_map(self) -> bool:
        from .version import STACK_MAP_REQUIRED_FROM
        return self.class_version >= STACK_MAP_REQUIRED_FROM

    def field(self, name: str, descriptor: str,
              access: int = ACC_PRIVATE | ACC_STATIC) -> None:
        self.fields.append((name, descriptor, access))

    def method(self, name: str, descriptor: str,
               access: int = ACC_STATIC) -> MethodBuilder:
        builder = MethodBuilder(self.pool, name, descriptor, access)
        self.methods.append(builder)
        return builder

    def serialize(self) -> bytes:
        # Methods are serialized FIRST because doing so interns their names,
        # descriptors and constants, and the pool has to be complete before it
        # is written out ahead of them in the file.
        methods = b"".join(m.serialize(self.needs_stack_map)
                           for m in self.methods)
        fields = b"".join(
            struct.pack(">HHHH", access, self.pool.utf8(name),
                        self.pool.utf8(descriptor), 0)
            for name, descriptor, access in self.fields)
        this_class = self.pool.class_ref(self.internal_name)
        super_class = self.pool.class_ref(self.superclass)

        out = b"\xca\xfe\xba\xbe"
        out += struct.pack(">HH", CLASS_VERSION_MINOR, self.class_version)
        out += self.pool.serialize()
        out += struct.pack(">HHH", self.access, this_class, super_class)
        out += struct.pack(">H", 0)                          # no interfaces
        out += struct.pack(">H", len(self.fields)) + fields
        out += struct.pack(">H", len(self.methods)) + methods
        out += struct.pack(">H", 0)                          # no attributes
        return out
