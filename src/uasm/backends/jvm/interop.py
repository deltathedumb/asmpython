"""Java types, as modules a program can import.

    import com.minecraft.block as block
    import jvm.com.minecraft.block          # always reaches this backend's

    my_block = block.Block()
    my_block.setName("stone")

WHAT CROSSES THE BOUNDARY. The static subset this backend compiles has four
value types -- `int`, `float`, `bool` and `None` -- so those are what Java
primitives become. Every REFERENCE type becomes a HANDLE: an integer standing
for an entry in a table the generated class keeps. A handle can be stored,
passed to another Java method and used as a receiver; it cannot be taken apart,
because there is nothing in the subset to take it apart into. `getName()`
returns a handle to a `String`, and that handle is exactly as useful as a
`String` is to a program with no strings -- which is to say, useful only by
handing it back to Java, which is the honest answer.

THE FRONTEND IS TOLD, NOT ASKED. Everything the analyser needs -- which classes
a package has, which methods each has, the parameter and result types of every
overload, and THE SYMBOL to emit a call to -- is computed here and published as
plain data (see `package_table`). The frontend never parses a descriptor and
never builds a symbol name, so the two cannot disagree about either. That
matters more than it looks: a mangling scheme written out twice is a mangling
scheme with two spellings, and the failure is a link error against a method the
user can see in `javap`.

LAZILY, because a real class path is enormous. Minecraft is tens of thousands
of classes and a program names a dozen; building every package's table up front
would cost more than the compile. Package names are listed eagerly (that is
just the index) and a package's members are built the first time something
looks inside it.
"""
from __future__ import annotations

from dataclasses import dataclass

from .classpath import ClassPath, JavaClass, Method, parse_parameters

#: How a Java descriptor's types appear to the frontend. The names are the
#: frontend's own (`analysis.SemType.name`), which makes this a data contract
#: between the two halves rather than a shared class -- the same shape the
#: `("call", symbol, arity)` entries have always had.
_PRIMITIVE = {
    "Z": "bool",
    "B": "int", "C": "int", "S": "int", "I": "int", "J": "int",
    "F": "float", "D": "float",
    "V": "None",
}

#: The prefix a handle type carries, so the frontend can tell one from an
#: `int` and can name the class in a diagnostic.
HANDLE = "jvm:"


def type_name(descriptor: str) -> str:
    """The frontend type for one Java descriptor."""
    if descriptor in _PRIMITIVE:
        return _PRIMITIVE[descriptor]
    if descriptor.startswith("L") and descriptor.endswith(";"):
        return HANDLE + descriptor[1:-1]
    if descriptor.startswith("["):
        # An array is a reference like any other. Its element type is not
        # modelled: there is no indexing in the subset to model it for.
        return HANDLE + descriptor
    raise ValueError(f"cannot read descriptor {descriptor!r}")


def internal_of(type_name_: str) -> str:
    """The Java class behind a handle type name."""
    return type_name_[len(HANDLE):]


def is_handle(type_name_: str) -> bool:
    return type_name_.startswith(HANDLE)


@dataclass(frozen=True, slots=True)
class JavaOp:
    """One Java operation the backend knows how to emit."""

    #: "new", "call", "static", or "str".
    kind: str
    #: Owning class, internal form. Empty for "str".
    owner: str
    #: Method name; "<init>" for a constructor, empty for "str".
    method: str
    #: JVM method descriptor. For "str", the literal itself.
    descriptor: str


# ── symbol names ────────────────────────────────────────────────────────────
#
# An IR symbol may hold letters, digits, `_`, `.` and `$`. A Java owner and
# descriptor hold `/`, `(`, `)`, `;`, `[` and -- for an inner class -- `$`, so
# they are escaped.
#
# `$` IS THE SEPARATOR AND SO CANNOT BE THE ESCAPE. Escaping with `$` too made
# `jvm$call$A$dB$m$$pI$rV` impossible to split back apart, and splitting it back
# apart is the point: a symbol has to survive `--emit-ir` and be recompiled
# later, with only the class path and the symbol itself to go on. So the escape
# is `_`, no payload ever contains `$`, and `split("$")` is exact.
#
# `/` becomes `.` rather than an escape because a Java identifier cannot
# contain either, so the mapping is unambiguous and the result is readable --
# which matters when a call goes wrong and the evidence is a symbol.

