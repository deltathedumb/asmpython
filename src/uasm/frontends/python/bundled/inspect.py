"""Reading a callable's signature.

COVERAGE: `signature`, `Signature`, `Parameter` and the five parameter kinds,
`_empty`, `isfunction`, `isclass`, `getdoc`.

NOT COVERED: `getsource` and `getsourcelines`, `getmembers`, every frame
helper (`currentframe`, `stack`, `getouterframes`), `signature` of a builtin,
a class or a bound method, `Signature.bind` and `BoundArguments`,
`getfullargspec`, `Signature.replace` / `Parameter.replace`,
`iscoroutinefunction` and the generator predicates, and `unwrap`. A compiled
program keeps no source and no frames, and answering an invented signature
would be worse than not answering.

`getdoc` READS A FUNCTION'S DOCSTRING AND NOT A CLASS'S. A class carries no
`__doc__` through this compiler, so `getdoc(SomeClass)` is None where CPython
answers the text. That is a compiler gap rather than a decision, and it is
stated here because a caller cannot tell the difference between "no docstring"
and "cannot read docstrings".

`Parameter.kind` is a small class of this module's own rather than CPython's
`enum.IntEnum`, so `kind.name` and `kind.value` and comparison all read the
same and `repr(kind)` does not: CPython's is
`<_ParameterKind.KEYWORD_ONLY: 3>`.

Rebuilt from `__code__` rather than from source: `co_varnames` holds the
parameter names in declaration order with `*rest` and `**kw` last,
`co_argcount`/`co_posonlyargcount`/`co_kwonlyargcount` say where each group
ends, and `co_flags` says which variadic parts exist. That is exactly the
information a signature is, which is why it can be recovered.

THE KIND CONSTANTS CARRY AN UNDERSCORED MODULE-LEVEL NAME (`_K_KEYWORD_ONLY`)
and are re-exported under the public one from `Parameter`'s body. CPython
writes `POSITIONAL_ONLY = POSITIONAL_ONLY` there -- valid Python, since the
right-hand side is read from the module before the left binds in the class
namespace -- and this compiler reads the right-hand side as the class
attribute being defined and fails with `type object 'Parameter' has no
attribute 'POSITIONAL_ONLY'`. The rename sidesteps a compiler bug rather than
a language rule; see `docs/STDLIB.md`.
"""


class _Kind:
    """A parameter's kind. Ordered as the parameters themselves must be, which
    is what makes sorting by kind the same as leaving them alone."""

    def __init__(self, name, value):
        self._name_ = name
        self.value = value

    @property
    def name(self):
        return self._name_

    def __repr__(self):
        return self._name_

    def __eq__(self, other):
        if isinstance(other, _Kind):
            return self.value == other.value
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __hash__(self):
        return hash(self.value)


_K_POSITIONAL_ONLY = _Kind("POSITIONAL_ONLY", 0)
_K_POSITIONAL_OR_KEYWORD = _Kind("POSITIONAL_OR_KEYWORD", 1)
_K_VAR_POSITIONAL = _Kind("VAR_POSITIONAL", 2)
_K_KEYWORD_ONLY = _Kind("KEYWORD_ONLY", 3)
_K_VAR_KEYWORD = _Kind("VAR_KEYWORD", 4)


class _Empty:
    """The absence of a default or an annotation, as an object -- because
    None is a value a parameter may legitimately default to."""

    def __repr__(self):
        return "<class 'inspect._empty'>"


_empty = _Empty()


class Parameter:
    POSITIONAL_ONLY = _K_POSITIONAL_ONLY
    POSITIONAL_OR_KEYWORD = _K_POSITIONAL_OR_KEYWORD
    VAR_POSITIONAL = _K_VAR_POSITIONAL
    KEYWORD_ONLY = _K_KEYWORD_ONLY
    VAR_KEYWORD = _K_VAR_KEYWORD
    empty = _empty

    def __init__(self, name, kind, default=_empty, annotation=_empty):
        self.name = name
        self.kind = kind
        self.default = default
        self.annotation = annotation

    def __str__(self):
        head = self.name
        if self.kind == _K_VAR_POSITIONAL:
            head = "*" + head
        elif self.kind == _K_VAR_KEYWORD:
            head = "**" + head
        if self.annotation is not _empty:
            head = head + ": " + _name_of(self.annotation)
        if self.default is not _empty:
            head = head + ("=" if self.annotation is _empty else " = ") \
                + repr(self.default)
        return head

    def __repr__(self):
        # DOUBLE QUOTES, because CPython writes `'<{} "{}">'.format(...)` and
        # `repr(str(self))` gives single ones. A repr is read by people and
        # printed into test output, so a quote is a difference.
        return '<Parameter "%s">' % (self,)


