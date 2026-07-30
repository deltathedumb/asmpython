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

import dataclasses
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
    # Dynamic-loading builtins (see sema.py's _check_call): a module-level
    # `glfns = gl_import()` / `h = import_binary(path)` is exactly the kind
    # of value-import initializer this materialization pass needs to chase
    # (e.g. GLRenderer3D's methods referencing a sibling `glfns`), so the
    # builtin call name itself must count as always-resolvable the same way
    # `getattr`/`isinstance`/etc. already do above.
    "gl_import", "import_binary", "gl_resolve",
})


def _always_available_add(s: set) -> None:
    # Module-level frozenset constants are not materialized in gen1's merged
    # binary (same problem as _BUNDLED_SOURCE_STDLIB/_BUNDLED_DOTTED -- see
    # _bundled_dotted_stem). Call this helper instead of `s |= _ALWAYS_AVAILABLE`
    # so the names are added via individual .add() calls (which compile fine)
    # rather than a frozenset union that reads an uninitialized global (0).
    s.add("print")
    s.add("len")
    s.add("int")
    s.add("float")
    s.add("str")
    s.add("input")
    s.add("list")
    s.add("dict")
    s.add("set")
    s.add("frozenset")
    s.add("sum")
    s.add("min")
    s.add("max")
    s.add("abs")
    s.add("sorted")
    s.add("reversed")
    s.add("any")
    s.add("all")
    s.add("ord")
    s.add("chr")
    s.add("repr")
    s.add("type")
    s.add("id")
    s.add("range")
    s.add("isinstance")
    s.add("getattr")
    s.add("hasattr")
    s.add("True")
    s.add("False")
    s.add("None")
    s.add("enumerate")
    s.add("zip")
    s.add("map")
    s.add("filter")
    s.add("vars")
    s.add("dir")
    s.add("iter")
    s.add("next")
    s.add("open")
    s.add("round")
    s.add("divmod")
    s.add("pow")
    s.add("hash")
    s.add("bool")
    s.add("bytes")
    s.add("bytearray")
    s.add("tuple")
    s.add("object")
    s.add("super")
    s.add("staticmethod")
    s.add("classmethod")
    s.add("property")
    s.add("NotImplemented")
    s.add("Ellipsis")
    s.add("gl_import")
    s.add("import_binary")
    s.add("gl_resolve")


def _is_bundled_source_stdlib(name: str) -> int:
    # 1 if `name` is a bundled SOURCE stdlib module (Python source that gets
    # merged), 0 if it is an FFI-only or unknown module. FFI-only modules
    # (os, sys, math, socket, ...) must NOT be merged as source.
    #
    # _BUNDLED_SOURCE_STDLIB is the single source of truth. This was 105
    # hand-written `if name == "..."` branches listing exactly the same
    # modules, so adding one to the set without also adding it here (or the
    # reverse) silently changed how that module resolved.
    # NOTE: no `if cond: return val` (inline form) -- asmpython's parser
    # requires the body on a new indented line.
    if name in _BUNDLED_SOURCE_STDLIB:
        return 1
    return 0

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
    if node is None:
        return
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
    if isinstance(node, A.Comprehension):
        # `[elt for a, b in iter if cond]`: `var`/`targets` are loop-bound
        # names, not free references — collect names from the rest of the
        # node (elt/key/value/iter/cond/extra_for_*) and drop the bound
        # ones, so e.g. `{fwd for fwd, _rfl in DUNDER_BINOP.values()}`
        # reports only `DUNDER_BINOP` as free, not `fwd`/`_rfl`.
        _nc: A.Comprehension = node
        bound: set[str] = set()
        if _nc.var:
            bound.add(_nc.var)
        _flatten_targets(_nc.targets, bound)
        for t in _nc.extra_for_vars:
            if t:
                bound.add(t)
        for t in _nc.extra_for_targets:
            _flatten_targets(t, bound)
        inner: set[str] = set()
        _free_names(_nc.elt, inner)
        _free_names(_nc.iter, inner)
        if _nc.cond is not None:
            _free_names(_nc.cond, inner)
        for ei in _nc.extra_for_iters:
            _free_names(ei, inner)
        for ec in _nc.extra_for_conds:
            if ec is not None:
                _free_names(ec, inner)
        out |= inner - bound
        return
    if isinstance(node, A.DictComprehension):
        # Unlike A.Comprehension, DictComprehension has no extra_for_*
        # fields — it only supports a single `for` clause.
        _ndc: A.DictComprehension = node
        bound2: set[str] = set()
        if _ndc.var:
            bound2.add(_ndc.var)
        _flatten_targets(_ndc.targets, bound2)
        inner2: set[str] = set()
        _free_names(_ndc.key, inner2)
        _free_names(_ndc.value, inner2)
        _free_names(_ndc.iter, inner2)
        if _ndc.cond is not None:
            _free_names(_ndc.cond, inner2)
        out |= inner2 - bound2
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
    # Statement shapes: only reachable when _free_names is asked to scan a
    # whole method/function body (see _class_free_names below), not from
    # value-import initializer resolution (which only ever passes a single
    # expression). Each case collects the free names *referenced*, not
    # names the statement itself binds (locals, loop variables, ...) -- a
    # method's own locals shadowing an outer name isn't tracked precisely
    # here (this is a conservative "what might this method need from
    # outside itself" scan, not a real scope analysis), so a local that
    # happens to share a name with a module-level value just means that
    # value gets materialized even though the method doesn't actually need
    # it -- harmless (an unused extra global), unlike under-collecting.
    if isinstance(node, A.Assign):
        _free_names(node.value, out)
        return
    if isinstance(node, A.AugAssign):
        out.add(node.target)
        _free_names(node.value, out)
        return
    if isinstance(node, A.TupleAssign):
        for v in node.values:
            _free_names(v, out)
        return
    if isinstance(node, A.MultiAssign):
        _free_names(node.value, out)
        return
    if isinstance(node, A.AttrAssign):
        _free_names(node.obj, out)
        _free_names(node.value, out)
        return
    if isinstance(node, A.IndexAssign):
        _free_names(node.target, out)
        _free_names(node.value, out)
        return
    if isinstance(node, A.Return):
        if node.value is not None:
            _free_names(node.value, out)
        return
    if isinstance(node, A.ExprStmt):
        _free_names(node.expr, out)
        return
    if isinstance(node, A.If):
        _free_names(node.test, out)
        _free_names(node.then, out)
        _free_names(node.orelse, out)
        return
    if isinstance(node, A.While):
        _free_names(node.test, out)
        _free_names(node.body, out)
        _free_names(node.orelse, out)
        return
    if isinstance(node, A.For):
        if node.iter is not None:
            _free_names(node.iter, out)
        for a in node.range_args:
            _free_names(a, out)
        _free_names(node.body, out)
        _free_names(node.orelse, out)
        return
    if isinstance(node, A.With):
        _free_names(node.expr, out)
        _free_names(node.body, out)
        return
    if isinstance(node, A.Try):
        _free_names(node.body, out)
        _free_names(node.handler, out)
        for _types, _bind, body in node.extra_handlers:
            _free_names(body, out)
        _free_names(node.else_body, out)
        _free_names(node.finally_body, out)
        return
    if isinstance(node, A.Raise):
        if node.value is not None:
            _free_names(node.value, out)
        return
    if isinstance(node, A.YieldStmt):
        _free_names(node.value, out)
        return
    if isinstance(node, A.Del):
        _free_names(node.target, out)
        return
    if isinstance(node, A.Break):
        return
    if isinstance(node, A.Continue):
        return
    if isinstance(node, A.Pass):
        return
    if isinstance(node, A.Global):
        return
    if isinstance(node, A.Nonlocal):
        return
    if isinstance(node, list):
        for item in node:
            _free_names(item, out)
        return


