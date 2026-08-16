"""`compile()` inside a produced binary.

WHAT IT IS FOR. A program calls `compile(src, name, "exec")` to ask ONE
question far more often than any other: is this source valid Python? Every
`syntax/*` case in the conformance suite is that question, and answering it
needs a parser rather than a compiler -- which is why this exists before any
bytecode does.

WHAT IT ANSWERS WITH. A `code` object, built from the tree, carrying what a
program reads off one: `co_filename`, `co_name`, `co_flags` and the tree
itself. It is not CPython's code object and it has no bytecode; what it is for
is to BE one to `type(x).__name__`, and to be the thing `exec` and `eval`
run -- see `_pyrun`.

WHY IT IS NOT RECOMMENDED, and why the compiler says so at the call site
rather than here: a produced binary is compiled ahead of time, and source
handed to it at run time cannot be. Everything reached through this is
interpreted rather than native, and the frontend reports that where the call
is written -- a diagnostic a reader sees, rather than a surprise a profiler
finds.

    _pylex      text  -> tokens
    _pyparse    tokens -> tree
    _pyvalidate tree  -> the same tree, or the SyntaxError CPython raises
    _pycompile  the three of them, behind the name a program calls
"""

import warnings

from _pylex import IndentError, LexError
from _pyparse import IndentParseError, ParseError, parse
from _pyvalidate import validate

#: The modes `compile()` takes. `single` compiles one interactive statement,
#: which is `exec` with a different name here -- the difference is what the
#: interactive loop does with the result, and there is no interactive loop.
_MODES = ("exec", "eval", "single", "func_type")


class code:
    """What `compile()` answers.

    NAMED IN LOWER CASE ON PURPOSE: `type(compile(...)).__name__` is `'code'`
    in CPython, and a program prints it. The class is the whole of what most
    uses need; the tree is here for `exec` and `eval` to walk.
    """

    def __init__(self, tree, filename, name, mode):
        self.tree = tree
        self.co_filename = filename
        self.co_name = name
        self.co_qualname = name
        self.co_firstlineno = 1
        self.co_flags = 0
        self.co_argcount = 0
        self.co_kwonlyargcount = 0
        self.co_posonlyargcount = 0
        self.co_varnames = ()
        self.co_names = ()
        self.co_consts = ()
        self._mode = mode

    def co_positions(self):
        """One position per statement, as the rest of this runtime records
        them -- see `apy_pos_add` in link/objects.py for why per statement."""
        out = []
        for node in self.tree.walk():
            line = getattr(node, "lineno", 0)
            if line:
                out.append((line, getattr(node, "end_lineno", line) or line,
                            getattr(node, "col_offset", 0),
                            getattr(node, "end_col_offset", 0) or 0))
        return out

    def __repr__(self):
        return ("<code object " + self.co_name + " at 0x0, file \"" +
                self.co_filename + "\", line 1>")


def _finally_warnings(tree):
    """PEP 765: `return`, `break` or `continue` in a `finally` is a warning.

    NOT AN ERROR. The construct is legal and does something surprising -- it
    DISCARDS a pending exception -- so CPython compiles it and warns, and a
    program that turns warnings into errors gets to decide. Emitted from
    `compile()` because that is where CPython emits it, and where a
    `catch_warnings` block around the call can see it.
    """
    found = []

    def walk(node, in_finally, depth):
        kind = node.kind
        if kind in ("Try", "TryStar"):
            for stmt in node.get("body") or []:
                walk(stmt, in_finally, depth)
            for handler in node.get("handlers") or []:
                for stmt in handler.get("body") or []:
                    walk(stmt, in_finally, depth)
            for stmt in node.get("orelse") or []:
                walk(stmt, in_finally, depth)
            for stmt in node.get("finalbody") or []:
                # THE DEPTH RESTARTS. A `break` inside a loop that is ITSELF
                # inside the `finally` leaves that loop and not the `try`, so
                # it is not what the warning is about.
                walk(stmt, True, 0)
            return
        if kind in ("FunctionDef", "AsyncFunctionDef", "Lambda", "ClassDef"):
            # A `def` inside a `finally` is a DEFINITION, not a jump out of
            # it, and its `return` belongs to itself -- so its body is walked
            # AFRESH rather than skipped. Skipping it meant a `finally` inside
            # any function was never looked at, which is where every one of
            # these actually appears.
            for stmt in node.get("body") or []:
                walk(stmt, False, 0)
            return
        if kind in ("For", "AsyncFor", "While"):
            for stmt in (node.get("body") or []) + (node.get("orelse") or []):
                walk(stmt, in_finally, depth + 1)
            return
        if in_finally:
            if kind == "Return":
                found.append("'return' in a 'finally' block")
            elif kind in ("Break", "Continue") and depth == 0:
                word = "break" if kind == "Break" else "continue"
                found.append("'" + word + "' in a 'finally' block")
        for child in node.children():
            walk(child, in_finally, depth)

    body = tree.get("body")
    if not isinstance(body, list):
        # `eval` MODE HAS NO STATEMENTS -- its `body` is one expression, and
        # an expression cannot contain a `finally` to warn about.
        return found
    for stmt in body:
        walk(stmt, False, 0)
    return found


def _as_syntax_error(exc, filename):
    """The exception a program catches, from the one the parser raised.

    INDENTATION IS ITS OWN CLASS all the way through -- `_pylex.IndentError`
    and `_pyparse.IndentParseError` exist so that this can answer
    `IndentationError`, which is what `except IndentationError:` around a
    `compile()` is written to catch.
    """
    if isinstance(exc, (IndentError, IndentParseError)):
        made = IndentationError(exc.msg)
    else:
        made = SyntaxError(exc.msg)
    made.filename = filename
    made.lineno = exc.line
    made.offset = exc.col + 1
    made.msg = exc.msg
    return made


def compile(source, filename="<string>", mode="exec", flags=0,
            dont_inherit=False, optimize=-1, _feature_version=-1):
    """Parse and check `source`, and answer a `code` object.

    THE ARGUMENTS ARE CPYTHON'S, including the ones that do nothing here:
    a program passes `flags` and `dont_inherit` positionally, and a signature
    that refused them would reject calls that are perfectly ordinary.
    """
    if mode not in _MODES:
        raise ValueError("compile() mode must be 'exec', 'eval' or 'single'")
    if isinstance(source, bytes):
        source = source.decode("utf-8")
    if isinstance(source, code):
        return source
    try:
        tree = parse(source, "eval" if mode == "eval" else "exec")
        validate(tree, mode)
    except (LexError, ParseError) as exc:
        raise _as_syntax_error(exc, filename)
    for message in _finally_warnings(tree):
        # AT THE CALL SITE, which is what `catch_warnings(record=True)` around
        # this call sees -- `stacklevel` is CPython's for the same reason.
        warnings.warn(message, SyntaxWarning, stacklevel=2)
    name = "<module>" if mode == "exec" else "<" + mode + ">"
    return code(tree, filename, name, mode)
