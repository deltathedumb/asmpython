"""Minimal JVM class-file writer.

Enough of the format to emit a class of static methods: constant pool, fields,
methods, and the Code attribute. Nothing here knows about asmpython -- see
codegen.py for the IR translation.

The class-file version is selectable (``--class-version`` / ``--java-version``),
which means this writer has to satisfy both verifiers. Below version 50 the JVM
infers stack frames itself; from 50 it consults ``StackMapTable`` and from 51 it
is mandatory. Frames are emitted whenever the target version is 50 or above --
see :meth:`MethodBuilder.stack_map_table` for why that stays cheap here.
"""

from __future__ import annotations

import struct

CLASS_VERSION_MINOR = 0
CLASS_VERSION_MAJOR = 52  # Java 8: the oldest version still accepted everywhere

# Java release -> highest class-file major version that release emits.
JAVA_TO_CLASS_VERSION = {
    "1.1": 45, "1.2": 46, "1.3": 47, "1.4": 48,
    "1.5": 49, "5": 49,
    "1.6": 50, "6": 50,
    "1.7": 51, "7": 51,
    "1.8": 52, "8": 52,
    "9": 53, "10": 54, "11": 55, "12": 56, "13": 57, "14": 58,
    "15": 59, "16": 60, "17": 61, "18": 62, "19": 63, "20": 64,
    "21": 65, "22": 66, "23": 67, "24": 68, "25": 69,
}

# From this version the verifier requires a StackMapTable on every method with
# branches. Below it the old type-inference verifier computes frames itself.
STACK_MAP_REQUIRED_FROM = 50

# StackMapTable verification_type_info tags.
ITEM_TOP = 0
ITEM_INTEGER = 1
ITEM_FLOAT = 2
ITEM_DOUBLE = 3
ITEM_LONG = 4
ITEM_OBJECT = 7


def resolve_class_version(class_version=None, java_version=None) -> int:
    """Pick the class-file major version from the two CLI options.

    ``--class-version`` wins outright; ``--java-version`` is looked up in the
    table above. An unknown Java release is an error rather than a silent
    fallback, because quietly emitting an older class than asked for produces a
    jar that works here and fails on the user's target.
    """
    if class_version not in (None, ""):
        major = int(class_version)
        if not 45 <= major <= with_headroom():
            raise ValueError(
                f"class-file version {major} is outside the supported range "
                f"45..{with_headroom()}"
            )
        return major
    if java_version not in (None, ""):
        key = str(java_version).strip()
        if key.startswith("1.") and key not in JAVA_TO_CLASS_VERSION:
            key = key[2:]
        if key not in JAVA_TO_CLASS_VERSION:
            known = ", ".join(sorted(JAVA_TO_CLASS_VERSION, key=_version_sort_key))
            raise ValueError(f"unknown Java version {java_version!r}; known: {known}")
        return JAVA_TO_CLASS_VERSION[key]
    return CLASS_VERSION_MAJOR


def with_headroom() -> int:
    """Allow a few versions past what we know, so a new JDK is not blocked."""
    return max(JAVA_TO_CLASS_VERSION.values()) + 5


def _version_sort_key(text: str):
    parts = text.split(".")
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else (999,)

ACC_PUBLIC = 0x0001
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_SUPER = 0x0020

# Constant pool tags
CONSTANT_Utf8 = 1
CONSTANT_Integer = 3
CONSTANT_Float = 4
CONSTANT_Long = 5
CONSTANT_Double = 6
CONSTANT_Class = 7
CONSTANT_String = 8
CONSTANT_Fieldref = 9
CONSTANT_Methodref = 10
CONSTANT_NameAndType = 12