def _name_of(value):
    name = getattr(value, "__name__", None)
    return name if name is not None else repr(value)


class Signature:
    empty = _empty

    def __init__(self, parameters=None, return_annotation=_empty):
        held = {}
        for one in (parameters or []):
            held[one.name] = one
        self.parameters = held
        self.return_annotation = return_annotation

    def __str__(self):
        parts = []
        seen_star = False
        posonly = False
        for name in self.parameters:
            one = self.parameters[name]
            # THE `/` MARKER CLOSES THE POSITIONAL-ONLY GROUP, and it is
            # written where the group ENDS rather than beside any parameter --
            # so it is emitted when the first parameter that is not one
            # arrives, and after the loop when they run to the end.
            if posonly and one.kind != _K_POSITIONAL_ONLY:
                parts.append("/")
                posonly = False
            if one.kind == _K_POSITIONAL_ONLY:
                posonly = True
            # THE BARE `*` MARKER, when a keyword-only parameter follows and
            # no `*rest` already introduced it -- without it the rendered
            # signature would not parse back to the same thing.
            if one.kind == _K_KEYWORD_ONLY and not seen_star:
                parts.append("*")
                seen_star = True
            if one.kind == _K_VAR_POSITIONAL:
                seen_star = True
            parts.append(str(one))
        if posonly:
            parts.append("/")
        out = "(" + ", ".join(parts) + ")"
        # THE RETURN ANNOTATION IS PART OF THE RENDERING. Without it
        # `str(signature(f))` does not read back as the `def` it came from,
        # which is the one property this string is for.
        if self.return_annotation is not _empty:
            out = out + " -> " + _name_of(self.return_annotation)
        return out

    def __repr__(self):
        # NO QUOTES AT ALL here, where `Parameter` has double ones. The two
        # are not written the same way in CPython and copying either rule to
        # the other is a difference a reader sees immediately.
        return "<Signature %s>" % (self,)


def signature(obj):
    """The signature of a Python function."""
    return Signature(_parameters_of(obj), _return_of(obj))


def _return_of(obj):
    held = getattr(obj, "__annotations__", {})
    return held["return"] if "return" in held else _empty


def _parameters_of(obj):
    code = getattr(obj, "__code__", None)
    if code is None:
        raise TypeError("unsupported callable: " + repr(obj))
    names = list(code.co_varnames)
    npos = code.co_argcount
    nposonly = getattr(code, "co_posonlyargcount", 0)
    nkw = code.co_kwonlyargcount
    flags = getattr(code, "co_flags", 0)
    has_var = (flags & 4) != 0
    has_kw = (flags & 8) != 0
    defaults = getattr(obj, "__defaults__", None) or ()
    kwdefaults = getattr(obj, "__kwdefaults__", None) or {}
    hints = getattr(obj, "__annotations__", {})

    out = []
    # THE POSITIONAL DEFAULTS FILL FROM THE RIGHT, which is the only place
    # they can attach: `def f(a, b=1)` has one default and two positions.
    first_default = npos - len(defaults)
    for i in range(npos):
        name = names[i]
        kind = _K_POSITIONAL_ONLY if i < nposonly else _K_POSITIONAL_OR_KEYWORD
        default = defaults[i - first_default] if i >= first_default else _empty
        out.append(Parameter(name, kind, default,
                             hints[name] if name in hints else _empty))
    if has_var:
        # `*rest` SITS BETWEEN the positional group and the keyword-only one,
        # which is where it is written and what makes the rendering parse
        # back. In `co_varnames` it is after them all.
        name = names[npos + nkw]
        out.append(Parameter(name, _K_VAR_POSITIONAL, _empty,
                             hints[name] if name in hints else _empty))
    for i in range(nkw):
        name = names[npos + i]
        out.append(Parameter(name, _K_KEYWORD_ONLY,
                             kwdefaults[name] if name in kwdefaults else _empty,
                             hints[name] if name in hints else _empty))
    if has_kw:
        name = names[len(names) - 1]
        out.append(Parameter(name, _K_VAR_KEYWORD, _empty,
                             hints[name] if name in hints else _empty))
    return out


def isfunction(obj):
    return hasattr(obj, "__code__")


def isclass(obj):
    return isinstance(obj, type)


def getdoc(obj):
    return getattr(obj, "__doc__", None)
