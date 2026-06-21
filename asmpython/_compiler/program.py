"""Whole-program loader: discover, parse, and merge a project's modules.

asmpython compiles a *program*, not a single file. When the entry module
imports classes/functions from sibling project files (`from .errors import
SourcePos`, `from . import ast_nodes as A`), those definitions must be visible
to sema and codegen — otherwise constructing `SourcePos(...)` or calling an
inherited method fails because the class lives in another file.

`load_program` walks the import graph starting at the entry file, parses every
reachable *project* module, and merges their top-level functions and classes
into one `Module`. The result is a single compilation unit the existing
sema/codegen pipeline handles unchanged. Third-party / stdlib imports
(`math`, `os`, things outside the project root) are left alone — they're
resolved through the FFI registry as before.

This is "whole-program compilation": no per-file `.o` linking, no cross-file
symbol ABI — every project module's code ends up in one `.asm`.
"""

from __future__ import annotations

from pathlib import Path

from .lexer import Lexer
from .parser import Parser
from . import ast_nodes as A


# Names that are always resolvable in any module, so an imported value's
# initializer may reference them and still be safely materialized. Compiler
# builtins + the handful of constant literals the language provides.
_ALWAYS_AVAILABLE: frozenset[str] = frozenset({
    "print", "len", "int", "float", "str", "input", "list", "dict", "set",
    "frozenset", "sum", "min", "max", "abs", "sorted", "reversed", "any",
    "all", "ord", "chr", "repr", "type", "id", "range", "isinstance",
    "getattr", "hasattr", "True", "False", "None",
    "enumerate", "zip", "map", "filter", "vars", "dir", "iter", "next",
    "open", "round", "divmod", "pow", "hash", "bool", "bytes", "bytearray",
    "tuple", "object", "super", "staticmethod", "classmethod", "property",
    "NotImplemented", "Ellipsis",
})


def _flatten_targets(targets: list, out: set[str]) -> None:
    """Collect every name bound by a (possibly nested) unpack target list,
    e.g. `["a", ["b", "c"]]` -> {"a", "b", "c"}. Mirrors sema's
    `_flat_target_names` for the subset program.py needs."""
    for t in targets:
        if isinstance(t, str):
            out.add(t)
        elif isinstance(t, list):
            _flatten_targets(t, out)


def _free_names(node: object, out: set[str]) -> None:
    """Collect the bare names an expression references: `Name` lookups and
    `Call`/`MethodCall` callee names. Used to decide whether a value-import's
    initializer can be safely materialized (every name it needs must already
    be available). Attribute names and string literals are not free
    variables, so they're skipped. Explicit per-node-type walk over every
    expression shape (no statement shapes: every call site passes a single
    expression, e.g. an import initializer or an `if`/assert test).
    """
    if isinstance(node, A.Name):
        out.add(node.name)
        return
    if isinstance(node, A.Call):
        out.add(node.func)
        for a in node.args:
            _free_names(a, out)
        for _kw, val in node.kwargs:
            _free_names(val, out)
        return
    if isinstance(node, A.MethodCall):
        # `obj.method(...)`: the receiver and args are sub-expressions; the
        # method name itself is an attribute, not a free variable.
        _free_names(node.obj, out)
        for a in node.args:
            _free_names(a, out)
        for _kw, val in node.kwargs:
            _free_names(val, out)
        return
    if isinstance(node, A.Attr):
        # `obj.name`: only the object is a free reference.
        _free_names(node.obj, out)
        return
    if isinstance(node, (A.Comprehension, A.DictComprehension)):
        # `[elt for a, b in iter if cond]`: `var`/`targets` are loop-bound
        # names, not free references — collect names from the rest of the
        # node (elt/key/value/iter/cond/extra_for_*) and drop the bound
        # ones, so e.g. `{fwd for fwd, _rfl in DUNDER_BINOP.values()}`
        # reports only `DUNDER_BINOP` as free, not `fwd`/`_rfl`.
        bound: set[str] = set()
        if node.var:
            bound.add(node.var)
        _flatten_targets(node.targets, bound)
        for t in node.extra_for_vars:
            if t:
                bound.add(t)
        for t in node.extra_for_targets:
            _flatten_targets(t, bound)
        inner: set[str] = set()
        if isinstance(node, A.Comprehension):
            _free_names(node.elt, inner)
        else:
            _free_names(node.key, inner)
            _free_names(node.value, inner)
        _free_names(node.iter, inner)
        if node.cond is not None:
            _free_names(node.cond, inner)
        for ei in node.extra_for_iters:
            _free_names(ei, inner)
        for ec in node.extra_for_conds:
            if ec is not None:
                _free_names(ec, inner)
        out |= inner - bound
        return
    if isinstance(node, A.BinOp):
        _free_names(node.left, out)
        _free_names(node.right, out)
        return
    if isinstance(node, A.UnaryOp):
        _free_names(node.operand, out)
        return
    if isinstance(node, A.Compare):
        for o in node.operands:
            _free_names(o, out)
        return
    if isinstance(node, A.BoolOp):
        _free_names(node.left, out)
        _free_names(node.right, out)
        return
    if isinstance(node, A.IfExp):
        _free_names(node.test, out)
        _free_names(node.body, out)
        _free_names(node.orelse, out)
        return
    if isinstance(node, A.NamedExpr):
        out.add(node.target)
        _free_names(node.value, out)
        return
    if isinstance(node, A.ListLit):
        for el in node.elems:
            _free_names(el, out)
        return
    if isinstance(node, A.Subscript):
        _free_names(node.obj, out)
        if isinstance(node.index, A.Slice):
            if node.index.start is not None:
                _free_names(node.index.start, out)
            if node.index.stop is not None:
                _free_names(node.index.stop, out)
            if node.index.step is not None:
                _free_names(node.index.step, out)
        else:
            _free_names(node.index, out)
        return
    if isinstance(node, A.FString):
        for seg in node.segments:
            _free_names(seg, out)
        return
    if isinstance(node, A.DictLit):
        for k in node.keys:
            if k is not None:
                _free_names(k, out)
        for v in node.values:
            _free_names(v, out)
        return
    if isinstance(node, A.TupleLit):
        for el in node.elems:
            _free_names(el, out)
        return
    if isinstance(node, A.SetLit):
        for el in node.elems:
            _free_names(el, out)
        return
    if isinstance(node, A.Starred):
        _free_names(node.value, out)
        return
    if isinstance(node, A.Lambda):
        # The lambda's own params shadow any same-named outer reference, so
        # exclude them from what its body contributes as free.
        if node.body is not None:
            inner: set[str] = set()
            _free_names(node.body, inner)
            out |= inner - set(node.params)
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            _free_names(item, out)
        return