class ConstantPool:
    """Interned constant pool.

    Longs and doubles occupy TWO entries each -- a quirk of the format that is
    easy to forget and produces a class the verifier rejects with a confusing
    message, so the width is handled here rather than at call sites.
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
            self._entries.append(b"")  # unusable slot after long/double
        self._index[key] = index
        return index

    def utf8(self, text: str) -> int:
        raw = text.encode("utf-8")
        return self._add(("utf8", text), bytes([CONSTANT_Utf8]) + struct.pack(">H", len(raw)) + raw)

    def integer(self, value: int) -> int:
        return self._add(("int", value), bytes([CONSTANT_Integer]) + struct.pack(">i", value))

    def long(self, value: int) -> int:
        return self._add(("long", value), bytes([CONSTANT_Long]) + struct.pack(">q", value), width=2)

    def double(self, value: float) -> int:
        return self._add(("double", value), bytes([CONSTANT_Double]) + struct.pack(">d", value), width=2)

    def string(self, text: str) -> int:
        return self._add(("str", text), bytes([CONSTANT_String]) + struct.pack(">H", self.utf8(text)))

    def class_ref(self, internal_name: str) -> int:
        return self._add(
            ("class", internal_name),
            bytes([CONSTANT_Class]) + struct.pack(">H", self.utf8(internal_name)),
        )

    def name_and_type(self, name: str, descriptor: str) -> int:
        return self._add(
            ("nat", name, descriptor),
            bytes([CONSTANT_NameAndType])
            + struct.pack(">HH", self.utf8(name), self.utf8(descriptor)),
        )

    def methodref(self, owner: str, name: str, descriptor: str) -> int:
        return self._add(
            ("method", owner, name, descriptor),
            bytes([CONSTANT_Methodref])
            + struct.pack(">HH", self.class_ref(owner), self.name_and_type(name, descriptor)),
        )

    def fieldref(self, owner: str, name: str, descriptor: str) -> int:
        return self._add(
            ("field", owner, name, descriptor),
            bytes([CONSTANT_Fieldref])
            + struct.pack(">HH", self.class_ref(owner), self.name_and_type(name, descriptor)),
        )

    def serialize(self) -> bytes:
        out = struct.pack(">H", len(self._entries) + 1)
        for entry in self._entries:
            out += entry
        return out


class MethodBuilder:
    """Accumulates bytecode for one method, with forward-reference patching."""

    def __init__(self, pool: ConstantPool, name: str, descriptor: str,
                 access: int = ACC_PUBLIC | ACC_STATIC) -> None:
        self.pool = pool
        self.name = name
        self.descriptor = descriptor
        self.access = access
        self.code = bytearray()
        # Local slot layout for StackMapTable, as (slot, ITEM_LONG|ITEM_DOUBLE)
        # in slot order. codegen fills this in; empty means "emit no frames",
        # which is only valid below class version 50.
        self.frame_locals: list[tuple[int, int]] = []
        self.max_locals = 0
        # A conservative operand-stack bound. Tracking the true high-water mark
        # would need real flow analysis; every sequence this backend emits is a
        # short expression evaluation, so a fixed headroom is both simpler and
        # safe. The verifier rejects an UNDERestimate, never an over-estimate.
        self.max_stack = 16
        self._labels: dict[str, int] = {}
        # Offsets that need a frame even though nothing branches to them --
        # code following an unconditional transfer is unreachable by
        # fallthrough, and the verifier demands a frame there.
        self._extra_frame_points: set[int] = set()
        self._patches: list[tuple[int, str, int]] = []  # (offset, label, base)
        # (start, end, handler, caught class) — the JVM's answer to longjmp.
        self.exception_table: list[tuple[int, int, int, str]] = []
        # Offsets that are catch targets. Their frame is the one exception to
        # the empty-stack invariant: the JVM pushes the caught exception.
        self._handler_stack: dict[int, str] = {}

    # ---- raw emission ----------------------------------------------------

    def u1(self, value: int) -> None:
        self.code.append(value & 0xFF)

    def u2(self, value: int) -> None:
        self.code += struct.pack(">H", value & 0xFFFF)

    def here(self) -> int:
        return len(self.code)

    def mark(self, label: str) -> None:
        self._labels[label] = len(self.code)

    def jump(self, opcode: int, label: str) -> None:
        base = len(self.code)
        self.u1(opcode)
        self._patches.append((len(self.code), label, base))
        self.u2(0)  # patched in finish()

    def frame_point(self) -> None:
        """Mark the current offset as needing a stack map frame."""
        self._extra_frame_points.add(len(self.code))

    def finish(self) -> None:
        for offset, label, base in self._patches:
            if label not in self._labels:
                raise KeyError(f"jump to undefined label {label!r} in {self.name}")
            delta = self._labels[label] - base
            struct.pack_into(">h", self.code, offset, delta)

    def stack_map_table(self) -> bytes | None:
        """A StackMapTable covering every branch target, or None if not needed.

        This is cheap here only because codegen maintains two invariants: every
        local is written at method entry so no slot's type ever changes, and the
        operand stack is empty at every branch target. That makes every frame
        identical, so each entry is a `full_frame` listing the same locals with
        an empty stack -- no dataflow analysis required.

        A long or double contributes ONE entry to the locals array even though
        it occupies two slots; getting that wrong is the usual reason a
        hand-built StackMapTable is rejected.
        """
        targets = sorted(
            {self._labels[label] for _, label, _ in self._patches if label in self._labels}
            | {p for p in self._extra_frame_points if p < len(self.code)}
            | {p for p in self._handler_stack if p < len(self.code)}
        )
        if not targets or not self.frame_locals:
            return None

        locals_blob = struct.pack(">H", len(self.frame_locals))
        for _slot, item in self.frame_locals:
            locals_blob += bytes([item])

        entries = b""
        previous = -1
        for offset in targets:
            delta = offset - previous - 1
            previous = offset
            entries += bytes([255])                    # full_frame
            entries += struct.pack(">H", delta)
            entries += locals_blob
            caught = self._handler_stack.get(offset)
            if caught is None:
                entries += struct.pack(">H", 0)        # empty operand stack
            else:
                # A catch target is the one place the stack is not empty: the
                # JVM has already pushed the caught exception onto it.
                entries += struct.pack(">H", 1)
                entries += bytes([ITEM_OBJECT]) + struct.pack(">H", self.pool.class_ref(caught))
        return struct.pack(">H", len(targets)) + entries

    def catch(self, start: int, end: int, handler: int, caught: str) -> None:
        """Route exceptions thrown in [start, end) to `handler`."""
        self.exception_table.append((start, end, handler, caught))
        self._handler_stack[handler] = caught

    def serialize(self, emit_frames: bool = True) -> bytes:
        self.finish()
        attributes = b""
        attribute_count = 0
        if emit_frames:
            table = self.stack_map_table()
            if table is not None:
                attributes += struct.pack(">HI", self.pool.utf8("StackMapTable"),
                                          len(table)) + table
                attribute_count = 1

        code_attr = struct.pack(">HHI", self.max_stack, self.max_locals, len(self.code))
        code_attr += bytes(self.code)
        code_attr += struct.pack(">H", len(self.exception_table))
        for start, end, handler, caught in self.exception_table:
            code_attr += struct.pack(">HHHH", start, end, handler,
                                     self.pool.class_ref(caught))
        code_attr += struct.pack(">H", attribute_count) + attributes
        body = struct.pack(">HHHH", self.access, self.pool.utf8(self.name),
                           self.pool.utf8(self.descriptor), 1)
        body += struct.pack(">HI", self.pool.utf8("Code"), len(code_attr)) + code_attr
        return body


class ClassBuilder:
    """Assembles a class of static methods and static fields."""

    def __init__(self, internal_name: str, superclass: str = "java/lang/Object",
                 class_version: int = CLASS_VERSION_MAJOR) -> None:
        self.pool = ConstantPool()
        self.internal_name = internal_name
        self.superclass = superclass
        self.class_version = class_version
        self.methods: list[MethodBuilder] = []
        self.fields: list[tuple[str, str]] = []

    @property
    def needs_stack_map(self) -> bool:
        return self.class_version >= STACK_MAP_REQUIRED_FROM

    def add_field(self, name: str, descriptor: str) -> None:
        self.fields.append((name, descriptor))

    def method(self, name: str, descriptor: str,
               access: int = ACC_PUBLIC | ACC_STATIC) -> MethodBuilder:
        builder = MethodBuilder(self.pool, name, descriptor, access)
        self.methods.append(builder)
        return builder

    def serialize(self) -> bytes:
        # Method bodies are serialized FIRST: doing so interns their names,
        # descriptors and constants, and the pool must be complete before it is
        # written out ahead of them in the file.
        method_bytes = b"".join(m.serialize(self.needs_stack_map) for m in self.methods)
        field_bytes = b""
        for name, descriptor in self.fields:
            field_bytes += struct.pack(
                ">HHHH", ACC_PUBLIC | ACC_STATIC, self.pool.utf8(name),
                self.pool.utf8(descriptor), 0,
            )
        this_class = self.pool.class_ref(self.internal_name)
        super_class = self.pool.class_ref(self.superclass)

        out = b"\xca\xfe\xba\xbe"
        out += struct.pack(">HH", CLASS_VERSION_MINOR, self.class_version)
        out += self.pool.serialize()
        out += struct.pack(">HHH", ACC_PUBLIC | ACC_SUPER | ACC_FINAL, this_class, super_class)
        out += struct.pack(">H", 0)  # interfaces
        out += struct.pack(">H", len(self.fields)) + field_bytes
        out += struct.pack(">H", len(self.methods)) + method_bytes
        out += struct.pack(">H", 0)  # class attributes
        return out
