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
    """Collect the bare names an expression/statement references: `Name`
    lookups and `Call`/`MethodCall` callee names. Used to decide whether a
    value-import's initializer can be safely materialized (every name it needs
    must already be available). Attribute names and string literals are not
    free variables, so they're skipped.
    """
    if isinstance(node, A.Name):
        out.add(node.name)
        return
    if isinstance(node, A.Call):
        out.add(node.func)
        for a in node.args:
            _free_names(a, out)
        for _kw, val in getattr(node, "kwargs", []) or []:
            _free_names(val, out)
        return
    if isinstance(node, A.MethodCall):
        # `obj.method(...)`: the receiver and args are sub-expressions; the
        # method name itself is an attribute, not a free variable.
        _free_names(node.obj, out)
        for a in node.args:
            _free_names(a, out)
        return
    if isinstance(node, A.Attr):
        # `obj.name`: only the object is a free reference.
        _free_names(node.obj, out)
        return
    if isinstance(node, (A.Comprehension, A.DictComprehension)):
        # `[elt for a, b in iter if cond]`: `var`/`targets` are loop-bound
        # names, not free references — collect names from the rest of the
        # node (elt/key/value/iter/cond) and drop the bound ones, so e.g.
        # `{fwd for fwd, _rfl in DUNDER_BINOP.values()}` reports only
        # `DUNDER_BINOP` as free, not `fwd`/`_rfl`.
        bound: set[str] = set()
        if node.var:
            bound.add(node.var)
        _flatten_targets(node.targets, bound)
        inner: set[str] = set()
        for f in dataclasses.fields(node):
            if f.name in ("var", "targets"):
                continue
            _free_names(getattr(node, f.name), inner)
        out |= inner - bound
        return
    if dataclasses.is_dataclass(node):
        for f in dataclasses.fields(node):
            _free_names(getattr(node, f.name), out)
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
    if py.is_file() and _within(py, root):
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
    "configparser", "locale", "socket",
})

# Dotted module names that map to a differently-named file in stdlib/.
_BUNDLED_DOTTED: dict[str, str] = {
    "os.path":      "ospath",
    "urllib.parse": "urllibparse",
}