def _resolve_relative(importer: Path, level: int, module: str, root: Path) -> Path | None:
    """Resolve a relative import (`from ..pkg.mod import x`) to a file path.

    `level` is the dot count: 1 = same package dir, 2 = parent, etc. `module`
    is the dotted remainder (may be ""). Returns the `.py` file or None if it
    doesn't resolve to a project file (or escapes the project root).
    """
    base = importer.parent
    # Each extra dot beyond the first walks up one directory.
    for _ in range(level - 1):
        base = base.parent
    target = base
    if module:
        for part in module.split("."):
            target = target / part
    candidate = target.with_suffix(".py") if target.suffix == "" else target
    py = candidate if candidate.suffix == ".py" else Path(str(candidate) + ".py")
    if py.is_file() and (_within(py, root) or _is_within_stdlib(py)):
        return py
    # `from . import submod` — module is "", the imported *name* is the module.
    return None


def _resolve_absolute(module: str, root: Path) -> Path | None:
    """Resolve an absolute dotted import (`asmpython._compiler.errors`) to a
    project file under `root`'s parent, or None if it's not a project module."""
    parts = module.split(".")
    # The project root dir is named after the top package (e.g. `asmpython`),
    # so an import `asmpython.x.y` maps to <root>/x/y.py with root.name == parts[0].
    if not parts:
        return None
    if parts[0] != root.name:
        return None
    target = root
    for part in parts[1:]:
        target = target / part
    py = Path(str(target) + ".py")
    if py.is_file():
        return py
    # A package import (`asmpython._compiler`) -> its __init__.py.
    init = target / "__init__.py"
    if init.is_file():
        return init
    return None


# Stdlib modules implemented as asmpython *source* (they define real classes,
# e.g. `pathlib.Path`, `argparse.ArgumentParser`) rather than FFI-only
# `Func`/`Const` bindings (`os`, `sys`, `math`, ...). `import pathlib` /
# `from argparse import ArgumentParser` resolve to these bundled files and get
# merged like project modules, so their classes are fully type-checked.
_BUNDLED_SOURCE_STDLIB: frozenset[str] = frozenset({
    "pathlib", "argparse",
    "string", "collections", "itertools", "functools", "json",
    "ospath", "re", "io", "operator", "copy",
    "enum", "abc", "contextlib",
    "struct", "hashlib", "heapq", "bisect", "statistics",
    "typing", "dataclasses", "textwrap", "csv", "uuid", "base64",
    "fractions", "decimal", "datetime", "warnings", "urllib",
    "urllibparse", "pprint", "platform", "glob", "threading",
    "logging", "secrets", "shutil", "traceback", "inspect",
    "fnmatch", "queue", "weakref", "gc",
    "configparser", "locale",
    "subprocess", "atexit", "tempfile", "types", "signal",
    "html", "keyword", "shlex", "calendar", "difflib",
    "ipaddress", "numbers", "hmac", "timeit", "getpass",
    "gzip", "zipfile", "pickle",
    "colorsys", "cmath", "sched",
    "lumen", "_font8x8",
    # 2.0.0 stdlib additions (batch 1)
    "errno", "stat", "getopt", "binascii", "array", "unittest",
    "urllib_request", "urllib_error",
    # 2.0.0 stdlib additions (batch 2)
    "token", "tokenize", "shelve", "codecs", "fileinput", "linecache",
    "mimetypes", "socketserver", "smtplib", "ftplib", "poplib", "imaplib",
    "http_server", "xml_etree", "html_parser", "tarfile",
    "concurrent_futures", "profile", "pstats", "tracemalloc",
    "uu", "quopri", "zlib", "ssl", "sqlite3", "asyncio", "importlib",
})

# Dotted module names that map to a differently-named file in stdlib/.
_BUNDLED_DOTTED: dict[str, str] = {
    "os.path":                   "ospath",
    "urllib.parse":              "urllibparse",
    "urllib.request":            "urllib_request",
    "urllib.error":              "urllib_error",
    "http.server":               "http_server",
    "xml.etree.ElementTree":     "xml_etree",
    "xml.etree":                 "xml_etree",
    "html.parser":               "html_parser",
    "concurrent.futures":        "concurrent_futures",
}


