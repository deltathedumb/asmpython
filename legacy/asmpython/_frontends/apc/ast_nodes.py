"""APC syntax tree.

Deliberately small: this frontend targets the low-level core of the language
(functions, integer/float work, control flow, layouts, enums) -- the part that
maps directly onto the neutral SSA IR. Higher-level surface (``type`` classes,
``string`` values, generics) parses to nothing here and is rejected with a
clear message rather than half-lowered.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    line: int = 0
    col: int = 0


# ── expressions ──────────────────────────────────────────────────────────────
@dataclass
class Literal(Node):
    value: object = 0
    kind: str = "int"          # int | float | str | bool | none


@dataclass
class Name(Node):
    ident: str = ""


@dataclass
class NsAccess(Node):
    """``Enum::Member`` -- resolved at compile time."""
    namespace: str = ""
    member: str = ""


@dataclass
class Member(Node):
    """``h.len`` -- a layout field read through a pointer."""
    obj: object = None
    field: str = ""


@dataclass
class Index(Node):
    obj: object = None
    index: object = None


@dataclass
class Unary(Node):
    op: str = ""
    operand: object = None


@dataclass
class Binary(Node):
    op: str = ""
    lhs: object = None
    rhs: object = None


@dataclass
class Call(Node):
    callee: object = None
    args: list = field(default_factory=list)


@dataclass
class Cast(Node):
    expr: object = None
    type_name: str = ""


@dataclass
class SizeOf(Node):
    type_name: str = ""


# ── statements ───────────────────────────────────────────────────────────────
@dataclass
class VarDecl(Node):
    name: str = ""
    type_name: str | None = None
    value: object = None
    mutable: bool = True       # `let` vs `const`


@dataclass
class Assign(Node):
    target: object = None
    value: object = None


@dataclass
class If(Node):
    cond: object = None
    then_body: list = field(default_factory=list)
    else_body: list = field(default_factory=list)


@dataclass
class While(Node):
    cond: object = None
    body: list = field(default_factory=list)


@dataclass
class For(Node):
    var: str = ""
    start: object = None
    end: object = None
    body: list = field(default_factory=list)


@dataclass
class Return(Node):
    value: object = None


@dataclass
class ExprStmt(Node):
    expr: object = None


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


# ── declarations ─────────────────────────────────────────────────────────────
@dataclass
class Param(Node):
    name: str = ""
    type_name: str = ""
    default: object = None


@dataclass
class FuncDecl(Node):
    name: str = ""
    params: list = field(default_factory=list)
    ret_type: str | None = None
    body: list | None = None       # None for `extern`
    is_extern: bool = False
    is_plain: bool = False


@dataclass
class LayoutField(Node):
    """``magic: bytes[2] = 0``

    Geometry only. A layout says where a field is and how big it is; what the
    bytes *mean* is chosen at the use site with ``as``, never declared here.
    """
    name: str = ""
    size: int = 0                  # bytes
    offset: int | None = None      # None => packs after the previous field


@dataclass
class LayoutDecl(Node):
    name: str = ""
    fields: list = field(default_factory=list)


@dataclass
class EnumMember(Node):
    name: str = ""
    value: object = None           # None => auto


@dataclass
class EnumDecl(Node):
    name: str = ""
    repr_type: str | None = None   # `enum X[u8]` -- pinned representation
    members: list = field(default_factory=list)


@dataclass
class FieldDecl(Node):
    """``pub const Parent.Red: int = red`` -- a field, declared by assigning it.

    A type's layout is the ordered set of these found in its constructor, so
    they must be unconditional and at the constructor's top level.
    """
    name: str = ""
    type_name: str | None = None
    value: object = None
    public: bool = False
    mutable: bool = False


@dataclass
class NameDecl(Node):
    """``name lllib::bits`` or ``name lllib::bits { ... }``

    Without a block it names the namespace the REST OF THE FILE belongs to --
    one line at the top instead of indenting everything. With a block it scopes
    just that block, so a file can hold more than one.
    """
    name: str = ""                     # may be qualified: "lllib::bits"
    parent: str | None = None
    body: list | None = None           # None => applies to the rest of the file


@dataclass
class TypeDecl(Node):
    name: str = ""
    parent: str | None = None
    methods: list = field(default_factory=list)


@dataclass
class ImportDecl(Node):
    """``import std::framebuf`` / ``import std::(a, b)``"""
    module: str = ""                       # dotted-free path, e.g. "std::framebuf"
    names: list = field(default_factory=list)


@dataclass
class ExportDecl(Node):
    names: list = field(default_factory=list)


@dataclass
class Module(Node):
    decls: list = field(default_factory=list)
