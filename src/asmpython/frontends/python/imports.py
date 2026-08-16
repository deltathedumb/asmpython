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
        for root in self.roots:
            try:
                rel = resolved.relative_to(root)
            except ValueError:
                continue
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
