"""Finding a program's OWN modules, so more than one file can be compiled.

asmpython compiles a file. Real programs are directories, and until now
`import helpers` beside `prog.py` was `E0083: no module named 'helpers' is
available; there is no import path` -- which was true and unhelpful, because
the file was right there.

## What this is, and what it deliberately is not

It is a RESOLVER: a dotted module name and an import level become a path on
disk. It is not a loader, and nothing here executes anything.

What happens to the source once found is `bundled.py`'s business, and that is
the whole trick -- **a program's own modules are spliced exactly as the bundled
standard library is**. `bundled.py` already orders modules so a dependency
precedes its importer, mangles every name so two modules may both define
`helper`, rewrites `mod.name` and `from mod import name` to point at the
mangled definitions, and remaps positions onto the import statement so a
diagnostic points somewhere real. None of that is specific to the standard
library. All of it is what compiling several files into one program needs.

So the result is still ONE module, which the frontend's own words call "the
only shape the rest of the frontend knows".

## The consequences of being a splice, stated plainly

* **No module objects at run time.** `import helpers` binds nothing a program
  can pass around; `helpers.f()` is a call to a mangled function, resolved at
  compile time. `import x` then `x` alone is not a value.
* **One copy, however many importers.** Two modules importing a third get the
  same spliced definitions, so a module-level list in it is shared -- which is
  what Python does, and for a different reason.
* **A cycle is not an error here.** Definitions all land in one module, so
  mutually importing files work as long as neither needs the other's
  module-level statements to have RUN first. Python would raise; this cannot
  detect it. See `_ordered` in `bundled.py`.

## Where it looks

In order, first hit wins:

1. the bundled standard library -- so a program's `queue.py` does not displace
   the real one, and `import queue` keeps meaning what it meant
2. the directory of the file being compiled
3. each `--import-path` directory, in the order given

A package is a directory with `__init__.py`; `a.b.c` is `a/b/c.py` or
`a/b/c/__init__.py`. Relative imports resolve against the importing module's
own package, which is why every module carries one.
"""
from __future__ import annotations

from pathlib import Path

#: The prefix a spliced user name gets. DISTINCT FROM THE BUNDLED ONE, and not
#: for tidiness: `bundled.splice` refuses a tree that already contains its own
#: prefix, on the grounds that a program writing the reserved spelling would
#: otherwise have its own name silently replaced. This splice runs first, so
#: sharing the prefix tripped that guard on every multi-module program --
#: `_Reserved: _asmpy_bundled_opcodes__NUM`, about a name the compiler had
#: just minted itself.
MANGLE = "_asmpy_module_"


class ImportError_(Exception):
    """A module named in an import that no search path holds."""

    def __init__(self, name: str, tried: list[Path]) -> None:
        super().__init__(name)
        self.name = name
        self.tried = tried