def _resolve_bundled_stdlib(module: str) -> Path | None:
    stem = _BUNDLED_DOTTED.get(module)
    if stem is None:
        top = module.split(".")[0]
        if top not in _BUNDLED_SOURCE_STDLIB:
            return None
        stem = top
    py = Path(__file__).resolve().parent.parent / "stdlib" / f"{stem}.py"
    return py if py.is_file() else None


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
    function and method bodies (the compiler uses function-local imports, e.g.
    `_handle_include`'s `from asmpython.stdlib.assembly import pkgformat`) and
    control-flow blocks."""
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
                # package's module/__init__, OR a *submodule* (`from
                # asmpython.stdlib.assembly import pkgformat`). Resolve the
                # module itself, and also try each imported name as a submodule.
                p = _resolve_absolute(stmt.module, root)
                if p is None:
                    p = _resolve_user_module(stmt.module, importer, root)
                if p is None:
                    p = _resolve_bundled_stdlib(stmt.module)
                if p is not None:
                    out.append(p)
                for orig in (stmt.orig_names or stmt.names):
                    sub = _resolve_absolute(f"{stmt.module}.{orig}", root)
                    if sub is None:
                        sub = _resolve_user_module(
                            f"{stmt.module}.{orig}", importer, root
                        )
                    if sub is not None:
                        out.append(sub)
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

    seen: set[Path] = {entry_path}
    # Names already defined so merges don't duplicate (first definition wins).
    func_names = {f.name for f in entry.funcs}
    class_names = {c.name for c in entry.classes}

    # Per-module parsed AST + the top-level value assigns it exports, recorded
    # in discovery order so the materialization pass can resolve cross-module
    # value imports and order them leaves-first.
    parsed: dict[Path, A.Module] = {entry_path: entry}
    discovery_order: list[Path] = [entry_path]

    queue = _project_imports(entry, entry_path, root)
    while queue:
        mod_path = queue.pop(0).resolve()
        if mod_path in seen:
            continue
        seen.add(mod_path)
        try:
            mod_src = mod_path.read_text(encoding="utf-8")
            mod = Parser(Lexer(mod_src).tokenize()).parse()
        except Exception:
            # A module we can't parse is skipped — it may be third-party-ish or
            # use constructs outside the subset; the importer still type-checks
            # leniently against the missing name.
            continue
        parsed[mod_path] = mod
        discovery_order.append(mod_path)
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
            if p.resolve() not in seen:
                queue.append(p)

    _merge_import_bindings(entry, parsed, discovery_order)
    _materialize_value_imports(entry, parsed, discovery_order, root)
    return entry


def _merge_import_bindings(
    entry: A.Module, parsed: dict[Path, A.Module], discovery_order: list[Path]
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
    seen_keys: set = set()

    def key(stmt) -> tuple:
        if isinstance(stmt, A.Import):
            return ("import", stmt.module)
        return ("from", stmt.level, stmt.module, tuple(stmt.names))

    extra: list = []
    # Names already bound at entry top-level, so a merged module's own global
    # doesn't shadow/duplicate one the entry (or an earlier merge) defined.
    available: set = {
        s.target
        for s in entry.body
        if isinstance(s, A.Assign) and isinstance(s.target, str)
    }
    available |= {f.name for f in entry.funcs}
    available |= {c.name for c in entry.classes}
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
        # A merged module's own top-level constant assignments (e.g.
        # `ASMPKG_SUFFIX = ".asmpkg"`) are used by its functions, so they must
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
                free: set = set()
                _free_names(stmt.value, free)
                if not free <= available:
                    continue
                available.add(stmt.target)
                extra.append(stmt)
    if extra:
        entry.body[:0] = extra


def _materialize_value_imports(
    entry: A.Module,
    parsed: dict[Path, A.Module],
    discovery_order: list[Path],
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
    base_available: set[str] = {f.name for f in entry.funcs}
    base_available |= {c.name for c in entry.classes}
    base_available |= {
        s.target
        for s in entry.body
        if isinstance(s, A.Assign) and isinstance(s.target, str)
    }
    base_available |= _ALWAYS_AVAILABLE

    # Map each module's locally-imported value name -> (source module, orig
    # name), so a free name in an initializer can be chased to its definition.
    def value_import_edges(mod_path: Path) -> dict[str, tuple[Path, str]]:
        edges: dict[str, tuple[Path, str]] = {}
        for stmt in parsed[mod_path].body:
            if not isinstance(stmt, A.FromImport):
                continue
            tgt = _resolve_fromimport_path(stmt, mod_path, root)
            if tgt is None or tgt not in parsed:
                continue
            for local, orig in zip(stmt.names, stmt.orig_names or stmt.names):
                edges[local] = (tgt, orig)
        return edges

    materialized: dict[str, A.Assign] = {}  # local alias -> renamed assign
    prepend: list[A.Stmt] = []

    def resolve(local: str, mod_path: Path, orig: str, stack: frozenset) -> bool:
        """Ensure `local` is materialized as the value `orig` from `mod_path`.
        Returns True on success. `stack` guards against import cycles."""
        if local in base_available or local in materialized:
            return True
        key = (mod_path, orig)
        if key in stack:  # cycle — give up on this chain
            return False
        exports = _toplevel_value_assigns(parsed[mod_path])
        if orig not in exports:
            return False
        assign = exports[orig]
        free: set[str] = set()
        _free_names(assign.value, free)  # type: ignore[union-attr]
        edges = value_import_edges(mod_path)
        deps: list[A.Assign] = []
        for nm in free:
            if nm in base_available or nm in materialized:
                continue
            # Is it a value this module imported? Chase it.
            if nm in edges:
                src, src_orig = edges[nm]
                if resolve(nm, src, src_orig, stack | {key}):
                    continue
            # Or a value defined locally in this same module? Pull it too.
            if nm in exports:
                if resolve(nm, mod_path, nm, stack | {key}):
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
    for mod_path in reversed(discovery_order):
        for local, (src, orig) in value_import_edges(mod_path).items():
            resolve(local, src, orig, frozenset())

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
            return pkg_init if pkg_init.is_file() and _within(pkg_init, root) else None
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
