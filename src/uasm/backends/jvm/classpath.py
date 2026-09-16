"""Reading a class path: what Java types a program may name.

`classfile.py` writes class files; this reads them. Both halves of the format
live in this package rather than one being a dependency, because they have to
agree about the constant pool and the two would otherwise drift in the way
that only shows up when a class this compiler wrote is read by the same
compiler and comes back different.

WHY READ AT ALL. A backend that wants to offer `import com.minecraft.block`
has to know what is in that package -- which classes exist, which methods they
have, and what each one's descriptor is, so that a call can be type-checked
before it is emitted rather than failing at run time with a
`NoSuchMethodError`. Nothing else knows: the jar is the specification.

WHAT IS READ, AND WHAT IS SKIPPED. The constant pool, the class and superclass
names, the interfaces, and the name/descriptor/flags of every field and method.
Method BODIES are skipped -- their contents are the one part of a class file a
caller can never observe -- so this reads a large jar quickly and holds very
little of it.
"""
from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ACC_PUBLIC = 0x0001
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_INTERFACE = 0x0200
ACC_ABSTRACT = 0x0400

#: Constant pool tags whose payload this needs to walk past. A reader that
#: guesses a width here does not fail at that entry -- it fails at some later
#: one, having lost its place, which is why every tag is listed.
_FIXED = {
    3: 4,    # Integer
    4: 4,    # Float
    5: 8,    # Long      (occupies two entries)
    6: 8,    # Double    (occupies two entries)
    7: 2,    # Class
    8: 2,    # String
    9: 4,    # Fieldref
    10: 4,   # Methodref
    11: 4,   # InterfaceMethodref
    12: 4,   # NameAndType
    15: 3,   # MethodHandle
    16: 2,   # MethodType
    17: 4,   # Dynamic
    18: 4,   # InvokeDynamic
    19: 2,   # Module
    20: 2,   # Package
}
_WIDE = {5, 6}


class ClassFileError(ValueError):
    """A file on the class path that is not a class file this can read."""


@dataclass
class Method:
    name: str
    descriptor: str
    access: int

    @property
    def is_static(self) -> bool:
        return bool(self.access & ACC_STATIC)

    @property
    def is_public(self) -> bool:
        return bool(self.access & ACC_PUBLIC)

    @property
    def is_constructor(self) -> bool:
        return self.name == "<init>"

    @property
    def parameters(self) -> list[str]:
        return parse_parameters(self.descriptor)

    @property
    def returns(self) -> str:
        return self.descriptor[self.descriptor.index(")") + 1:]

    @property
    def arity(self) -> int:
        return len(self.parameters)


@dataclass
class JavaClass:
    #: Internal name, `com/minecraft/block/Block`.
    internal: str
    superclass: str | None
    interfaces: tuple[str, ...]
    access: int
    methods: list[Method] = field(default_factory=list)

    @property
    def package(self) -> str:
        """Dotted package name: `com.minecraft.block`."""
        head, _, _ = self.internal.rpartition("/")
        return head.replace("/", ".")

    @property
    def simple(self) -> str:
        return self.internal.rpartition("/")[2]

    @property
    def binary(self) -> str:
        """Dotted binary name: `com.minecraft.block.Block`."""
        return self.internal.replace("/", ".")

    @property
    def is_interface(self) -> bool:
        return bool(self.access & ACC_INTERFACE)

    @property
    def is_abstract(self) -> bool:
        return bool(self.access & ACC_ABSTRACT)

    def constructors(self) -> list[Method]:
        return [m for m in self.methods if m.is_constructor and m.is_public]

    def instance_methods(self, name: str) -> list[Method]:
        return [m for m in self.methods
                if m.name == name and m.is_public and not m.is_static
                and not m.is_constructor]

    def static_methods(self, name: str) -> list[Method]:
        return [m for m in self.methods
                if m.name == name and m.is_public and m.is_static]


def parse_parameters(descriptor: str) -> list[str]:
    """The parameter descriptors of a method descriptor, in order.

    `(ILjava/lang/String;[I)V` is three parameters, and the only way to know
    that is to walk it: a type is one character, or `L...;`, or any number of
    `[` in front of either.
    """
    out: list[str] = []
    body = descriptor[1:descriptor.index(")")]
    i = 0
    while i < len(body):
        start = i
        while i < len(body) and body[i] == "[":
            i += 1
        if i >= len(body):
            raise ClassFileError(f"truncated descriptor {descriptor!r}")
        if body[i] == "L":
            end = body.index(";", i)
            i = end + 1
        else:
            i += 1
        out.append(body[start:i])
    return out


def read_class(data: bytes) -> JavaClass:
    """One class file, as much of it as a caller can observe."""
    if len(data) < 10 or data[:4] != b"\xca\xfe\xba\xbe":
        raise ClassFileError("not a class file (bad magic)")
    pool, at = _read_pool(data, 10)
    access, this_index, super_index = struct.unpack_from(">HHH", data, at)
    at += 6
    count = struct.unpack_from(">H", data, at)[0]
    at += 2
    interfaces = []
    for _ in range(count):
        interfaces.append(_class_name(pool,
                                      struct.unpack_from(">H", data, at)[0]))
        at += 2
    at = _skip_members(data, at)                    # fields
    methods, at = _read_methods(data, at, pool)

    return JavaClass(
        internal=_class_name(pool, this_index),
        superclass=_class_name(pool, super_index) if super_index else None,
        interfaces=tuple(interfaces),
        access=access,
        methods=methods,
    )