def _stdlib_dir() -> Path:
    # Computed inline rather than cached in a module-level constant: a
    # top-level `_STDLIB_DIR: Path = Path(__file__)...` initializer isn't a
    # trivially-hoistable constant when this file is itself merged into a
    # self-compiling program (whole-program compilation can't see `__file__`
    # as a resolvable free name for a global), which broke selfhost with an
    # "undefined variable '_STDLIB_DIR'" error. A local computation inside
    # each function that needs it has no such requirement.
    return Path(__file__).resolve().parent.parent / "stdlib"


def _is_within_stdlib(path: Path) -> bool:
    """True if `path` lives under asmpython's bundled stdlib/ directory.

    Bundled-stdlib files are always trusted (same as `_resolve_bundled_stdlib`
    itself not checking project-root containment), so their own internal
    relative imports (a package like `stdlib/gui/` importing its own
    `._canvas` submodule) must resolve independently of the user project's
    root — the normal `_within(path, root)` check would always fail for them
    since they live outside the user's project entirely.
    """
    return _within(path, _stdlib_dir())


def _resolve_bundled_stdlib(module: str) -> Path | None:
    stem = _BUNDLED_DOTTED.get(module)
    stdlib_dir = _stdlib_dir()
    if stem is None:
        top = module.split(".")[0]
        if top not in _BUNDLED_SOURCE_STDLIB:
            return None
        rest = module.split(".")[1:]
        if rest:
            # A genuine dotted submodule path inside a bundled *package*
            # (`lumen.framebuffer` -> stdlib/lumen/framebuffer.py), distinct
            # from `_BUNDLED_DOTTED`'s flat-file aliases (`os.path` ->
            # stdlib/ospath.py, a different file entirely, not a real
            # `os/path.py` submodule). Only a real package directory (not a
            # flat `<top>.py` module, e.g. `urllib`) can have submodules;
            # `module` naming more path segments than actually exist (e.g.
            # `urllib.parse.quote`, where `quote` is a name *inside*
            # urllibparse.py, not its own file) must resolve to nothing here
            # rather than falling back to the unrelated `<top>.py` file.
            if not (stdlib_dir / top).is_dir():
                return None
            sub = stdlib_dir / top
            for part in rest:
                sub = sub / part
            sub_py = Path(str(sub) + ".py")
            if sub_py.is_file():
                return sub_py
            sub_init = sub / "__init__.py"
            if sub_init.is_file():
                return sub_init
            return None
        stem = top
    py = stdlib_dir / f"{stem}.py"
    if py.is_file():
        return py
    # A bundled stdlib module may be a package directory (stdlib/<stem>/
    # __init__.py) instead of a flat file, e.g. for a module large enough to
    # split across several internal submodules.
    pkg_init = stdlib_dir / stem / "__init__.py"
    return pkg_init if pkg_init.is_file() else None


def _resolve_user_module(module: str, importer: Path, root: Path) -> Path | None:
    """Look for a user-written sibling module (`import utils`, `from utils import X`).

    Searches the importer's own directory for `module.py` (or `module/__init__.py`),
    accepting the result only if it lives inside the project root so we never
    accidentally swallow a stdlib import that happens to share a filename.

    This handles flat user projects where `main.py` and `utils.py` live together
    without a package `__init__.py` structure.
    """
    parts = module.split(".")
    if not parts:
        return None
    target = importer.parent
    for part in parts:
        target = target / part
    py = Path(str(target) + ".py")
    if py.is_file() and _within(py, root):
        return py
    init = target / "__init__.py"
    if init.is_file() and _within(init, root):
        return init
    return None


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _collect_import_stmts(module: A.Module) -> list:
    """Every Import / FromImport in the module — including those nested inside
    function and method bodies and control-flow blocks."""
    found: list = []

    def walk(stmts) -> None:
        for s in stmts or []:
            if isinstance(s, (A.Import, A.FromImport)):
                found.append(s)
            elif isinstance(s, A.FuncDef):
                walk(s.body)
            elif isinstance(s, A.If):
                walk(s.then)
                walk(s.orelse)
            elif isinstance(s, (A.While, A.For)):
                walk(s.body)
            elif isinstance(s, A.Try):
                walk(s.body)
                walk(getattr(s, "handler", None))
                for _types, _bind, hbody in getattr(s, "extra_handlers", []) or []:
                    walk(hbody)
                walk(getattr(s, "else_body", None))
                walk(getattr(s, "finally_body", None))

    walk(module.body)
    for f in module.funcs:
        walk(f.body)
    for c in module.classes:
        for m in c.methods:
            walk(m.body)
    return found