class Finder:
    """Where a program's modules live, and how a name becomes a file.

    Holds no state beyond the search path and a cache of what it has read, so
    two compilations in one process cannot see each other's files.
    """

    def __init__(self, roots: tuple[Path, ...] = ()) -> None:
        #: Deduplicated, order preserved: a directory named twice searches
        #: once, and the first spelling decides where it sits.
        seen: dict[Path, None] = {}
        for r in roots:
            try:
                seen.setdefault(Path(r).resolve(), None)
            except OSError:
                continue
        self.roots: tuple[Path, ...] = tuple(seen)
        self._read: dict[str, str] = {}

    # ── resolution ──────────────────────────────────────────────────────────
    def absolute(self, name: str, level: int, package: str) -> str:
        """The absolute module name an import statement means.

        `from ...ir import types` inside `asmpython.frontends.python` is level
        3, which strips three components off `asmpython.frontends.python` --
        NOT two. A relative import's first dot means "this package", so level
        N goes up N-1 packages from it, and the arithmetic being one off is
        how `from . import x` would silently reach a sibling of the package.
        """
        if level == 0:
            return name
        parts = package.split(".") if package else []
        if level - 1 > len(parts):
            raise ImportError_(("." * level) + (name or ""), [])
        base = parts[:len(parts) - (level - 1)]
        return ".".join(base + ([name] if name else []))

    def find(self, name: str) -> Path | None:
        """The file holding `name`, or None. A package is its `__init__.py`."""
        rel = name.replace(".", "/")
        for root in self.roots:
            for candidate in (root / f"{rel}.py", root / rel / "__init__.py"):
                if candidate.is_file():
                    return candidate
        return None

    def package_of(self, path: Path) -> str:
        """The dotted package a file sits in, as this finder sees it.

        Empty when the file is directly on a root, or on none of them -- a
        module with no package cannot use a relative import, and saying so
        with `E0083` beats resolving one against a guess.
        """
        try:
            resolved = Path(path).resolve()
        except OSError:
            return ""
        # THE DEEPEST ROOT WINS, not the first -- which is the opposite of
        # `find`, and both are right. `find` answers "which file does this
        # name mean" and the path order decides that. This answers "what is
        # this file's package", and a file can sit under two roots at once:
        # `src/asmpython/ir/types.py` is `asmpython.ir.types` under `src` and
        # a bare `types` under its own directory, which is always a root.
        #
        # Taking the first match made every relative import in asmpython's own
        # source resolve against no package at all -- so `from . import x` in
        # a file three packages deep looked for a top-level `x`, and the
        # self-hosting probe got WORSE rather than better.
        best = None
        for root in self.roots:
            try:
                rel = resolved.relative_to(root)
            except ValueError:
                continue
            if best is None or len(rel.parts) > len(best.parts):
                best = rel
        for rel in ([best] if best is not None else []):
            # DROPPING THE FILE NAME IS ALREADY RIGHT FOR BOTH SHAPES.
            # `a/b/mod.py` gives `a.b`, and `a/b/__init__.py` gives `a.b` too
            # -- which is what a package's own `__init__` needs, because
            # `from . import x` in it means `a.b.x` rather than `a.x`.
            return ".".join(rel.parts[:-1])
        return ""

    # ── source ──────────────────────────────────────────────────────────────
    def read(self, name: str) -> str:
        """`name`'s source. Cached, because the dependency walk asks twice:
        once to discover what a module imports and once to splice it."""
        if name not in self._read:
            found = self.find(name)
            if found is None:
                raise ImportError_(name, list(self.roots))
            self._read[name] = found.read_text(encoding="utf-8")
        return self._read[name]

    def has(self, name: str) -> bool:
        return self.find(name) is not None


#: The finder in force for the compilation running now.
#:
#: A MODULE GLOBAL, matching how `modules.use_backend` already publishes what a
#: backend makes importable. The frontend is handed a source and a sink and
#: nothing else, so anything the driver knows and the frontend needs arrives
#: this way; threading a finder through `splice` and every helper it calls
#: would be the same coupling spelled longer.
#:
#: Republished every compilation, so two in one process cannot see each
#: other's paths -- the bug that arrangement invites, and the reason `use()`
#: replaces rather than adds.
_CURRENT = Finder()


def use(roots) -> None:
    global _CURRENT
    _CURRENT = Finder(tuple(roots))


def current() -> Finder:
    return _CURRENT


# ── the splice ──────────────────────────────────────────────────────────────
#
# A SECOND DRIVER, beside `bundled.splice` rather than inside it. The
# TRANSFORMS are shared and imported below -- `_Rename` rewrites a module's own
# names to mangled ones, `_Rewrite` points the program's references at them,
# and neither knows anything about the standard library. What differs is the
# DRIVING: bundled modules are a fixed set using absolute imports, and a
# program's are an open set found on a path, using relative ones.
#
# Sharing the driver too was the first plan and it is the wrong trade. That
# function decides what nineteen conformance cases depend on, and folding a
# user-supplied search path into the question it answers puts those cases
# behind a path the user controls, for no gain. The reuse that matters is the
# transforms, and they are reused exactly.
#
# ORDER IS FIXED: this runs FIRST. A program's own module may `import
# functools`, and after this splice that statement is an ordinary one in the
# merged tree, which `bundled.splice` then resolves exactly as it resolves the
# program's own. The other order leaves it unspliced.