def _read_pool(data: bytes, at: int) -> tuple[dict, int]:
    count = struct.unpack_from(">H", data, at - 2)[0]
    entries: dict[int, tuple] = {}
    index = 1
    while index < count:
        tag = data[at]
        at += 1
        if tag == 1:                                # Utf8
            length = struct.unpack_from(">H", data, at)[0]
            at += 2
            entries[index] = ("utf8", _decode(data[at:at + length]))
            at += length
        elif tag in _FIXED:
            width = _FIXED[tag]
            entries[index] = ("raw", data[at:at + width])
            at += width
        else:
            raise ClassFileError(f"unknown constant pool tag {tag}")
        index += 2 if tag in _WIDE else 1
    return entries, at


def _decode(raw: bytes) -> str:
    """The class file's modified UTF-8, back to a string.

    `\\xc0\\x80` is U+0000 and a surrogate pair is written as two three-byte
    sequences, neither of which Python's UTF-8 decoder accepts -- so a class
    whose name or descriptor contains either would raise here rather than
    decode wrongly. Both are legal and rare; `surrogatepass` handles the second
    and the first is rewritten first.
    """
    return raw.replace(b"\xc0\x80", b"\x00").decode("utf-8", "surrogatepass")


def _class_name(pool: dict, index: int) -> str:
    kind, payload = pool[index]
    if kind != "raw":
        raise ClassFileError(f"constant {index} is not a class reference")
    return _utf8(pool, struct.unpack(">H", payload)[0])


def _utf8(pool: dict, index: int) -> str:
    kind, payload = pool[index]
    if kind != "utf8":
        raise ClassFileError(f"constant {index} is not a string")
    return payload


def _skip_members(data: bytes, at: int) -> int:
    count = struct.unpack_from(">H", data, at)[0]
    at += 2
    for _ in range(count):
        at += 6                                      # access, name, descriptor
        at = _skip_attributes(data, at)
    return at


def _read_methods(data: bytes, at: int, pool: dict) -> tuple[list, int]:
    count = struct.unpack_from(">H", data, at)[0]
    at += 2
    out = []
    for _ in range(count):
        access, name_i, desc_i = struct.unpack_from(">HHH", data, at)
        at += 6
        out.append(Method(_utf8(pool, name_i), _utf8(pool, desc_i), access))
        at = _skip_attributes(data, at)              # bodies are not read
    return out, at


def _skip_attributes(data: bytes, at: int) -> int:
    count = struct.unpack_from(">H", data, at)[0]
    at += 2
    for _ in range(count):
        length = struct.unpack_from(">I", data, at + 2)[0]
        at += 6 + length
    return at


class ClassPath:
    """Every class a program may name, indexed by internal name.

    Built once per compilation from the jars and directories the user named.
    Later entries do NOT win: the first jar on the path that defines a class is
    the one that provides it, which is what a JVM does and what makes an
    ordering meaningful.
    """

    def __init__(self) -> None:
        self.classes: dict[str, JavaClass] = {}
        #: Dotted package -> {simple name: internal name}.
        self.packages: dict[str, dict[str, str]] = {}
        self.sources: list[str] = []

    def add(self, entry: Path) -> int:
        """Add a jar or a directory of class files. Returns how many it held."""
        if entry.is_dir():
            found = sorted(entry.rglob("*.class"))
            added = sum(self._add_bytes(p.read_bytes(), str(p)) for p in found)
        elif entry.suffix.lower() in (".jar", ".zip", ".jmod"):
            # A `.jmod` is a zip like a jar, with everything under `classes/`.
            # Accepting one is what lets a program name `java.lang.Object` --
            # the JDK has shipped no `rt.jar` since 8, and without the module
            # file there is no way to resolve an inherited `toString`.
            added = 0
            with zipfile.ZipFile(entry) as jar:
                for info in jar.infolist():
                    if info.filename.endswith(".class"):
                        added += self._add_bytes(jar.read(info.filename),
                                                 f"{entry}!{info.filename}")
        else:
            raise ClassFileError(
                f"{entry} is neither a jar nor a directory of class files")
        self.sources.append(str(entry))
        return added

    def _add_bytes(self, data: bytes, where: str) -> int:
        try:
            cls = read_class(data)
        except (ClassFileError, struct.error, IndexError, KeyError):
            # A jar can hold things that are not classes this understands --
            # a newer format version, a module-info, a multi-release entry.
            # Skipping one is right; failing the build over one is not.
            return 0
        if cls.internal in self.classes:
            return 0
        self.classes[cls.internal] = cls
        self.packages.setdefault(cls.package, {})[cls.simple] = cls.internal
        return 1

    def find(self, dotted: str) -> JavaClass | None:
        """A class by its dotted binary name, `com.minecraft.block.Block`."""
        return self.classes.get(dotted.replace(".", "/"))

    def package(self, dotted: str) -> dict[str, str]:
        """{simple name: internal name} for one package, empty if unknown."""
        return self.packages.get(dotted, {})

    def method_of(self, internal: str, name: str, *,
                  static: bool = False) -> list[Method]:
        """Public methods called `name`, searching superclasses.

        Inherited methods are as callable as declared ones, and a mod calling
        `toString()` on a class that does not declare it is the ordinary case
        rather than the exotic one.
        """
        seen: set[str] = set()
        out: list[Method] = []
        at: str | None = internal
        while at and at not in seen:
            seen.add(at)
            cls = self.classes.get(at)
            if cls is None:
                break
            found = (cls.static_methods(name) if static
                     else cls.instance_methods(name))
            out.extend(m for m in found
                       if not any(o.descriptor == m.descriptor for o in out))
            at = cls.superclass
        return out

    def __len__(self) -> int:
        return len(self.classes)


def load(entries) -> ClassPath:
    """Build a `ClassPath` from paths, in order."""
    path = ClassPath()
    for entry in entries:
        path.add(Path(entry))
    return path
