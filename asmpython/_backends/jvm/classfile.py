"""Minimal JVM class-file writer.

Enough of the format to emit a class of static methods: constant pool, fields,
methods, and the Code attribute. Nothing here knows about asmpython -- see
codegen.py for the IR translation.

Class file version is deliberately **49.0 (Java 5)**. From version 50 the
verifier consults ``StackMapTable``, and from 51 it is mandatory; at 49 the JVM
uses the old type-inference verifier and computes frames itself. Since every
value asmpython lowers is a ``long`` or ``double`` in a local slot, there is
nothing to gain from the newer verifier and a great deal of machinery to avoid.
Modern JVMs still load version 49 class files.
"""

from __future__ import annotations

import struct

CLASS_VERSION_MINOR = 0
CLASS_VERSION_MAJOR = 49  # Java 5 -- see module docstring

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
        self.max_locals = 0
        # A conservative operand-stack bound. Tracking the true high-water mark
        # would need real flow analysis; every sequence this backend emits is a
        # short expression evaluation, so a fixed headroom is both simpler and
        # safe. The verifier rejects an UNDERestimate, never an over-estimate.
        self.max_stack = 16
        self._labels: dict[str, int] = {}
        self._patches: list[tuple[int, str, int]] = []  # (offset, label, base)

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

    def finish(self) -> None:
        for offset, label, base in self._patches:
            if label not in self._labels:
                raise KeyError(f"jump to undefined label {label!r} in {self.name}")
            delta = self._labels[label] - base
            struct.pack_into(">h", self.code, offset, delta)

    def serialize(self) -> bytes:
        self.finish()
        code_attr = struct.pack(">HHI", self.max_stack, self.max_locals, len(self.code))
        code_attr += bytes(self.code)
        code_attr += struct.pack(">HH", 0, 0)  # exception table, attributes
        body = struct.pack(">HHHH", self.access, self.pool.utf8(self.name),
                           self.pool.utf8(self.descriptor), 1)
        body += struct.pack(">HI", self.pool.utf8("Code"), len(code_attr)) + code_attr
        return body


class ClassBuilder:
    """Assembles a class of static methods and static fields."""

    def __init__(self, internal_name: str, superclass: str = "java/lang/Object") -> None:
        self.pool = ConstantPool()
        self.internal_name = internal_name
        self.superclass = superclass
        self.methods: list[MethodBuilder] = []
        self.fields: list[tuple[str, str]] = []

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
        method_bytes = b"".join(m.serialize() for m in self.methods)
        field_bytes = b""
        for name, descriptor in self.fields:
            field_bytes += struct.pack(
                ">HHHH", ACC_PUBLIC | ACC_STATIC, self.pool.utf8(name),
                self.pool.utf8(descriptor), 0,
            )
        this_class = self.pool.class_ref(self.internal_name)
        super_class = self.pool.class_ref(self.superclass)

        out = b"\xca\xfe\xba\xbe"
        out += struct.pack(">HH", CLASS_VERSION_MINOR, CLASS_VERSION_MAJOR)
        out += self.pool.serialize()
        out += struct.pack(">HHH", ACC_PUBLIC | ACC_SUPER | ACC_FINAL, this_class, super_class)
        out += struct.pack(">H", 0)  # interfaces
        out += struct.pack(">H", len(self.fields)) + field_bytes
        out += struct.pack(">H", len(self.methods)) + method_bytes
        out += struct.pack(">H", 0)  # class attributes
        return out