def _rename_call_targets(stmts: list, renames: dict[str, str]) -> None:
    """Recursively rewrite every `A.Call.func` and `A.ClosureBind.func_name`
    in a statement list (and every expression nested inside it) per
    `renames`.

    `ClosureBind.func_name` has to move in lockstep with the call-site rename:
    sema keys its captured-free-var-type table (`_fv_types`) by this same
    string (see `_prescan_fv_types`/`scan_closurebinds`), and codegen uses it
    both as the local variable the closure object is bound to and as the
    label baked into the closure's function pointer. Leaving it as the old
    name after renaming the `FuncDef` left `_fv_types` keyed under a name sema
    never looks up again, silently dropping the captured types (free vars
    defaulted back to `int`).

    Explicit per-node-type walk over every statement/expression shape (not a
    nested function's own body — that's a separate top-level FuncDef already
    walked on its own by this function's callers).
    """
    for s in stmts:
        if isinstance(s, A.Assign):
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.AugAssign):
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.TupleAssign):
            for t in s.targets:
                if isinstance(t, (A.Subscript, A.Attr)):
                    _rename_call_targets_expr(t, renames)
            for v in s.values:
                _rename_call_targets_expr(v, renames)
        elif isinstance(s, A.MultiAssign):
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.Return):
            if s.value is not None:
                _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.If):
            _rename_call_targets_expr(s.test, renames)
            _rename_call_targets(s.then, renames)
            _rename_call_targets(s.orelse, renames)
        elif isinstance(s, A.While):
            _rename_call_targets_expr(s.test, renames)
            _rename_call_targets(s.body, renames)
            _rename_call_targets(s.orelse, renames)
        elif isinstance(s, A.For):
            for a in s.range_args:
                _rename_call_targets_expr(a, renames)
            if s.iter is not None:
                _rename_call_targets_expr(s.iter, renames)
            _rename_call_targets(s.body, renames)
            _rename_call_targets(s.orelse, renames)
        elif isinstance(s, A.ExprStmt):
            _rename_call_targets_expr(s.expr, renames)
        elif isinstance(s, A.AttrAssign):
            _rename_call_targets_expr(s.obj, renames)
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.IndexAssign):
            _rename_call_targets_expr(s.target, renames)
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.With):
            _rename_call_targets_expr(s.expr, renames)
            _rename_call_targets(s.body, renames)
        elif isinstance(s, A.Try):
            _rename_call_targets(s.body, renames)
            _rename_call_targets(s.handler, renames)
            for _types, _bind, hbody in s.extra_handlers:
                _rename_call_targets(hbody, renames)
            _rename_call_targets(s.else_body, renames)
            _rename_call_targets(s.finally_body, renames)
        elif isinstance(s, A.Raise):
            if s.value is not None:
                _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.Del):
            _rename_call_targets_expr(s.target, renames)
        elif isinstance(s, A.YieldStmt):
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.Match):
            _rename_call_targets_expr(s.subject, renames)
            for _pattern, guard, body in s.cases:
                if guard is not None:
                    _rename_call_targets_expr(guard, renames)
                _rename_call_targets(body, renames)
        elif isinstance(s, A.ClosureBind) and s.func_name in renames:
            s.func_name = renames[s.func_name]


def _rename_call_targets_expr(e, renames: dict[str, str]) -> None:
    if isinstance(e, A.Call):
        if e.func in renames:
            e.func = renames[e.func]
        for a in e.args:
            _rename_call_targets_expr(a, renames)
        for _kn, kv in e.kwargs:
            _rename_call_targets_expr(kv, renames)
    elif isinstance(e, A.MethodCall):
        _rename_call_targets_expr(e.obj, renames)
        for a in e.args:
            _rename_call_targets_expr(a, renames)
        for _kn, kv in e.kwargs:
            _rename_call_targets_expr(kv, renames)
    elif isinstance(e, A.BinOp):
        _rename_call_targets_expr(e.left, renames)
        _rename_call_targets_expr(e.right, renames)
    elif isinstance(e, A.UnaryOp):
        _rename_call_targets_expr(e.operand, renames)
    elif isinstance(e, A.Compare):
        for o in e.operands:
            _rename_call_targets_expr(o, renames)
    elif isinstance(e, A.BoolOp):
        _rename_call_targets_expr(e.left, renames)
        _rename_call_targets_expr(e.right, renames)
    elif isinstance(e, A.IfExp):
        _rename_call_targets_expr(e.test, renames)
        _rename_call_targets_expr(e.body, renames)
        _rename_call_targets_expr(e.orelse, renames)
    elif isinstance(e, A.NamedExpr):
        _rename_call_targets_expr(e.value, renames)
    elif isinstance(e, A.ListLit):
        for el in e.elems:
            _rename_call_targets_expr(el, renames)
    elif isinstance(e, A.Subscript):
        _rename_call_targets_expr(e.obj, renames)
        if isinstance(e.index, A.Slice):
            if e.index.start is not None:
                _rename_call_targets_expr(e.index.start, renames)
            if e.index.stop is not None:
                _rename_call_targets_expr(e.index.stop, renames)
            if e.index.step is not None:
                _rename_call_targets_expr(e.index.step, renames)
        else:
            _rename_call_targets_expr(e.index, renames)
    elif isinstance(e, A.Attr):
        _rename_call_targets_expr(e.obj, renames)
    elif isinstance(e, A.FString):
        for seg in e.segments:
            _rename_call_targets_expr(seg, renames)
    elif isinstance(e, A.DictLit):
        for k in e.keys:
            if k is not None:
                _rename_call_targets_expr(k, renames)
        for v in e.values:
            _rename_call_targets_expr(v, renames)
    elif isinstance(e, A.TupleLit):
        for el in e.elems:
            _rename_call_targets_expr(el, renames)
    elif isinstance(e, A.SetLit):
        for el in e.elems:
            _rename_call_targets_expr(el, renames)
    elif isinstance(e, A.Starred):
        _rename_call_targets_expr(e.value, renames)
    elif isinstance(e, A.Comprehension):
        _rename_call_targets_expr(e.elt, renames)
        _rename_call_targets_expr(e.iter, renames)
        if e.cond is not None:
            _rename_call_targets_expr(e.cond, renames)
        for ei in e.extra_for_iters:
            _rename_call_targets_expr(ei, renames)
        for ec in e.extra_for_conds:
            if ec is not None:
                _rename_call_targets_expr(ec, renames)
    elif isinstance(e, A.DictComprehension):
        _rename_call_targets_expr(e.key, renames)
        _rename_call_targets_expr(e.value, renames)
        _rename_call_targets_expr(e.iter, renames)
        if e.cond is not None:
            _rename_call_targets_expr(e.cond, renames)
    elif isinstance(e, A.Lambda):
        if e.body is not None:
            _rename_call_targets_expr(e.body, renames)