def _class_free_names(cls) -> set[str]:
    """Every bare name a class's methods reference, across all method
    bodies. Used to auto-materialize module-level values a merged class
    depends on (see load_program's class-merge loop) -- e.g. GLRenderer3D's
    methods referencing a sibling module-level `glfns = gl_import()` that
    nothing ever explicitly `from module import glfns`s."""
    out: set[str] = set()
    methods: list = cls.methods
    for m in methods:
        if m.asm_body is not None:
            continue  # raw-NASM body, nothing to scan
        inner: set[str] = set()
        _free_names(m.body, inner)
        m_params: list = m.params
        out |= inner - set(m_params)
    return out


def _func_free_names(f) -> set[str]:
    """Every bare name a top-level function's body references, minus its own
    params. Mirrors `_class_free_names` but for plain functions -- used to
    auto-materialize module-level values a merged function depends on (see
    `func_origin` in load_program), the function-level counterpart of the
    class/method case documented on `_class_free_names`."""
    if f.asm_body is not None:
        return set()
    inner: set[str] = set()
    _free_names(f.body, inner)
    f_params: list = f.params
    return inner - set(f_params)


def _resolve_relative(importer: Path, level: int, module: str, root: Path) -> Path | None:
    """Resolve a relative import to a module file or package initializer.

    `level` is the dot count: 1 = same package dir, 2 = parent, etc. `module`
    is the dotted remainder (may be ""). Both `module.py` and
    `module/__init__.py` are valid Python import targets.
    """
    base = importer.parent
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
    init = target / "__init__.py"
    if init.is_file() and (_within(init, root) or _is_within_stdlib(init)):
        return init
    # `from . import submod` — module is "", the imported *name* is resolved
    # separately by `_project_imports` because each imported name may be a
    # distinct sibling module.
    return None


def _resolve_absolute(module: str, root: Path) -> Path | None:
    """Resolve an absolute dotted import inside a project's source root.

    `root` may be either a package directory itself (`.../asmpython`) or a
    workspace/repository directory containing multiple top-level packages
    (`.../somnia`, `.../tests`, and so on). Only files contained by `root`
    are accepted, so normal stdlib and third-party imports remain external.
    """
    parts = module.split(".")
    if not parts:
        return None

    if root.name == parts[0]:
        target = root
    else:
        target = root / parts[0]
    for part in parts[1:]:
        target = target / part

    py = Path(str(target) + ".py")
    if py.is_file() and _within(py, root):
        return py
    init = target / "__init__.py"
    if init.is_file() and _within(init, root):
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
    # 3.14 stdlib additions (batch 1)
    "errno", "stat", "getopt", "binascii", "array", "unittest",
    "urllib_request", "urllib_error",
    # 3.14 stdlib additions (batch 2)
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
    #
    # In gen0 (CPython), __file__ is this module (program.py inside _compiler/),
    # so .parent = _compiler/, and two .parent calls reach the package root.
    # In gen1 (self-hosted binary), __file__ is baked in as the entry source
    # file (asmpython/__main__.py), so .parent = asmpython/ = the package root
    # already — stdlib is one level down, not two.
    # Distinguish the two cases by the last path component name.
    this_file: Path = Path(__file__).resolve()
    par: Path = this_file.parent
    if par.name == "_compiler":
        return par.parent / "stdlib"
    return par / "stdlib"


def _backends_dir() -> Path:
    # See _stdlib_dir's docstring for why this is computed inline rather
    # than cached in a module-level constant. Same gen0/gen1 duality applies:
    # same directory-name heuristic.
    this_file: Path = Path(__file__).resolve()
    par: Path = this_file.parent
    if par.name == "_compiler":
        return par.parent / "_backends"
    return par / "_backends"


def _is_cpython_only_backend(path: Path) -> bool:
    """True if `path` lives under asmpython/_backends/ -- the vendored
    x86-64 IR backend plugin (multiprocessing, importlib plugin loading,
    NASM/gcc subprocess invocation) that driver.py's --backend x86-64 flag
    loads at runtime under a real CPython host. It's never meant to be
    self-host-compiled: it depends on CPython-only machinery (a real bytes
    type, ProcessPoolExecutor, dynamic import by string) self-hosted
    asmpython doesn't have and isn't trying to grow just to compile its own
    build tooling. driver.py's import of it is function-local specifically
    so the self-hosted compiler only needs the legacy gcc/text-asm path;
    excluding it here keeps the bundler from discovering its whole
    transitive closure (regalloc.py, elf.py, coff.py, ...) and dragging it
    into a self-host build that was never going to run it anyway."""
    return _within(path, _backends_dir())


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


def _bundled_dotted_stem(module: str) -> str | None:
    # Inline mapping so gen1 can resolve it without a module-level dict global.
    # Module-level dict/frozenset constants are NOT materialized when program.py
    # is merged into a self-hosted binary (they live in program.py's own body,
    # which _materialize_value_imports skips unless explicitly imported), so any
    # reference to _BUNDLED_DOTTED or _BUNDLED_SOURCE_STDLIB in the compiled
    # binary evaluates to 0 (uninitialized). Inlining as if/elif chains avoids
    # the global lookup entirely.
    if module == "os.path":
        return "ospath"
    if module == "urllib.parse":
        return "urllibparse"
    if module == "urllib.request":
        return "urllib_request"
    if module == "urllib.error":
        return "urllib_error"
    if module == "http.server":
        return "http_server"
    if module == "xml.etree.ElementTree":
        return "xml_etree"
    if module == "xml.etree":
        return "xml_etree"
    if module == "html.parser":
        return "html_parser"
    if module == "concurrent.futures":
        return "concurrent_futures"
    return None


def _resolve_bundled_stdlib(module: str) -> Path | None:
    # `import asmpython.stdlib.assembly as assembly` (the fully-qualified form,
    # mirroring how the bundled stdlib is laid out on disk) names the same
    # file as plain `import assembly`, but every lookup below keys off the bare
    # stdlib-relative name. Without this strip, the qualified form silently
    # resolves to None here -- not a hard error, since an unresolved module
    # name just leaves the import's bound name untyped ("any") -- so `assembly`
    # was never merged, none of its classes (Canvas, PixelBuffer, ...) existed
    # in self.classes, and every method call on a value built from it fell
    # back through opaque/"any" dispatch heuristics instead of real per-class
    # codegen. That's what let `canvas.update()` collide with dict.update()'s
    # name-based "any" dispatch and crash codegen with an IndexError several
    # layers removed from the actual cause.
    prefix = "asmpython.stdlib."
    if module.startswith(prefix):
        module = module[len(prefix):]
    # Use the inline helper instead of _BUNDLED_DOTTED (a module-level dict that
    # is not initialized in gen1's merged binary -- see _bundled_dotted_stem).
    stem = _bundled_dotted_stem(module)
    stdlib_dir = _stdlib_dir()
    if stem is None:
        top = module.split(".")[0]
        rest = module.split(".")[1:]
        if rest:
            # A genuine dotted submodule path inside a bundled *package*
            # (`assembly.x86` -> stdlib/assembly/x86.py), distinct
            # from `_bundled_dotted_stem`'s flat-file aliases (`os.path` ->
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
        # Guard: only try to merge modules we know are bundled SOURCE stdlib.
        # FFI modules (os, sys, math, socket, ...) also have .py files in
        # stdlib/ (their BINDINGS dicts), but they must NOT be merged as source
        # code — doing so triggers a cascade that pulls in stdlib/__init__.py
        # and breaks the compiled binary. Use the inline helper instead of
        # `top in _BUNDLED_SOURCE_STDLIB` (a module-level frozenset that is
        # always 0/uninitialized in gen1 -- see _bundled_dotted_stem comment).
        is_src = _is_bundled_source_stdlib(top)
        if not is_src:
            return None
        stem = top
    py = stdlib_dir / f"{stem}.py"
    py_ok = py.is_file()
    if py_ok:
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
            if isinstance(s, A.Import) or isinstance(s, A.FromImport):
                found.append(s)
            elif isinstance(s, A.FuncDef):
                walk(s.body)
            elif isinstance(s, A.If):
                walk(s.then)
                walk(s.orelse)
            elif isinstance(s, A.While):
                _sw: A.While = s
                walk(_sw.body)
            elif isinstance(s, A.For):
                _sf: A.For = s
                walk(_sf.body)
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
        elif isinstance(s, A.ConstDecl):
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.AugAssign):
            _rename_call_targets_expr(s.value, renames)
        elif isinstance(s, A.TupleAssign):
            for t in s.targets:
                if isinstance(t, A.Subscript) or isinstance(t, A.Attr):
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
    elif isinstance(e, A.TupleLit):
        # Same as ListLit just above -- this branch was missing entirely
        # before (a real, pre-existing gap unrelated to any one caller):
        # any call inside a tuple literal anywhere in this walk's reach
        # (not just `del a, b, c`'s TupleLit-wrapped multi-target) never
        # had its renamed Call.func target updated.
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