_ESCAPES = (("_", "_u"), ("$", "_d"), ("[", "_a"), ("(", "_p"), (")", "_r"),
            (";", "_s"), ("/", "."))


def mangle(text: str) -> str:
    for raw, safe in _ESCAPES:
        text = text.replace(raw, safe)
    return text


def unmangle(text: str) -> str:
    out: list[str] = []
    i = 0
    back = {safe[1]: raw for raw, safe in _ESCAPES if safe.startswith("_")}
    while i < len(text):
        ch = text[i]
        if ch == "_" and i + 1 < len(text) and text[i + 1] in back:
            out.append(back[text[i + 1]])
            i += 2
        elif ch == ".":
            out.append("/")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


#: The one symbol that makes a Java `String`. It takes a POINTER to
#: NUL-terminated UTF-8 in the program's own memory rather than carrying the
#: text, because a symbol may hold only identifier characters and a string
#: literal may hold anything -- and because bytes in a global survive
#: `--emit-ir` and a symbol full of escaped unicode would not.
STRING_SYMBOL = "jvm$str"


class Interop:
    """The class path, as importable modules; and the calls they generate.

    One per compilation. Holds the symbol table the backend reads back at
    emission time, which is why the object the driver configures and the object
    that emits have to be the same one.
    """

    def __init__(self, classpath: ClassPath):
        self.classpath = classpath
        #: Symbol -> what to emit for it. Filled as the frontend looks things
        #: up, read when the backend emits the calls it made.
        self.operations: dict[str, JavaOp] = {}
        #: Every string literal that reached a Java parameter, in first-seen
        #: order, so the emitted class has a constant for each.
        self.strings: dict[str, str] = {}

    # ── what `import` reaches ───────────────────────────────────────────────
    def modules(self) -> dict:
        """{package: members} for every package on the class path.

        The values are lazy: a package's member table is built when something
        first asks it for a name.
        """
        return {name: _Package(self, name) for name in self.classpath.packages}

    def package_table(self, package: str) -> dict:
        """The members of one package: its classes."""
        out = {}
        for simple, internal in self.classpath.package(package).items():
            cls = self.classpath.classes[internal]
            out[simple] = ("jclass", self.class_table(cls))
        return out

    def class_table(self, cls: JavaClass) -> dict:
        """Everything the frontend needs about one class.

        Instance methods include INHERITED ones, flattened here rather than
        walked by the analyser: a program calling `toString()` on a class that
        does not declare it is the ordinary case, and the analyser has no
        business knowing what a superclass is.
        """
        instance: dict[str, list] = {}
        static: dict[str, list] = {}
        for name in self._method_names(cls):
            found = self.classpath.method_of(cls.internal, name)
            if found:
                instance[name] = [self._overload("call", cls.internal, m)
                                  for m in found]
            found = self.classpath.method_of(cls.internal, name, static=True)
            if found:
                static[name] = [self._overload("static", cls.internal, m)
                                for m in found]
        return {
            "internal": cls.internal,
            "type": HANDLE + cls.internal,
            # Every supertype, so the frontend can decide assignability
            # without knowing what a class path is. Interfaces included: a
            # parameter typed as one accepts anything implementing it.
            "supers": self._supers(cls),
            "abstract": cls.is_abstract or cls.is_interface,
            "new": [self._overload("new", cls.internal, m)
                    for m in cls.constructors()],
            "static": static,
            "instance": instance,
        }

    def _supers(self, cls: JavaClass) -> list[str]:
        out: list[str] = []
        pending = [cls.superclass, *cls.interfaces]
        while pending:
            at = pending.pop(0)
            if at is None or at in out:
                continue
            out.append(at)
            found = self.classpath.classes.get(at)
            if found is not None:
                pending += [found.superclass, *found.interfaces]
        return out

    def _method_names(self, cls: JavaClass) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        at = cls.internal
        while at:
            here = self.classpath.classes.get(at)
            if here is None:
                break
            for m in here.methods:
                if m.is_public and not m.is_constructor and m.name not in seen:
                    seen.add(m.name)
                    names.append(m.name)
            at = here.superclass
        return names

    def _overload(self, kind: str, owner: str, method: Method) -> dict:
        symbol = self.symbol(kind, owner, method.name, method.descriptor)
        # A constructor's descriptor says it returns void; the CALL produces an
        # instance, which is what the frontend needs to hear.
        returns = HANDLE + owner if kind == "new" else type_name(method.returns)
        return {
            "symbol": symbol,
            "descriptor": method.descriptor,
            "params": [type_name(p)
                       for p in parse_parameters(method.descriptor)],
            "returns": returns,
        }

    def symbol(self, kind: str, owner: str, method: str,
               descriptor: str) -> str:
        """Register an operation and return the symbol that names it."""
        parts = ["jvm", kind, mangle(owner)]
        if kind != "new":
            parts.append(mangle(method))
        parts.append(mangle(descriptor))
        name = "$".join(parts)
        self.operations[name] = JavaOp(kind, owner, method, descriptor)
        return name

    def lookup(self, symbol: str) -> JavaOp | None:
        """The operation a symbol names, parsing it if this has not seen it.

        WITHOUT THIS the backend could only emit calls the frontend had looked
        up in the same process, so `--emit-ir` followed by a separate build
        would fail on every Java call. A symbol says what it means; the class
        path says whether it is true.
        """
        found = self.operations.get(symbol)
        if found is not None:
            return found
        if symbol == STRING_SYMBOL:
            op = JavaOp("str", "", "", "")
            self.operations[symbol] = op
            return op
        parts = symbol.split("$")
        if len(parts) < 4 or parts[0] != "jvm":
            return None
        kind = parts[1]
        if kind == "new" and len(parts) == 4:
            owner, method, descriptor = unmangle(parts[2]), "<init>", parts[3]
        elif kind in ("call", "static") and len(parts) == 5:
            owner, method, descriptor = (unmangle(parts[2]),
                                         unmangle(parts[3]), parts[4])
        else:
            return None
        descriptor = unmangle(descriptor)
        cls = self.classpath.classes.get(owner)
        if cls is None:
            return None
        # Verified against the class path rather than trusted: a symbol is text
        # in a file, and emitting an invoke for a method that is not there
        # produces a class that loads and then dies at the call.
        if kind == "new":
            ok = any(m.descriptor == descriptor for m in cls.constructors())
        else:
            ok = any(m.descriptor == descriptor for m in self.classpath
                     .method_of(owner, method, static=kind == "static"))
        if not ok:
            return None
        op = JavaOp(kind, owner, method, descriptor)
        self.operations[symbol] = op
        return op

    def string_symbol(self) -> str:
        """The symbol that turns UTF-8 bytes in memory into a Java `String`.

        String literals are the one Python value in the subset with no type of
        its own that still has to reach Java: `setName("stone")` is the
        ordinary case, and a `String` parameter cannot be filled any other way
        by a program that has no strings.
        """
        self.operations.setdefault(STRING_SYMBOL, JavaOp("str", "", "", ""))
        return STRING_SYMBOL

    def signature_of(self, symbol: str) -> tuple[list[str], str]:
        """(parameter IR types, result IR type) for one operation.

        The IR types, not the frontend's: this is what the external function
        declaration in the module has to say, and what the backend checks the
        call against.
        """
        op = self.lookup(symbol)
        if op is None:
            raise KeyError(symbol)
        if op.kind == "str":
            return ["ptr"], "i64"
        params = [_ir_type(p) for p in parse_parameters(op.descriptor)]
        if op.kind == "call":
            params.insert(0, "i64")                  # the receiver's handle
        if op.kind == "new":
            return params, "i64"
        result = op.descriptor[op.descriptor.index(")") + 1:]
        return params, _ir_type(result)


def _ir_type(descriptor: str) -> str:
    if descriptor in ("F", "D"):
        return "f64"
    if descriptor == "V":
        return "void"
    return "i64"


class _Package:
    """One package's members, built on first use.

    A mapping rather than a dict because the class path may hold tens of
    thousands of classes and a program names a handful. `modules.resolve`
    only ever calls `.get`, and `importable()` only ever lists the package
    names, so this needs to be no more of a dict than that.
    """

    def __init__(self, interop: Interop, package: str):
        self._interop = interop
        self._package = package
        self._members: dict | None = None

    def _table(self) -> dict:
        if self._members is None:
            self._members = self._interop.package_table(self._package)
        return self._members

    def get(self, name, default=None):
        return self._table().get(name, default)

    def __getitem__(self, name):
        return self._table()[name]

    def __contains__(self, name) -> bool:
        return name in self._table()

    def __iter__(self):
        return iter(self._table())

    def keys(self):
        return self._table().keys()

    def items(self):
        return self._table().items()

    def __len__(self) -> int:
        return len(self._table())