def _dedupe_lifted_funcs(module: "A.Module", taken_names: set[str]) -> None:
    """Rename any of `module`'s lifted (nested-function-turned-top-level)
    funcs whose bare name collides with `taken_names`, fixing up every call
    site within `module` to match.

    `taken_names` must NOT include any of `module`'s own funcs (lifted or
    not) — only names already claimed by *other* modules / by `module`'s
    surrounding context before this call. Passing `module`'s own names in
    would make every lifted func look like it collides with itself.

    Nested `def`s are lifted to module scope under their original source name
    (see `Parser._parse_stmt`'s `def` branch). That name is just the variable
    the closure happens to be bound to in its *own* file — it carries no
    cross-file meaning, so two unrelated files both nesting a helper named
    `walk` must not collide once whole-program merge puts every module's
    functions into one flat `entry.funcs` list. Plain (non-lifted) top-level
    functions are intentionally left alone: `load_program`'s merge treats a
    same-named top-level function in a later module as already-provided by an
    earlier one (first definition wins), which is the desired behavior there.
    """
    renames: dict[str, str] = {}
    local_names: set[str] = set()
    for f in module.funcs:
        if not getattr(f, "is_lifted", False):
            continue
        if f.name in taken_names or f.name in local_names:
            new_name = f.name
            n = 0
            while new_name in taken_names or new_name in local_names:
                n += 1
                new_name = f"{f.name}__lifted{n}"
            renames[f.name] = new_name
            f.name = new_name
        local_names.add(f.name)
    if renames:
        _rename_call_targets(module.body, renames)
        for f in module.funcs:
            _rename_call_targets(f.body, renames)
        for c in module.classes:
            for m in c.methods:
                _rename_call_targets(m.body, renames)


def _project_imports(module: A.Module, importer: Path, root: Path) -> list[Path]:
    """Every project `.py` file the module imports (relative or absolute),
    scanning top-level *and* nested (function-local) import statements."""
    out: list[Path] = []
    for stmt in _collect_import_stmts(module):
        if isinstance(stmt, A.FromImport):
            if stmt.level > 0:
                if stmt.module:
                    p = _resolve_fromimport_path(stmt, importer, root)
                    if p is not None:
                        out.append(p)
                else:
                    # `from . import a, b` — each name is either a sibling
                    # module (`a.py`) or a name (class/function) defined in the
                    # package's `__init__.py`. Pull in whichever exists.
                    base = importer.parent
                    for _ in range(stmt.level - 1):
                        base = base.parent
                    pkg_init = base / "__init__.py"
                    # Resolve by the *exported* name (`from . import ast_nodes
                    # as A` imports the module `ast_nodes`, not `A`), falling
                    # back to the bound name when there's no alias.
                    for orig in (stmt.orig_names or stmt.names):
                        py = base / f"{orig}.py"
                        if py.is_file() and _within(py, root):
                            out.append(py)
                        elif pkg_init.is_file() and _within(pkg_init, root):
                            # Name lives in the package __init__ (e.g.
                            # `from . import Func, Const`). Merge that init's
                            # definitions so the class is available.
                            out.append(pkg_init)
            elif stmt.module:
                # `from asmpython.pkg import X`: X may be a name defined in the
                # package's module/__init__, OR a *submodule*. Try each
                # imported name as a submodule FIRST: a name that resolves as
                # its own file (`from lumen import framebuffer` ->
                # stdlib/lumen/framebuffer.py) must NOT also pull in the
                # package's __init__.py — some bundled packages split
                # genuinely independent submodules with different link
                # requirements (lumen's __init__ needs SDL2; lumen.framebuffer
                # needs neither), and merging __init__ in regardless would
                # drag SDL2 into a program that only wants the bare-metal
                # framebuffer. Only fall back to resolving the base module
                # itself when at least one imported name *isn't* its own
                # submodule (so it must live in the package's __init__/be the
                # module itself, e.g. `from os.path import join`).
                resolved_as_submodule: set[str] = set()
                for orig in (stmt.orig_names or stmt.names):
                    sub = _resolve_absolute(f"{stmt.module}.{orig}", root)
                    if sub is None:
                        sub = _resolve_user_module(
                            f"{stmt.module}.{orig}", importer, root
                        )
                    if sub is None:
                        sub = _resolve_bundled_stdlib(f"{stmt.module}.{orig}")
                    if sub is not None:
                        out.append(sub)
                        resolved_as_submodule.add(orig)
                names_to_check = stmt.orig_names or stmt.names
                if any(n not in resolved_as_submodule for n in names_to_check):
                    p = _resolve_absolute(stmt.module, root)
                    if p is None:
                        p = _resolve_user_module(stmt.module, importer, root)
                    if p is None:
                        p = _resolve_bundled_stdlib(stmt.module)
                    if p is not None:
                        out.append(p)
        elif isinstance(stmt, A.Import):
            p = _resolve_absolute(stmt.module, root)
            if p is None:
                p = _resolve_user_module(stmt.module, importer, root)
            if p is None:
                p = _resolve_bundled_stdlib(stmt.module)
            if p is not None:
                out.append(p)
    return out


