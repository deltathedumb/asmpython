"""The tree `_pyparse` builds.

ONE CLASS, not sixty. A node is a KIND and whatever fields that kind carries,
because the alternative is a class per syntactic form and every one of them is
a constructor that assigns its arguments to attributes of the same name. The
kind is a string, so a walk reads `node.kind == "BinOp"` where CPython's own
tools read `isinstance(node, ast.BinOp)` -- the same test, one indirection
fewer, and no table of classes to keep in step with the grammar.

THE FIELD NAMES ARE CPYTHON'S. `BinOp` has `left`, `op` and `right`; a
`FunctionDef` has `name`, `args`, `body`, `decorator_list` and `returns`. That
is not decoration: the bytecode lowering this feeds was written against the
reference implementation's tree, so matching it makes that an adaptation
rather than a rewrite -- and anyone reading a walk here can check it against
the `ast` documentation directly.
"""


class Node:
    """One tree node: what it is, where it was, and its fields.

    The position is on every node because `SyntaxError` reports one and the
    checks in `_pyvalidate` all have something to point at.
    """

    def __init__(self, kind, line=0, col=0, **fields):
        self.kind = kind
        self.lineno = line
        self.col_offset = col
        for name in fields:
            setattr(self, name, fields[name])
        self._fieldnames = sorted(fields)

    def get(self, name, default=None):
        """A field, or `default` when this kind does not carry one.

        Every walk needs this: `orelse` is absent on a `While` that has no
        `else`, and asking for it should not be an error.
        """
        return getattr(self, name, default)

    def children(self):
        """Every node directly under this one, in field order.

        Flattened over lists, because most fields that hold children hold
        several -- and a walk that had to know which is which would be one
        more table to keep in step with the grammar.
        """
        out = []
        for name in self._fieldnames:
            value = getattr(self, name)
            if isinstance(value, Node):
                out.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Node):
                        out.append(item)
        return out

    def walk(self):
        """This node and everything under it, depth first."""
        out = [self]
        i = 0
        # BY INDEX OVER A GROWING LIST, not recursion: a deeply nested
        # expression would otherwise need a stack frame per level, and the
        # interpreter this runs on has a shallower one than CPython.
        while i < len(out):
            for child in out[i].children():
                out.append(child)
            i = i + 1
        return out

    def __repr__(self):
        parts = []
        for name in self._fieldnames:
            value = getattr(self, name)
            if isinstance(value, Node):
                parts.append(name + "=" + value.kind)
            elif isinstance(value, list):
                parts.append(name + "=[" + str(len(value)) + "]")
            else:
                parts.append(name + "=" + repr(value))
        return self.kind + "(" + ", ".join(parts) + ")"


class Arguments:
    """A function's parameters, in the five groups Python separates.

    Its own class rather than a `Node` because nothing walks INTO it looking
    for statements, and because the five lists have to stay distinguishable:
    which group a parameter is in decides whether a call may fill it by
    position, by keyword, or only by one of them.
    """

    def __init__(self):
        self.posonlyargs = []
        self.args = []
        self.vararg = None
        self.kwonlyargs = []
        self.kw_defaults = []
        self.kwarg = None
        self.defaults = []

    def every_name(self):
        """Every parameter name, in declaration order, including `*rest` and
        `**kw` -- which is what a duplicate-name check has to look at."""
        out = []
        for group in (self.posonlyargs, self.args):
            for one in group:
                out.append(one.arg)
        if self.vararg is not None:
            out.append(self.vararg.arg)
        for one in self.kwonlyargs:
            out.append(one.arg)
        if self.kwarg is not None:
            out.append(self.kwarg.arg)
        return out


class Arg:
    """One parameter: its name, its annotation, and where it was written."""

    def __init__(self, name, annotation=None, line=0, col=0):
        self.arg = name
        self.annotation = annotation
        self.lineno = line
        self.col_offset = col