def _dedupe_lifted_funcs(module: A.Module, taken_names: set[str]) -> None:
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


def _resolved_import_path(stmt, importer: Path, root: Path) -> Path | None:
    """Resolve an ordinary import statement to a merged source module."""
    if isinstance(stmt, A.Import):
        result = _resolve_absolute(stmt.module, root)
        if result is None:
            result = _resolve_user_module(stmt.module, importer, root)
        if result is None:
            result = _resolve_bundled_stdlib(stmt.module)
        return result
    if isinstance(stmt, A.FromImport) and stmt.module:
        return _resolve_fromimport_path(stmt, importer, root)
    return None


def _relative_submodule_path(
    importer: Path, level: int, name: str, root: Path
) -> Path | None:
    """Resolve the module half of ``from . import module as alias``."""
    base = importer.parent
    for _ in range(level - 1):
        base = base.parent
    target = base / name
    py = Path(str(target) + ".py")
    if py.is_file() and (_within(py, root) or _is_within_stdlib(py)):
        return py.resolve()
    init = target / "__init__.py"
    if init.is_file() and (_within(init, root) or _is_within_stdlib(init)):
        return init.resolve()
    return None


def _class_import_bindings(
    module: A.Module,
    module_path: Path,
    root: Path,
    class_names_by_module: dict[str, dict[str, str]],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Return direct-class and module-alias bindings visible in ``module``.

    Values are already translated to the collision-safe internal names chosen
    for the target module.
    """
    direct: dict[str, str] = {}
    qualified: dict[str, dict[str, str]] = {}
    for stmt in module.body:
        if isinstance(stmt, A.Import):
            target = _resolved_import_path(stmt, module_path, root)
            if target is None:
                continue
            target_names = class_names_by_module.get(str(target.resolve()))
            if target_names is None:
                continue
            alias = stmt.alias
            if alias is None:
                alias = stmt.module.split(".")[0]
            qualified[alias] = target_names
            continue
        if not isinstance(stmt, A.FromImport):
            continue
        local_names: list = stmt.names
        original_names: list = stmt.orig_names if stmt.orig_names else stmt.names
        if stmt.level > 0 and not stmt.module:
            for local, original in zip(local_names, original_names):
                target = _relative_submodule_path(
                    module_path, stmt.level, original, root
                )
                if target is not None:
                    target_names = class_names_by_module.get(str(target))
                    if target_names is not None:
                        qualified[local] = target_names
                continue
        target = _resolved_import_path(stmt, module_path, root)
        if target is None:
            continue
        target_names = class_names_by_module.get(str(target.resolve()))
        if target_names is None:
            continue
        for local, original in zip(local_names, original_names):
            internal = target_names.get(original)
            if internal is not None:
                direct[local] = internal
    return direct, qualified


def _rewrite_class_references(
    value,
    bare_names: dict[str, str],
    qualified_names: dict[str, dict[str, str]],
) -> None:
    """Rewrite class-valued AST references after whole-program name isolation."""
    if isinstance(value, A.Call):
        replacement = bare_names.get(value.func)
        if replacement is not None:
            value.func = replacement
    elif isinstance(value, A.Name):
        replacement = bare_names.get(value.name)
        if replacement is not None:
            value.name = replacement
    elif (
        isinstance(value, A.MethodCall)
        or isinstance(value, A.Attr)
        or isinstance(value, A.AttrAssign)
    ):
        obj = value.obj
        if isinstance(obj, A.Name):
            module_classes = qualified_names.get(obj.name)
            if module_classes is not None:
                if isinstance(value, A.MethodCall):
                    replacement = module_classes.get(value.method)
                    if replacement is not None:
                        value.method = replacement
                elif isinstance(value, A.Attr):
                    replacement = module_classes.get(value.name)
                    if replacement is not None:
                        value.name = replacement
                else:
                    replacement = module_classes.get(value.name)
                    if replacement is not None:
                        value.name = replacement

    if dataclasses.is_dataclass(value):
        for data_field in dataclasses.fields(value):
            nested = getattr(value, data_field.name)
            _rewrite_class_references(nested, bare_names, qualified_names)
    elif isinstance(value, list) or isinstance(value, tuple):
        for nested in value:
            _rewrite_class_references(nested, bare_names, qualified_names)
    elif isinstance(value, dict):
        for nested in value.values():
            _rewrite_class_references(nested, bare_names, qualified_names)


def _merge_classes_with_module_identity(
    entry: A.Module,
    entry_path: Path,
    parsed: dict[str, A.Module],
    discovery_order: list[str],
    root: Path,
    class_origin: dict[str, str],
) -> None:
    """Merge classes without conflating equal leaf names from other modules.

    The native compiler has one flat symbol table, while Python gives every
    module its own global namespace.  Retain the first discovered spelling for
    source compatibility and give later cross-module collisions deterministic
    internal names.  Dotted bases and class-valued references are then rebound
    to those internal names before sema sees the flattened program.
    """
    occurrences: dict[str, list[str]] = {}
    for module_path in discovery_order:
        module = parsed.get(module_path)
        if module is None:
            continue
        local_seen: set[str] = set()
        for cls in module.classes:
            if cls.name in local_seen:
                continue
            local_seen.add(cls.name)
            paths = occurrences.get(cls.name)
            if paths is None:
                paths = []
                occurrences[cls.name] = paths
            paths.append(module_path)

    class_names_by_module: dict[str, dict[str, str]] = {}
    all_source_names: set[str] = set(occurrences)
    used_internal_names: set[str] = set()
    for module_index, module_path in enumerate(discovery_order):
        module = parsed.get(module_path)
        if module is None:
            continue
        local_map: dict[str, str] = {}
        for cls in module.classes:
            original = cls.name
            if original in local_map:
                continue
            internal = original
            paths = occurrences.get(original)
            if paths is not None and len(paths) > 1 and paths[0] != module_path:
                internal = (
                    original
                    + "__asmpython_module_"
                    + str(module_index)
                )
                suffix = 1
                while (
                    internal in all_source_names
                    or internal in used_internal_names
                ):
                    internal = (
                        original
                        + "__asmpython_module_"
                        + str(module_index)
                        + "_"
                        + str(suffix)
                    )
                    suffix += 1
            local_map[original] = internal
            used_internal_names.add(internal)
        class_names_by_module[module_path] = local_map

    # Resolve every parent while class names still carry their source spelling.
    for module_path in discovery_order:
        module = parsed.get(module_path)
        if module is None:
            continue
        module_path_obj = Path(module_path)
        local_map = class_names_by_module[module_path]
        direct, qualified = _class_import_bindings(
            module, module_path_obj, root, class_names_by_module
        )
        # Every `qualified` key is a module-qualifier alias that resolved to a
        # real merged project module carrying classes (see
        # _class_import_bindings, which only records a qualifier whose target
        # path is in class_names_by_module). Collect them so sema can tell a
        # genuine `projmod.Class()` construction from an opaque external
        # `ast.Class(...)` whose leaf merely collides with a merged class.
        entry.project_module_qualifiers.update(qualified.keys())
        for cls in module.classes:
            if cls.parent is None:
                continue
            qualifier = getattr(cls, "parent_qualifier", None)
            replacement = None
            if qualifier is not None:
                target_names = qualified.get(qualifier)
                if target_names is not None:
                    replacement = target_names.get(cls.parent)
            else:
                replacement = direct.get(cls.parent)
                if replacement is None:
                    replacement = local_map.get(cls.parent)
            if replacement is not None:
                cls.parent = replacement

        bare = dict(direct)
        for source_name, internal_name in local_map.items():
            bare[source_name] = internal_name
        _rewrite_class_references(module, bare, qualified)
        for cls in module.classes:
            cls.name = local_map.get(cls.name, cls.name)

    # Rebuild the flat class list. Unique names preserve the historical
    # first-definition-wins behavior; cross-module collisions are all retained.
    merged_classes: list = []
    class_origin.clear()
    merged_names: set[str] = set()
    entry_key = str(entry_path.resolve())
    for module_path in discovery_order:
        module = parsed.get(module_path)
        if module is None:
            continue
        for cls in module.classes:
            if cls.name in merged_names:
                continue
            merged_names.add(cls.name)
            merged_classes.append(cls)
            if module_path != entry_key:
                class_origin[cls.name] = module_path
    entry.classes = merged_classes


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
                    _s_orig_names: list = stmt.orig_names
                    _s_names: list = stmt.names
                    for orig in (_s_orig_names if _s_orig_names else _s_names):
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
                # its own file (`from assembly import x86` ->
                # stdlib/assembly/x86.py) must NOT also pull in the
                # package's __init__.py — a bundled package may split
                # genuinely independent submodules whose link requirements
                # differ, and merging __init__ in regardless would drag its
                # dependencies into a program that imported only one leaf.
                # Only fall back to resolving the base module
                # itself when at least one imported name *isn't* its own
                # submodule (so it must live in the package's __init__/be the
                # module itself, e.g. `from os.path import join`).
                resolved_as_submodule: set[str] = set()
                stmt_orig_names: list = stmt.orig_names
                stmt_names: list = stmt.names
                stmt_module: str = stmt.module
                _orig_or_names: list = stmt_orig_names if stmt_orig_names else stmt_names
                for orig in _orig_or_names:
                    sub = _resolve_absolute(f"{stmt_module}.{orig}", root)
                    if sub is None:
                        sub = _resolve_user_module(
                            f"{stmt_module}.{orig}", importer, root
                        )
                    if sub is None:
                        sub = _resolve_bundled_stdlib(f"{stmt_module}.{orig}")
                    if sub is not None:
                        out.append(sub)
                        resolved_as_submodule.add(orig)
                names_to_check: list = _orig_or_names
                _any_unresolved: int = 0
                for _ntc in names_to_check:
                    if _ntc not in resolved_as_submodule:
                        _any_unresolved = 1
                        break
                if _any_unresolved:
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
    # _backends/ is the CPython-only IR backend plugin (see
    # _is_cpython_only_backend's docstring) -- never self-host-compiled,
    # regardless of which project file imports it.
    _filtered: list = []
    for _fp in out:
        if not _is_cpython_only_backend(_fp):
            _filtered.append(_fp)
    return _filtered


def _project_root(entry: Path) -> Path:
    """Find the source root that owns an entry file.

    First discover the entry's top-level package as before. Then walk toward
    the filesystem root looking for an ordinary project/workspace marker.
    This lets an entry nested under one package import sibling top-level
    packages from the same repository, matching CPython's project-root
    behavior, while preserving the old package-root fallback for loose or
    installed source trees without a marker.
    """
    package_root = entry.parent
    while (
        (package_root.parent / "__init__.py").is_file()
        or (package_root / "__init__.py").is_file()
    ):
        if (package_root.parent / "__init__.py").is_file():
            package_root = package_root.parent
        else:
            break

    current = package_root
    while True:
        if (
            (current / "pyproject.toml").is_file()
            or (current / "setup.cfg").is_file()
            or (current / "setup.py").is_file()
            or (current / "project.json").is_file()
            or (current / ".git").is_dir()
        ):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return package_root


def _collect_referenced_names(stmts: list, out: set[str]) -> None:
    """Collect every bare `Name` read and direct `Call` callee name reachable
    from a statement list (recursively, through all dataclass/list/dict AST
    fields). Used to decide whether a candidate-for-renaming global is truly
    module-private (referenced only inside its own defining module)."""
    def walk(value) -> None:
        if isinstance(value, A.Name):
            out.add(value.name)
            return
        if isinstance(value, A.Call):
            out.add(value.func)
        if dataclasses.is_dataclass(value):
            for fld in dataclasses.fields(value):
                walk(getattr(value, fld.name))
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    for s in stmts:
        walk(s)


def _rewrite_name_refs_in_stmts(stmts: list, renames: dict[str, str]) -> None:
    """Rewrite every bare `Name`/`Call`-callee reference to a renamed global,
    recursively through a statement list. Only bare name reads and direct
    call targets are rewritten (an attribute name `obj.field` or a dict/str
    key is never a global reference). Local rebinds are NOT tracked -- a
    module-private global chosen for renaming here is, by construction, only
    ever referenced (never shadowed by a same-named local in the same module,
    which would make it not module-private in the first place)."""
    def rw_expr(value) -> None:
        if isinstance(value, A.Name):
            if value.name in renames:
                value.name = renames[value.name]
            return
        if isinstance(value, A.Call):
            if value.func in renames:
                value.func = renames[value.func]
        if dataclasses.is_dataclass(value):
            for fld in dataclasses.fields(value):
                rw_expr(getattr(value, fld.name))
        elif isinstance(value, (list, tuple)):
            for item in value:
                rw_expr(item)
        elif isinstance(value, dict):
            for item in value.values():
                rw_expr(item)

    for s in stmts:
        rw_expr(s)


def _rename_colliding_module_globals(
    entry: A.Module,
    entry_path: Path,
    parsed: dict[str, A.Module],
    discovery_order: list[str],
    root: Path,
) -> None:
    """Give same-named module-level VALUE globals from different merged modules
    distinct internal names, mirroring the class-collision renaming.

    Whole-program merging flattens every module into one namespace. Two modules
    that each define a top-level `_BINARY_OPS = {...}` (with *different*
    contents -- e.g. one keyed by ast node classes, another by operator
    strings) previously collapsed to a single global ("first wins", see
    `_merge_import_bindings`), silently giving one module the other's dict and
    breaking every lookup. Classes already avoid this via
    `__asmpython_module_N` renaming; value globals now get the same treatment.

    Only a genuinely MODULE-PRIVATE global is renamed: one no other module
    imports by that name (renaming a shared/exported value would break its
    importers, which `_materialize_value_imports` wires up by the original
    name). The first-discovered definition keeps the bare name; each later
    colliding module's copy is renamed and that module's own function/method/
    body references are rewritten to match."""
    # name -> list of module paths (in discovery order) that define it as a
    # top-level PLAIN-VALUE global. Deliberately restricted to plain data
    # initializers (dict/list/tuple/set/str/int/float literals and the like):
    # a class/function/exception name, or a name bound to a call result, may be
    # referenced across module boundaries through the flattened namespace and
    # must never be renamed out from under those references. `__dunder__` names
    # (`__all__`, `__version__`, ...) are also excluded -- they're conventional
    # module metadata, not the differently-valued same-name data globals this
    # pass exists to separate.
    def _is_plain_value_assign(stmt) -> bool:
        if not (isinstance(stmt, A.Assign) and isinstance(stmt.target, str)):
            return False
        if stmt.target.startswith("__") and stmt.target.endswith("__"):
            return False
        return isinstance(
            stmt.value,
            (A.DictLit, A.ListLit, A.TupleLit, A.SetLit, A.StrLit, A.IntLit, A.FloatLit),
        )

    # name -> the DISTINCT module paths that define it (a name reassigned
    # several times within one module is still a single definer -- collect a
    # per-module name set first, so `i = 0` twice in one file doesn't look
    # like a two-module collision and get spuriously renamed).
    definers: dict[str, list[str]] = {}
    for mod_path in discovery_order:
        mod = parsed.get(mod_path)
        if mod is None:
            continue
        local_names: set[str] = set()
        for stmt in mod.body:
            if _is_plain_value_assign(stmt):
                local_names.add(stmt.target)
        for name in local_names:
            definers.setdefault(name, []).append(mod_path)

    # Names any module imports as a value (`from .x import NAME`): renaming
    # these would break the import wiring, so they're off-limits.
    imported_value_names: set[str] = set()
    for mod_path in discovery_order:
        mod = parsed.get(mod_path)
        if mod is None:
            continue
        for stmt in mod.body:
            if isinstance(stmt, A.FromImport):
                orig = stmt.orig_names if stmt.orig_names else stmt.names
                for nm in orig:
                    imported_value_names.add(nm)
                for nm in stmt.names:
                    imported_value_names.add(nm)

    # A name referenced by a module that does NOT define it is shared through
    # the flattened namespace -- renaming any one copy would strand that
    # reference. Only rename a name that is truly module-private: referenced
    # exclusively inside its own definers. Collect, per name, the set of
    # module paths that reference it anywhere (body/funcs/methods).
    #
    # CRITICAL: the ENTRY module's `.funcs`/`.classes` have, by this point,
    # already absorbed every OTHER module's merged funcs/classes (the queue
    # loop did `entry.funcs.append(f)`). Scanning them would attribute a
    # merged function's references to the entry, making every merged private
    # global look cross-referenced ("referenced by the entry"). So for the
    # entry, scan only its own top-level `body`; each merged func's real
    # references are already counted under its ORIGIN module below.
    entry_key = str(entry_path.resolve())
    referencing_modules: dict[str, set[str]] = {}
    for mod_path in discovery_order:
        mod = parsed.get(mod_path)
        if mod is None:
            continue
        refs: set[str] = set()
        _collect_referenced_names(mod.body, refs)
        if str(Path(mod_path).resolve()) == entry_key:
            for nm in refs:
                referencing_modules.setdefault(nm, set()).add(mod_path)
            continue
        for f in mod.funcs:
            _collect_referenced_names(f.body, refs)
        for c in mod.classes:
            for m in c.methods:
                _collect_referenced_names(m.body, refs)
        for nm in refs:
            referencing_modules.setdefault(nm, set()).add(mod_path)

    used_internal: set[str] = set()
    for name, paths in definers.items():
        if len(paths) < 2 or name in imported_value_names:
            continue
        # Module-privacy check: every module that references this name must be
        # one of its definers. If any other module reads it (via the flat
        # namespace), renaming a copy would break that read -- skip it.
        definer_set = set(paths)
        if not referencing_modules.get(name, set()) <= definer_set:
            continue
        # First definer keeps the bare name; rename the rest.
        for idx, mod_path in enumerate(paths[1:], start=1):
            mod = parsed.get(mod_path)
            if mod is None:
                continue
            module_index = discovery_order.index(mod_path)
            internal = f"{name}__asmpython_modvar_{module_index}"
            suffix = 1
            while internal in used_internal or internal in definers:
                internal = f"{name}__asmpython_modvar_{module_index}_{suffix}"
                suffix += 1
            used_internal.add(internal)
            renames = {name: internal}
            # Rewrite the module's own defining assign + every reference in its
            # body, funcs, and methods.
            for s in mod.body:
                if isinstance(s, A.Assign) and s.target == name:
                    s.target = internal
            _rewrite_name_refs_in_stmts(mod.body, renames)
            for f in mod.funcs:
                _rewrite_name_refs_in_stmts(f.body, renames)
            for c in mod.classes:
                for m in c.methods:
                    _rewrite_name_refs_in_stmts(m.body, renames)


def _toplevel_value_assigns(module: A.Module) -> dict[str, A.Stmt]:
    """Top-level `name = <expr>` (and annotated) assignments in a module, keyed
    by target name. These are the module's exported *values* — a sibling that
    does `from .mod import name` is referring to one of these. Only plain
    name-target assigns count (not attribute/subscript writes).

    A module-level `const NAME = value` (from the `constants` compiler
    extension) is just as much an exported value as an ordinary assignment
    -- normalized here into an equivalent `A.Assign` (same "normalize at
    entry" approach used by ir_lower.py/codegen.py) so every downstream
    consumer of this dict, which assumes plain `A.Assign` shape, keeps
    working unchanged. Whether the imported name stays const-locked in the
    *importing* module is out of scope here: each module's own Parser/sema
    run independently (see extensions.py's isolation guarantees), so the
    importer only ever sees a plain value, never the origin module's
    const-ness.
    """
    out: dict[str, A.Stmt] = {}
    for s in module.body:
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            out[s.target] = s
        elif isinstance(s, A.ConstDecl):
            out[s.name] = A.Assign(
                target=s.name, value=s.value, pos=s.pos, annot=s.annotation
            )
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


def load_program(
    entry_src: str,
    entry_path: Path,
    *,
    active_extensions: "frozenset[str] | None" = None,
) -> A.Module:
    """Parse the entry module and every reachable project module, merging their
    top-level funcs, classes, AND imported value globals into the entry Module.
    Returns the merged unit.

    The entry module's own `body` (top-level statements) is the program's main
    code. Imported modules contribute their definitions (funcs/classes) and —
    when the importer pulls a *value* out of them via `from .mod import NAME` —
    that value's initializer assignment, prepended to the entry body so it runs
    (and is collected as a global) before the code that uses it. Other module-
    level side-effecting statements are still not run.

    `active_extensions` is always empty now -- the opt-in compiler-syntax
    extension system was withdrawn (see `archived/extensions/`). The
    parameter is kept only so this function's signature doesn't need to
    change.
    """
    entry_path = entry_path.resolve()
    root = _project_root(entry_path)

    entry = Parser(Lexer(entry_src).tokenize(), active_extensions).parse()

    seen: list[str] = [str(entry_path)]
    # Names already defined so merges don't duplicate (first definition wins).
    _dedupe_lifted_funcs(entry, set())
    func_names = set()
    for f in entry.funcs:
        func_names.add(f.name)

    # Per-module parsed AST + the top-level value assigns it exports, recorded
    # in discovery order so the materialization pass can resolve cross-module
    # value imports and order them leaves-first.
    parsed: dict[str, A.Module] = {str(entry_path): entry}
    discovery_order: list[str] = [str(entry_path)]
    # class name -> the module path string it was merged from, so
    # _materialize_value_imports can find module-level values a merged
    # class's methods reference (e.g. GLRenderer3D's methods reading a
    # sibling `glfns = gl_import()`) even though nothing ever explicitly
    # `from module import glfns`s -- only `from module import GLRenderer3D`.
    class_origin: dict[str, str] = {}
    # Same idea, but for plain top-level functions merged in below: a
    # module's own function bodies referencing that module's own top-level
    # constants (e.g. zipfile.py's functions reading `ZIP_STORED`) need the
    # same auto-materialization, since nothing ever explicitly
    # `from .zipfile import ZIP_STORED` just because `from .zipfile import
    # some_func` merged the function.
    func_origin: dict[str, str] = {}

    queue = _project_imports(entry, entry_path, root)
    while queue:
        mod_path: Path = queue.pop(0)
        mod_path = mod_path.resolve()
        mod_path_str = str(mod_path)
        if mod_path_str in seen:
            continue
        seen.append(mod_path_str)
        try:
            mod_src = mod_path.read_text(encoding="utf-8")
            mod = Parser(Lexer(mod_src).tokenize(), active_extensions).parse()
        except Exception as _exc:
            # A module we can't parse is skipped — it may be third-party-ish or
            # use constructs outside the subset; the importer still type-checks
            # leniently against the missing name.
            continue
        parsed[mod_path_str] = mod
        discovery_order.append(mod_path_str)
        _dedupe_lifted_funcs(mod, func_names)
        mod_is_stdlib = _is_within_stdlib(mod_path)
        for f in mod.funcs:
            if f.name not in func_names:
                func_names.add(f.name)
                if mod_is_stdlib:
                    f.is_stdlib = True
                entry.funcs.append(f)
                func_origin[f.name] = mod_path_str
            elif getattr(f, "is_public_export", False):
                # A native-library export must not be hidden by an earlier
                # private helper with the same bare name from another merged
                # module.  The public definition is the externally visible
                # contract and therefore wins this otherwise-first-definition
                # collision.
                for index, existing in enumerate(entry.funcs):
                    if (
                        existing.name == f.name
                        and not getattr(existing, "is_public_export", False)
                    ):
                        if mod_is_stdlib:
                            f.is_stdlib = True
                        entry.funcs[index] = f
                        func_origin[f.name] = mod_path_str
                        break
        for c in mod.classes:
            if mod_is_stdlib:
                for m in c.methods:
                    m.is_stdlib = True
        # Recurse into this module's own project imports.
        for p in _project_imports(mod, mod_path, root):
            sub: Path = p
            if str(sub.resolve()) not in seen:
                queue.append(sub)

    _merge_classes_with_module_identity(
        entry,
        entry_path,
        parsed,
        discovery_order,
        root,
        class_origin,
    )
    _merge_function_import_aliases(
        entry,
        parsed,
        discovery_order,
        root,
        func_names,
        func_origin,
        active_extensions,
    )
    _rename_colliding_module_globals(
        entry, entry_path, parsed, discovery_order, root
    )
    _bind_module_qualified_names(
        entry, entry_path, parsed, discovery_order, root, func_origin, class_origin
    )
    _merge_import_bindings(entry, parsed, discovery_order)
    _materialize_value_imports(entry, parsed, discovery_order, root, class_origin, func_origin)
    return entry


def _rewrite_module_qualified(
    stmts: list, renames: dict[str, str], shadowed: set[str]
) -> None:
    """Rewrite `M.f(...)` -> `f(...)` and `M.v` -> `v` throughout `stmts`.

    `renames` is keyed by the dotted SPELLING (`"json.dumps"`) so one lookup
    settles both which module is being dotted and which merged symbol it means.
    `shadowed` names a module alias that some local binding has taken over in
    this scope; those are left untouched, since `json = 5` makes `json.x` an
    attribute access on an int, not a module reference.

    Reuses `_walk_exprs` for the statement shapes rather than repeating the
    per-node chain a third time.
    """
    def spelling(e) -> "str | None":
        """The dotted spelling of a Name/Attr chain (`a.b.c`), else None.

        A module reference can be any depth -- `os.path.basename` is
        Attr(Attr(Name(os), path), basename) -- so matching only a bare
        `Name` saw single-segment modules and nothing else.
        """
        parts: list[str] = []
        cur = e
        while isinstance(cur, A.Attr):
            parts.append(cur.name)
            cur = cur.obj
        if not isinstance(cur, A.Name):
            return None
        parts.append(cur.name)
        parts.reverse()
        return ".".join(parts)

    def usable(dotted: "str | None") -> bool:
        # Shadowing is judged on the ROOT name: `os = 5` makes `os.path.x` an
        # attribute access on an int, however deep the chain goes.
        return dotted is not None and dotted.split(".", 1)[0] not in shadowed

    def fix(e):
        if isinstance(e, A.MethodCall):
            base = spelling(e.obj)
            if usable(base) and f"{base}.{e.method}" in renames:
                return A.Call(
                    func=renames[f"{base}.{e.method}"],
                    args=list(e.args),
                    kwargs=list(e.kwargs),
                    pos=e.pos,
                )
            return None
        if isinstance(e, A.Attr):
            base = spelling(e.obj)
            if usable(base) and f"{base}.{e.name}" in renames:
                return A.Name(name=renames[f"{base}.{e.name}"], pos=e.pos)
            return None
        return None

    _map_exprs(stmts, fix)


def _map_exprs(stmts: list, fix) -> None:
    """Walk every expression under `stmts`, replacing any node for which
    `fix(node)` returns a replacement (None leaves it alone). Bottom-up: the
    children of a node are visited before the node itself, so a rewrite of an
    inner expression is visible to an outer one.

    Deliberately shares no code with `_rename_call_targets`: that one mutates
    `Call.func` strings in place and cannot substitute one node kind for
    another, which is exactly what turning a `MethodCall` into a `Call`
    requires.
    """
    def walk_expr(e):
        if e is None or not hasattr(e, "__dataclass_fields__"):
            return e
        for fname in list(e.__dataclass_fields__):
            val = getattr(e, fname, None)
            if isinstance(val, list):
                setattr(e, fname, [
                    walk_expr(item) if hasattr(item, "__dataclass_fields__") else item
                    for item in val
                ])
            elif hasattr(val, "__dataclass_fields__"):
                setattr(e, fname, walk_expr(val))
        replaced = fix(e)
        return replaced if replaced is not None else e

    def walk_stmts(ss) -> None:
        for s in ss or []:
            if s is None or not hasattr(s, "__dataclass_fields__"):
                continue
            for fname in list(s.__dataclass_fields__):
                val = getattr(s, fname, None)
                if isinstance(val, list):
                    # A statement's list field is either nested statements or
                    # nested expressions; `A.Match.cases` is neither (it holds
                    # (pattern, guard, body) tuples), so it gets its own arm.
                    if val and isinstance(val[0], tuple):
                        continue
                    if val and _is_stmt_node(val[0]):
                        walk_stmts(val)
                    else:
                        setattr(s, fname, [
                            walk_expr(item)
                            if hasattr(item, "__dataclass_fields__") else item
                            for item in val
                        ])
                elif hasattr(val, "__dataclass_fields__"):
                    if _is_stmt_node(val):
                        walk_stmts([val])
                    else:
                        setattr(s, fname, walk_expr(val))
            if isinstance(s, A.Match):
                new_cases: list = []
                for pattern, guard, body in s.cases:
                    walk_stmts(body)
                    new_cases.append(
                        (pattern, walk_expr(guard) if guard is not None else None, body)
                    )
                s.cases = new_cases
            elif isinstance(s, A.Try):
                for _t, _b, hbody in s.extra_handlers:
                    walk_stmts(hbody)

    walk_stmts(stmts)


_STMT_TYPES = (
    A.Assign, A.AugAssign, A.MultiAssign, A.TupleAssign, A.ConstDecl,
    A.Return, A.If, A.While, A.For, A.Break, A.Continue, A.ExprStmt,
    A.Pass, A.Import, A.FromImport, A.AttrAssign, A.IndexAssign,
    A.With, A.Try, A.Raise, A.Global, A.Nonlocal, A.Del, A.Match,
    A.YieldStmt, A.FuncDef, A.ClassDef, A.ClosureBind,
)


def _is_stmt_node(node: object) -> bool:
    return isinstance(node, _STMT_TYPES)


def _module_alias_targets(
    module: A.Module, module_path: Path, root: Path
) -> dict[str, str]:
    """`import M` / `import M as A` in `module`, as {bound name -> resolved
    module path}, for MERGED modules only.

    A merged module is one whose source is pulled into the program: bundled
    SOURCE stdlib, or project source. FFI modules (os, sys, math, socket, ...)
    are deliberately excluded -- they resolve through the BINDINGS registry and
    sema already types `os.getcwd()` correctly, so rewriting them would break a
    path that works.
    """
    out: dict[str, str] = {}
    for stmt in _collect_import_stmts(module):
        if not isinstance(stmt, A.Import):
            continue
        target = _resolve_absolute(stmt.module, root)
        if target is None:
            target = _resolve_user_module(stmt.module, module_path, root)
        if target is None:
            target = _resolve_bundled_stdlib(stmt.module)
        if target is None:
            continue
        # `import a.b.c as d` binds `d`, and the unaliased form is reached by
        # its FULL DOTTED SPELLING -- `import os.path` is used as
        # `os.path.basename(...)`, never as `path.basename(...)`. Keying on the
        # spelling covers both: "json" for a plain import, "os.path" for a
        # dotted one.
        #
        # This previously bound NOTHING for a dotted import, on the reasoning
        # that `import a.b.c` binds only the leading segment and so "cannot name
        # the leaf module's own functions". True of the leading segment, but the
        # dotted spelling names them exactly, and dropping it meant `os` stayed
        # unbound: `os.path.basename(p)` lowered to a dict lookup on an
        # uninitialised local, returned 0, and crashed dereferencing it. Four
        # ospath cases plus others in the corpus.
        if stmt.alias:
            out[stmt.alias] = str(target.resolve())
        else:
            out[stmt.module] = str(target.resolve())
    return out


def _rebinds_name(stmts: list, name: str) -> bool:
    """True if `stmts` assigns `name` anywhere, so a module alias of that name
    is shadowed and must not be rewritten inside this scope."""
    for s in stmts or []:
        if isinstance(s, (A.Assign, A.AugAssign, A.ConstDecl)):
            target = getattr(s, "target", None) or getattr(s, "name", None)
            if target == name:
                return True
        elif isinstance(s, A.TupleAssign):
            for t in s.targets:
                if isinstance(t, A.Name) and t.name == name:
                    return True
        elif isinstance(s, A.For):
            if s.var == name or name in s.targets:
                return True
            if _rebinds_name(s.body, name) or _rebinds_name(s.orelse, name):
                return True
        elif isinstance(s, A.With):
            if s.name == name or _rebinds_name(s.body, name):
                return True
        elif isinstance(s, A.If):
            if _rebinds_name(s.then, name) or _rebinds_name(s.orelse, name):
                return True
        elif isinstance(s, A.While):
            if _rebinds_name(s.body, name) or _rebinds_name(s.orelse, name):
                return True
        elif isinstance(s, A.Try):
            if (
                _rebinds_name(s.body, name)
                or _rebinds_name(s.handler, name)
                or _rebinds_name(s.else_body, name)
                or _rebinds_name(s.finally_body, name)
            ):
                return True
            for _t, _b, hbody in s.extra_handlers:
                if _rebinds_name(hbody, name):
                    return True
    return False


def _bind_module_qualified_names(
    entry: A.Module,
    entry_path: Path,
    parsed: dict[str, A.Module],
    discovery_order: list[str],
    root: Path,
    func_origin: dict[str, str],
    class_origin: dict[str, str],
) -> None:
    """Make `import M` + `M.f()` reach the merged `f`.

    Whole-program merging flattens every module's top-level definitions into
    one namespace, which is why `from json import dumps` works: `dumps` simply
    becomes a global. Nothing connected the DOTTED spelling to the same symbol,
    so `import json` + `json.dumps(x)` resolved to nothing, and sema reported
    E005 "no module 'json' is available" -- for a module whose source had in
    fact been merged into the program moments earlier.

    That one gap accounted for 133 of the corpus's failures. It applied to
    every bundled source module (struct, bisect, copy, operator, json, io, re,
    ... all confirmed) and to project source modules imported the same way.

    This pass rewrites, in the entry and in every merged module's bodies:

        MethodCall(obj=Name(M), method=f, args)  ->  Call(func=f, args)
        Attr(obj=Name(M), name=v)                ->  Name(v)

    `func_origin` / `class_origin` map each merged name to the module it came
    from, so a rewrite only happens when the flattened symbol of that name
    ACTUALLY came from the module being dotted. When two merged modules define
    the same top-level name, the loser is left alone rather than silently bound
    to the wrong function -- so this pass can convert a failure into a success,
    never a success into a wrong answer.
    """
    modules: list[tuple[A.Module, Path]] = [(entry, entry_path)]
    for mod_path in discovery_order:
        mod = parsed.get(mod_path)
        if mod is not None and mod is not entry:
            modules.append((mod, Path(mod_path)))

    for module, module_path in modules:
        aliases = _module_alias_targets(module, module_path, root)
        if not aliases:
            continue
        renames: dict[str, str] = {}
        for alias, target_key in aliases.items():
            target = parsed.get(target_key)
            if target is None:
                continue
            for f in target.funcs:
                if f.is_lifted:
                    continue
                if func_origin.get(f.name) == target_key:
                    renames[f"{alias}.{f.name}"] = f.name
            for c in target.classes:
                if class_origin.get(c.name) == target_key:
                    renames[f"{alias}.{c.name}"] = c.name
            for stmt in target.body:
                if isinstance(stmt, (A.Assign, A.ConstDecl)):
                    value_name = getattr(stmt, "target", None) or getattr(
                        stmt, "name", None
                    )
                    if isinstance(value_name, str):
                        renames.setdefault(f"{alias}.{value_name}", value_name)
        if not renames:
            continue
        shadowed = {a for a in aliases if _rebinds_name(module.body, a)}
        _rewrite_module_qualified(module.body, renames, shadowed)
        for f in module.funcs:
            local_shadow = shadowed | {
                a for a in aliases if a in f.params or _rebinds_name(f.body, a)
            }
            _rewrite_module_qualified(f.body, renames, local_shadow)
        for c in module.classes:
            for m in c.methods:
                local_shadow = shadowed | {
                    a for a in aliases if a in m.params or _rebinds_name(m.body, a)
                }
                _rewrite_module_qualified(m.body, renames, local_shadow)


def _merge_function_import_aliases(
    entry: A.Module,
    parsed: dict[str, A.Module],
    discovery_order: list[str],
    root: Path,
    func_names: set[str],
    func_origin: dict[str, str],
    active_extensions: "frozenset[str] | None",
) -> None:
    """Materialize ``from module import func as alias`` as a real symbol.

    Whole-program merging normally keeps one flat function for each original
    source name.  That is insufficient when an entry module defines the same
    name and deliberately preserves the imported function under an alias:
    flattening the alias back to the original name makes the wrapper recurse
    into itself.  Parse a fresh copy of the imported function, rename that copy
    (including direct recursive calls), and merge it under the local alias.
    """
    fresh_modules: dict[str, A.Module] = {}
    for importer_path in discovery_order:
        importer = parsed.get(importer_path)
        if importer is None:
            continue
        for stmt in _collect_import_stmts(importer):
            if not isinstance(stmt, A.FromImport):
                continue
            target_path = _resolve_fromimport_path(
                stmt,
                Path(importer_path),
                root,
            )
            if target_path is None:
                continue
            target_key = str(target_path.resolve())
            if target_key not in parsed:
                continue
            original_names: list = stmt.orig_names if stmt.orig_names else stmt.names
            for local, original in zip(stmt.names, original_names):
                if local == original or local in func_names:
                    continue
                target_module = fresh_modules.get(target_key)
                if target_module is None:
                    try:
                        target_source = target_path.read_text(encoding="utf-8")
                        target_module = Parser(
                            Lexer(target_source).tokenize(),
                            active_extensions,
                        ).parse()
                    except Exception:
                        continue
                    fresh_modules[target_key] = target_module
                imported = None
                for candidate in target_module.funcs:
                    if candidate.name == original and not candidate.is_lifted:
                        imported = candidate
                        break
                if imported is None:
                    continue
                imported.name = local
                _rename_call_targets(imported.body, {original: local})
                # A function aliased out of a stdlib module keeps its stdlib
                # provenance -- the main merge marks stdlib funcs `is_stdlib`
                # (so they may legitimately shadow a builtin name), but this
                # alias path parses a FRESH copy that never went through that
                # marking. Without it, `from fnmatch import fnfilter as filter`
                # registered a non-stdlib `filter` FuncDef and tripped the
                # "cannot redefine builtin 'filter'" guard [E143].
                if _is_within_stdlib(target_path):
                    imported.is_stdlib = True
                entry.funcs.append(imported)
                func_names.add(local)
                func_origin[local] = target_key


def _simple_const_if_targets(stmt: A.If, available: set[str]) -> set[str] | None:
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

    def tuple_target_names(stmt) -> list[str] | None:
        if not isinstance(stmt, A.TupleAssign):
            return None
        if len(stmt.values) != 1:
            return None
        out: list[str] = []
        for t in stmt.targets:
            if not isinstance(t, A.Name):
                return None
            out.append(t.name)
        return out

    def key(stmt) -> str:
        if isinstance(stmt, A.Import):
            return "import:" + stmt.module
        if isinstance(stmt, A.FromImport):
            return "from:" + str(stmt.level) + ":" + stmt.module + ":" + ",".join(stmt.names)
        return ""

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
                _vi_orig: list = stmt.orig_names
                _vi_names: list = stmt.names
                for orig in (_vi_orig if _vi_orig else _vi_names):
                    value_import_targets.add(orig)

    extra: list = []
    # Names already bound at entry top-level, so a merged module's own global
    # doesn't shadow/duplicate one the entry (or an earlier merge) defined.
    available: set = set()
    for s in entry.body:
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            available.add(s.target)
        elif isinstance(s, A.TupleAssign):
            names = tuple_target_names(s)
            if names is not None:
                for name in names:
                    available.add(name)
    for f in entry.funcs:
        available.add(f.name)
    for c in entry.classes:
        available.add(c.name)
    _always_available_add(available)
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
            elif isinstance(stmt, A.TupleAssign):
                names = tuple_target_names(stmt)
                if names is None:
                    continue
                if any(name in value_import_targets or name in available for name in names):
                    continue
                free: set = set()
                for value in stmt.values:
                    _free_names(value, free)
                if not free <= available:
                    continue
                for name in names:
                    available.add(name)
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
        _new_body: list = []
        for _s in extra:
            _new_body.append(_s)
        for _s in entry.body:
            _new_body.append(_s)
        entry.body = _new_body


def _materialize_value_imports(
    entry: A.Module,
    parsed: dict[str, A.Module],
    discovery_order: list[str],
    root: Path,
    class_origin: dict[str, str] | None = None,
    func_origin: dict[str, str] | None = None,
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

    `class_origin` (class name -> the module path string it was merged from)
    also drives materialization for module-level values a merged CLASS's
    methods reference but the entry never explicitly imports by name --
    `from module import GLRenderer3D` merges the class fine (funcs/classes are
    always merged unconditionally), but a sibling `glfns = gl_import()` in
    that same module, referenced only inside GLRenderer3D's method bodies,
    would otherwise never run: nothing about merging the class itself asks
    "what module-level values does this class need". Each free name found
    via _class_free_names is resolved exactly like an explicit value import,
    just sourced from the class's own origin module instead of a FromImport
    statement.
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
    _always_available_add(base_available)

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
            _mv_orig: list = stmt.orig_names
            _mv_names: list = stmt.names
            for local, orig in zip(_mv_names, (_mv_orig if _mv_orig else _mv_names)):
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
        new_stack |= stack
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
    _rev_discovery: list = list(discovery_order)
    _rev_discovery.reverse()
    for mod_path_str in _rev_discovery:
        edges: dict[str, tuple[str, str]] = value_import_edges(mod_path_str)
        for _edge_item in edges.items():
            local: str = _edge_item[0]
            _edge_val = _edge_item[1]
            src_str: str = _edge_val[0]
            orig: str = _edge_val[1]
            resolve(local, src_str, orig, empty_stack)

    # Resolve module-level values a merged CLASS's methods reference, even
    # with no explicit `from module import that_value` anywhere (see this
    # function's docstring -- GLRenderer3D's @glfns.imported methods reading
    # the sibling `glfns = gl_import()` is the motivating case). A free name
    # only resolves here if it's a top-level value assign in the class's OWN
    # origin module: a method referencing some other free name (a builtin, a
    # local, a typo) is silently left alone, same as resolve() already does
    # for unresolvable chains elsewhere in this function.
    if class_origin:
        for cls in entry.classes:
            mod_path_str = class_origin.get(cls.name)
            if mod_path_str is None:
                continue  # entry's own class, nothing to chase
            mod = parsed.get(mod_path_str)
            if mod is None:
                continue
            exports = _toplevel_value_assigns(mod)
            for nm in _class_free_names(cls):
                if nm in exports:
                    resolve(nm, mod_path_str, nm, set())

    # Same as above, but for plain top-level FUNCTIONS merged in from other
    # modules: a function's own body may reference that module's own
    # top-level constants (e.g. zipfile.py's functions reading `ZIP_STORED`)
    # even though nothing ever explicitly `from .zipfile import ZIP_STORED`
    # -- only `from .zipfile import some_func` (which merges the function
    # unconditionally, same as classes, but previously left its needed
    # constants unmaterialized).
    if func_origin:
        for fn in entry.funcs:
            mod_path_str = func_origin.get(fn.name)
            if mod_path_str is None:
                continue  # entry's own function, nothing to chase
            mod = parsed.get(mod_path_str)
            if mod is None:
                continue
            exports = _toplevel_value_assigns(mod)
            for nm in _func_free_names(fn):
                if nm in exports:
                    resolve(nm, mod_path_str, nm, set())

    if prepend:
        _prepend_body: list = []
        for _s in prepend:
            _prepend_body.append(_s)
        for _s in entry.body:
            _prepend_body.append(_s)
        entry.body = _prepend_body


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