def _project_root(entry: Path) -> Path:
    """The top-level package directory: walk up while a parent has __init__.py
    so the whole `asmpython/` tree counts as one project."""
    root = entry.parent
    while (root.parent / "__init__.py").is_file() or (root / "__init__.py").is_file():
        if (root.parent / "__init__.py").is_file():
            root = root.parent
        else:
            break
    return root


def _toplevel_value_assigns(module: A.Module) -> dict[str, A.Stmt]:
    """Top-level `name = <expr>` (and annotated) assignments in a module, keyed
    by target name. These are the module's exported *values* — a sibling that
    does `from .mod import name` is referring to one of these. Only plain
    name-target assigns count (not attribute/subscript writes)."""
    out: dict[str, A.Stmt] = {}
    for s in module.body:
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            out[s.target] = s
    return out


def _rename_assign(stmt: A.Assign, new_target: str) -> A.Assign:
    """A shallow copy of an assignment with a different target name, so the
    same initializer can be bound under the local alias the importer used
    (`from .math import BINDINGS as _MATH_BINDINGS`)."""
    if stmt.target == new_target:
        return stmt
    return A.Assign(
        target=new_target,
        value=stmt.value,
        pos=stmt.pos,
        annot=getattr(stmt, "annot", None),
    )


def load_program(entry_src: str, entry_path: Path) -> A.Module:
    """Parse the entry module and every reachable project module, merging their
    top-level funcs, classes, AND imported value globals into the entry Module.
    Returns the merged unit.

    The entry module's own `body` (top-level statements) is the program's main
    code. Imported modules contribute their definitions (funcs/classes) and —
    when the importer pulls a *value* out of them via `from .mod import NAME` —
    that value's initializer assignment, prepended to the entry body so it runs
    (and is collected as a global) before the code that uses it. Other module-
    level side-effecting statements are still not run.
    """
    entry_path = entry_path.resolve()
    root = _project_root(entry_path)

    entry = Parser(Lexer(entry_src).tokenize()).parse()

    seen: list[str] = [str(entry_path)]
    # Names already defined so merges don't duplicate (first definition wins).
    _dedupe_lifted_funcs(entry, set())
    func_names = set()
    for f in entry.funcs:
        func_names.add(f.name)
    class_names = set()
    for c in entry.classes:
        class_names.add(c.name)

    # Per-module parsed AST + the top-level value assigns it exports, recorded
    # in discovery order so the materialization pass can resolve cross-module
    # value imports and order them leaves-first.
    parsed: dict[str, A.Module] = {str(entry_path): entry}
    discovery_order: list[str] = [str(entry_path)]

    queue = _project_imports(entry, entry_path, root)
    while queue:
        mod_path = queue.pop(0).resolve()
        mod_path_str = str(mod_path)
        if mod_path_str in seen:
            continue
        seen.append(mod_path_str)
        try:
            mod_src = mod_path.read_text(encoding="utf-8")
            mod = Parser(Lexer(mod_src).tokenize()).parse()
        except Exception:
            # A module we can't parse is skipped — it may be third-party-ish or
            # use constructs outside the subset; the importer still type-checks
            # leniently against the missing name.
            continue
        parsed[mod_path_str] = mod
        discovery_order.append(mod_path_str)
        _dedupe_lifted_funcs(mod, func_names)
        for f in mod.funcs:
            if f.name not in func_names:
                func_names.add(f.name)
                entry.funcs.append(f)
        for c in mod.classes:
            if c.name not in class_names:
                class_names.add(c.name)
                entry.classes.append(c)
        # Recurse into this module's own project imports.
        for p in _project_imports(mod, mod_path, root):
            if str(p.resolve()) not in seen:
                queue.append(p)

    _merge_import_bindings(entry, parsed, discovery_order)
    _materialize_value_imports(entry, parsed, discovery_order, root)
    return entry


def _simple_const_if_targets(stmt: "A.If", available: set[str]) -> set[str] | None:
    """If `stmt` is a top-level `if/elif/.../else` chain whose every branch
    consists solely of simple constant assigns (e.g. the platform-conditional
    `if sys.platform == "win32": SIGABRT: int = 22 else: SIGABRT: int = 6`
    pattern), return the set of every name assigned across all branches so the
    caller can hoist the whole `If` into the entry body as a program global.

    Returns None if the `If` doesn't fit this shape (left for the
    value-import / lenient-fallback machinery to handle as before).
    """
    free: set = set()
    _free_names(stmt.test, free)
    if not free <= available:
        return None
    targets: set[str] = set()
    for branch in (stmt.then, stmt.orelse):
        for s in branch or []:
            if isinstance(s, A.If):
                sub = _simple_const_if_targets(s, available)
                if sub is None:
                    return None
                targets |= sub
                continue
            if not (isinstance(s, A.Assign) and isinstance(s.target, str)):
                return None
            bfree: set = set()
            _free_names(s.value, bfree)
            if not bfree <= available:
                return None
            targets.add(s.target)
    return targets