def splice(tree, source_path):
    """Merge the program's own imported modules into `tree`, and return it.

    Unchanged when the program imports none of them, so a single-file program
    pays nothing and looks exactly as it did.
    """
    import ast
    from . import bundled

    finder = current()
    if not finder.roots:
        return tree
    package = finder.package_of(source_path)
    order, packages, members = [], {}, {}
    seen = set()

    def wanted(stmt, in_package):
        """The absolute names an import statement could mean, resolvable ones
        only.

        `from pkg import mod` is indistinguishable from `from pkg import name`
        until the path is searched, so both readings are tried and whichever
        exists wins.
        """
        if isinstance(stmt, ast.Import):
            candidates = [(a.name, 0) for a in stmt.names]
        elif isinstance(stmt, ast.ImportFrom):
            base = stmt.module or ""
            candidates = [(base, stmt.level)]
            candidates += [((base + "." if base else "") + a.name, stmt.level)
                           for a in stmt.names]
        else:
            return []
        out = []
        for name, level in candidates:
            if not name:
                continue
            try:
                absolute = finder.absolute(name, level, in_package)
            except ImportError_:
                continue
            # THE STANDARD LIBRARY WINS THE NAME. A program's own `queue.py`
            # must not displace the bundled one, or `import queue` quietly
            # stops meaning what it meant.
            if absolute not in bundled.available() and finder.has(absolute):
                out.append(absolute)
        return out

    def visit(name):
        """Depth-first, emitting a module AFTER what it needs -- the order the
        prelude has to be in, because a spliced definition referring to another
        module's name is only correct once that name exists.

        A CYCLE stops at `seen`, which leaves one of the pair defined after the
        other. Harmless for definitions, and wrong for a module-level statement
        needing the other to have run; see this module's header.
        """
        if name in seen:
            return
        seen.add(name)
        found = finder.find(name)
        own = finder.package_of(found)
        for stmt in ast.parse(finder.read(name), filename=str(found)).body:
            for child in wanted(stmt, own):
                visit(child)
        packages[name] = own
        order.append(name)

    anchor = None
    for stmt in tree.body:
        for name in wanted(stmt, package):
            anchor = anchor or stmt
            visit(name)
    if not order:
        return tree

    prelude = []
    for module in order:
        parsed = ast.parse(finder.read(module), filename=module)
        borrowed, brought, body = {}, {}, []
        for stmt in parsed.body:
            names = wanted(stmt, packages[module])
            if isinstance(stmt, ast.ImportFrom) and names:
                # The statement is DROPPED, because the module it names is
                # spliced instead -- so every name it bound has to point at
                # what that module was spliced under, or it is unbound.
                target = finder.absolute(stmt.module or "", stmt.level,
                                         packages[module])
                for alias in stmt.names:
                    child = finder.absolute(
                        ((stmt.module + ".") if stmt.module else "")
                        + alias.name, stmt.level, packages[module])
                    if child in seen:
                        # `from . import helpers` -- a MODULE, not a name in
                        # one, so `helpers.f` is an attribute access to
                        # rewrite rather than a binding.
                        brought[alias.asname or alias.name] = child
                    else:
                        borrowed[alias.asname or alias.name] = \
                            bundled._mangled(target, alias.name, MANGLE)
                continue
            if isinstance(stmt, ast.Import) and len(names) == len(stmt.names):
                for alias in stmt.names:
                    brought[alias.asname or alias.name] = \
                        finder.absolute(alias.name, 0, packages[module])
                continue
            body.append(stmt)
        parsed.body = body
        # A RE-EXPORT IS A MEMBER. `lib/__init__.py` writing `from .util
        # import shout` makes `lib.shout` a real attribute, and a `defined`
        # set holding only what the file itself declares leaves `lib.shout`
        # unrewritten -- so `lib` survives as a bare name and the program
        # fails with `name 'lib' is not defined`, about a module it imports.
        #
        # Emitted as an ALIAS in the prelude rather than special-cased in the
        # rewriter: `_mangled(lib, shout) = _mangled(lib.util, shout)` makes
        # the name exist under the spelling every reader of `members` already
        # expects, so nothing downstream needs to know a re-export happened.
        defined = _defines(parsed) | set(borrowed)
        members[module] = defined
        for local, target in sorted(borrowed.items()):
            prelude.append(ast.Assign(
                targets=[ast.Name(id=bundled._mangled(module, local, MANGLE),
                                  ctx=ast.Store())],
                value=ast.Name(id=target, ctx=ast.Load())))
        renamed = bundled._Rename(module, defined, borrowed, brought,
                                  members, MANGLE).visit(parsed)
        for stmt in renamed.body:
            if _is_docstring(stmt):
                continue
            prelude.append(stmt)
            # THE MANGLED NAME IS VISIBLE TO A PROGRAM: `type(C).__name__` is
            # the name of the `class` statement. Renaming it is how the
            # binding avoids colliding; restoring `__name__` after it is how
            # the collision stays invisible.
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and stmt.name.startswith(MANGLE):
                real = stmt.name[len(bundled._mangled(module, "", MANGLE)):]
                for dunder in ("__name__", "__qualname__"):
                    prelude.append(ast.Assign(
                        targets=[ast.Attribute(
                            value=ast.Name(id=stmt.name, ctx=ast.Load()),
                            attr=dunder, ctx=ast.Store())],
                        value=ast.Constant(value=real)))

    # EVERY SPLICED NODE POINTS AT THE IMPORT. The positions it arrives with
    # are lines in another file, which do not exist in the one being compiled
    # -- a diagnostic carrying one indexes past the end of the source. The
    # import is where this code entered the program.
    line = getattr(anchor, "lineno", 1)
    col = getattr(anchor, "col_offset", 0)
    for stmt in prelude:
        for node in ast.walk(stmt):
            if hasattr(node, "lineno"):
                node.lineno = node.end_lineno = line
                node.col_offset = col
                node.end_col_offset = col + 1

    names, imported, kept = {}, {}, []
    for stmt in tree.body:
        found = wanted(stmt, package)
        if isinstance(stmt, ast.Import) and len(found) == len(stmt.names):
            for alias in stmt.names:
                imported[alias.asname or alias.name] = \
                    finder.absolute(alias.name, 0, package)
            continue
        if isinstance(stmt, ast.ImportFrom) and found:
            target = finder.absolute(stmt.module or "", stmt.level, package)
            left = []
            for alias in stmt.names:
                child = finder.absolute(
                    ((stmt.module + ".") if stmt.module else "") + alias.name,
                    stmt.level, package)
                if child in seen:
                    imported[alias.asname or alias.name] = child
                elif alias.name in members.get(target, ()):
                    names[alias.asname or alias.name] = \
                        bundled._mangled(target, alias.name, MANGLE)
                else:
                    # NOT SOMETHING THE MODULE DEFINES, so the import of it
                    # SURVIVES -- it may be a bundled name, or a real mistake
                    # the analyser reports with a span of its own.
                    left.append(alias)
            if left:
                kept.append(ast.copy_location(
                    ast.ImportFrom(module=stmt.module, names=left,
                                   level=stmt.level), stmt))
            continue
        kept.append(stmt)

    tree.body = kept
    rewritten = bundled._Rewrite(imported, members, names,
                                 prefix=MANGLE).visit(tree)
    rewritten.body = prelude + rewritten.body
    return ast.fix_missing_locations(rewritten)


def _defines(parsed) -> set:
    """Every module-level name a parsed module binds.

    A MODULE-LEVEL VARIABLE COUNTS. A table a module builds is as much a
    member as a class it defines, and a set holding only definitions leaves
    every reference to one unrewritten -- so it reaches the builtin table,
    which does not have it.
    """
    import ast
    out = set()
    for stmt in parsed.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            out.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target,
                                                            ast.Name):
            out.add(stmt.target.id)
    return out


def _is_docstring(stmt) -> bool:
    import ast
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))