def _merge_import_bindings(
    entry: A.Module, parsed: dict[str, A.Module], discovery_order: list[str]
) -> None:
    """Replay each merged module's import statements into the entry body.

    A merged module's top-level functions are checked against the *whole-program*
    global scope, but the names those functions rely on from the module's own
    imports (`from asmpython.stdlib import ospath` -> the name `ospath`) live
    only in that module's body, which isn't otherwise merged. Without this,
    `ospath.join(...)` inside a merged `find_package` sees `ospath` undefined.

    Collect the Import / FromImport statements from every *non-entry* merged
    module and prepend them to the entry body (deduped on their surface text)
    so sema binds those module names globally. The entry's own imports are left
    where they are.
    """
    seen_keys: set[str] = set()

    def key(stmt) -> str:
        if isinstance(stmt, A.Import):
            return "import:" + stmt.module
        return "from:" + str(stmt.level) + ":" + stmt.module + ":" + ",".join(stmt.names)

    # Names any merged module pulls in via a relative value import (`from ..
    # import __version__`, `from .sys import BINDINGS as _SYS_BINDINGS`).
    # `_materialize_value_imports` (which runs after this pass) places each
    # of these — and their own transitive dependencies — in dependency order
    # ahead of whatever needs them. If this pass also naively hoists the
    # *defining* module's own copy of the same name (its free names are
    # trivially satisfiable, e.g. a bare string literal has none), it can
    # land that definition in `entry.body` at a position later than a
    # dependent statement that got hoisted earlier in this same pass —
    # `_materialize_value_imports` then sees the name already present and
    # skips re-ordering it, leaving the dependent before its definition.
    # Skip hoisting any such name here so only the dependency-aware pass
    # ever places it.
    value_import_targets: set[str] = set()
    for mod_path in discovery_order:
        mod = parsed.get(mod_path)
        if mod is None:
            continue
        for stmt in mod.body:
            if isinstance(stmt, A.FromImport) and stmt.level > 0:
                for orig in stmt.orig_names or stmt.names:
                    value_import_targets.add(orig)

    extra: list = []
    # Names already bound at entry top-level, so a merged module's own global
    # doesn't shadow/duplicate one the entry (or an earlier merge) defined.
    available: set = set()
    for s in entry.body:
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            available.add(s.target)
    for f in entry.funcs:
        available.add(f.name)
    for c in entry.classes:
        available.add(c.name)
    available |= _ALWAYS_AVAILABLE
    for mod_path in discovery_order[1:]:
        mod = parsed.get(mod_path)
        if mod is None:
            continue
        for stmt in _collect_import_stmts(mod):
            k = key(stmt)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            extra.append(stmt)
            if isinstance(stmt, A.Import):
                # `import sys` binds `sys`; `import a.b.c` binds `a`. Once
                # replayed into the entry body this name is a valid global
                # reference, so platform-conditional constants below (e.g.
                # `if sys.platform == "win32": ...`) can depend on it.
                available.add(stmt.module.split(".")[0])
            elif isinstance(stmt, A.FromImport):
                # Track locally-bound names so subsequent constant assignments
                # in later (or same) modules don't shadow import-bound aliases.
                # Example: `from . import ast_nodes as A` must not be clobbered
                # by `re.py`'s `A: int = 256`.
                for _n in stmt.names:
                    available.add(_n)
        # A merged module's own top-level constant assignments (e.g.
        # `STDLIB_BINDINGS = {...}`) are used by its functions, so they must
        # become program globals too. Skip a constant whose initializer needs a
        # name we can't provide here (e.g. `STDLIB_BINDINGS = {... _MATH_BINDINGS
        # ...}`, which depends on a cross-module value import) — the dependency-
        # aware `_materialize_value_imports` pass handles those. First wins.
        for stmt in mod.body:
            if (
                isinstance(stmt, A.Assign)
                and isinstance(stmt.target, str)
                and stmt.target not in available
            ):
                if stmt.target in value_import_targets:
                    # Some merged module imports this exact name as a value
                    # (e.g. `from .. import __version__`); leave it for
                    # `_materialize_value_imports`, which places it ahead of
                    # every dependent rather than wherever this single-pass
                    # walk happens to be.
                    continue
                free: set = set()
                _free_names(stmt.value, free)
                if not free <= available:
                    continue
                available.add(stmt.target)
                extra.append(stmt)
            elif isinstance(stmt, A.If):
                # Platform-conditional top-level constants (e.g. signal.py's
                # `if sys.platform == "win32": SIGABRT: int = 22 else: SIGABRT: int = 6`).
                # Hoist the whole `If` so codegen evaluates the right branch.
                targets = _simple_const_if_targets(stmt, available)
                if targets is not None and not (targets & available):
                    available |= targets
                    extra.append(stmt)
    if extra:
        entry.body[:0] = extra


def _materialize_value_imports(
    entry: A.Module,
    parsed: dict[str, A.Module],
    discovery_order: list[str],
    root: Path,
) -> None:
    """Pull every cross-module *value* import into the entry body as a global,
    transitively.

    For each `from .other import NAME [as ALIAS]` the entry reaches, find
    `NAME`'s top-level assignment in `other` and prepend it to the entry body
    under the local alias, so codegen collects it as a global and runs its
    initializer. The resolution is *recursive*: if that initializer references
    names that `other` itself imported as values (e.g. `__init__`'s
    `STDLIB_BINDINGS = {... _MATH_BINDINGS ...}`, where `_MATH_BINDINGS` is
    imported from `math`), those dependencies are resolved and emitted first.
    The result is a dependency-ordered (post-order) list of globals.

    A value is materialized only when its initializer is fully resolvable:
    every free name is a builtin, a merged class/func, an entry global, an
    available value, or another resolvable value import. If any free name dead-
    ends in a source module's own imports/globals (e.g. `Const(value=_py_math.pi)`,
    which needs CPython's `math`), the whole chain is abandoned and the importer
    keeps its prior opaque binding — so the pass never turns a clean file broken.
    """
    # Names available without materialization: merged classes/funcs, the entry's
    # own module-level assigns, and the builtins the compiler always provides.
    base_available: set[str] = set()
    for f in entry.funcs:
        base_available.add(f.name)
    for c in entry.classes:
        base_available.add(c.name)
    for s in entry.body:
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            base_available.add(s.target)
    base_available |= _ALWAYS_AVAILABLE

    # Map each module's locally-imported value name -> (source module str path,
    # orig name), so a free name in an initializer can be chased to its definition.
    def value_import_edges(mod_path_str: str) -> dict[str, tuple[str, str]]:
        edges: dict[str, tuple[str, str]] = {}
        mod = parsed.get(mod_path_str)
        if mod is None:
            return edges
        mod_path = Path(mod_path_str)
        for stmt in mod.body:
            if not isinstance(stmt, A.FromImport):
                continue
            tgt = _resolve_fromimport_path(stmt, mod_path, root)
            if tgt is None:
                continue
            tgt_str = str(tgt)
            if tgt_str not in parsed:
                continue
            for local, orig in zip(stmt.names, stmt.orig_names or stmt.names):
                edges[local] = (tgt_str, orig)
        return edges

    materialized: dict[str, A.Assign] = {}  # local alias -> renamed assign
    prepend: list = []

    def resolve(local: str, mod_path_str: str, orig: str, stack: set) -> bool:
        """Ensure `local` is materialized as the value `orig` from `mod_path_str`.
        Returns True on success. `stack` guards against import cycles."""
        if local in base_available or local in materialized:
            return True
        cycle_key = mod_path_str + "\x00" + orig
        if cycle_key in stack:  # cycle — give up on this chain
            return False
        mod = parsed.get(mod_path_str)
        if mod is None:
            return False
        exports = _toplevel_value_assigns(mod)
        if orig not in exports:
            return False
        assign = exports[orig]
        free: set[str] = set()
        _free_names(assign.value, free)  # type: ignore[union-attr]
        edges = value_import_edges(mod_path_str)
        new_stack: set[str] = set()
        for s in stack:
            new_stack.add(s)
        new_stack.add(cycle_key)
        deps: list[A.Assign] = []
        for nm in free:
            if nm in base_available or nm in materialized:
                continue
            # Is it a value this module imported? Chase it.
            if nm in edges:
                src_str, src_orig = edges[nm]
                if resolve(nm, src_str, src_orig, new_stack):
                    continue
            # Or a value defined locally in this same module? Pull it too.
            if nm in exports:
                if resolve(nm, mod_path_str, nm, new_stack):
                    continue
            return False  # a free name we can't provide — abandon the chain
        # All deps satisfied (resolve() already appended them). Emit this one.
        renamed = _rename_assign(assign, local)
        materialized[local] = renamed
        prepend.append(renamed)
        return True

    # Resolve value imports for EVERY merged module, leaves-first — a merged
    # module's functions reference its own value imports (`sema.py`'s
    # STDLIB_BINDINGS) just as much as the entry's do. Aliases land as globals
    # in the flat program, so resolution is idempotent across modules.
    empty_stack: set[str] = set()
    for mod_path_str in reversed(discovery_order):
        edges: dict[str, tuple[str, str]] = value_import_edges(mod_path_str)
        for local, (src_str, orig) in edges.items():
            resolve(local, src_str, orig, empty_stack)

    if prepend:
        entry.body[:0] = prepend


def _resolve_fromimport_path(
    stmt: A.FromImport, importer: Path, root: Path
) -> Path | None:
    """The project file a `from ... import ...` statement resolves to, or None
    if it isn't a project module. Mirrors `_project_imports`' resolution but
    for a single statement and returning the module path (so we can read its
    exported assignments)."""
    if stmt.level > 0:
        if stmt.module:
            p = _resolve_relative(importer, stmt.level, stmt.module, root)
            if p is not None:
                return p
            # The module segment may name a *package* (`from .._stdlib import
            # X`): resolve to its `__init__.py`, where the value lives.
            base = importer.parent
            for _ in range(stmt.level - 1):
                base = base.parent
            pkg = base
            for part in stmt.module.split("."):
                pkg = pkg / part
            pkg_init = pkg / "__init__.py"
            if pkg_init.is_file() and (_within(pkg_init, root) or _is_within_stdlib(pkg_init)):
                return pkg_init
            return None
        # `from . import X`: X may be a sibling module or a name in __init__.
        # For value imports we care about the name living in the package
        # __init__, so resolve to that.
        base = importer.parent
        for _ in range(stmt.level - 1):
            base = base.parent
        pkg_init = base / "__init__.py"
        return pkg_init if pkg_init.is_file() else None
    if stmt.module:
        p = _resolve_absolute(stmt.module, root)
        if p is None:
            p = _resolve_user_module(stmt.module, importer, root)
        return p
    return None
