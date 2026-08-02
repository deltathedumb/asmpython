"""Finding a plugin module, from three places.

    asmpython plugin add mypack --cwd 1 --pypath 1 --pip 0

Three sources, each switchable, tried in that order:

  * CWD -- `mypack.py` or `mypack/` next to where you are standing. For a
    plugin that lives beside the program it compiles, and for trying
    something out before it is packaged.
  * PYTHONPATH -- an ordinary import. For anything already installed or
    already on the path.
  * PIP -- `pip install`, then import. For a plugin published to an index.

CWD IS FIRST AND PIP IS OFF BY DEFAULT, and both defaults are about surprise.
A file you are looking at should win over a same-named package installed
months ago, or editing it appears to do nothing. And installing software from
the network is not something a build command should do because a name did not
resolve -- `--pip 1` is a sentence the user has to type.

Each source reports separately when it fails, because "plugin not found" is
useless: the answers to "is it on my path", "am I in the right directory" and
"is it published" are different actions.
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True, slots=True)
class Sources:
    """Which places to look. `1`/`0` on the command line."""

    cwd: bool = True
    pypath: bool = True
    pip: bool = False

    def enabled(self) -> list[str]:
        return [n for n in ("cwd", "pypath", "pip") if getattr(self, n)]


@dataclass
class Resolution:
    """Where a module came from, and what was tried on the way."""

    module: ModuleType | None = None
    source: str = ""
    origin: str = ""
    tried: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.module is not None


class ResolveError(Exception):
    def __init__(self, name: str, tried: list[str]) -> None:
        detail = "; ".join(tried) if tried else "no sources were enabled"
        super().__init__(f"cannot find plugin {name!r}: {detail}")
        self.name = name
        self.tried = tried


def resolve(name: str, sources: Sources = Sources(),
            root: Path | None = None) -> Resolution:
    """Import `name` from the first enabled source that has it."""
    result = Resolution()
    root = root or Path.cwd()

    if sources.cwd:
        found = _from_directory(name, root)
        if found is not None:
            module, origin = found
            return Resolution(module, "cwd", origin, result.tried)
        result.tried.append(f"not in {root}")

    if sources.pypath:
        # With `--cwd 0` the working directory has to come off sys.path too,
        # or the switch does nothing: `python -m` puts the working directory
        # on the path, so a plugin excluded from the cwd search was found by
        # the import search anyway and reported as `pypath`. The user asked
        # not to load the file sitting next to them and it loaded, under
        # another name -- which is worse than not having the switch.
        with _without_cwd(root, disabled=not sources.cwd):
            try:
                module = importlib.import_module(name)
            except ImportError as exc:
                result.tried.append(f"not importable ({exc})")
            else:
                return Resolution(module, "pypath",
                                  getattr(module, "__file__", "") or "",
                                  result.tried)

    if sources.pip:
        installed, detail = _pip_install(name)
        if not installed:
            result.tried.append(f"pip install failed ({detail})")
        else:
            # The interpreter may already have decided this name does not
            # exist; without this, a just-installed package is invisible for
            # the rest of the process and the user is told to try again.
            importlib.invalidate_caches()
            try:
                module = importlib.import_module(name)
            except ImportError as exc:
                result.tried.append(f"installed but not importable ({exc})")
            else:
                return Resolution(module, "pip",
                                  getattr(module, "__file__", "") or "",
                                  result.tried)

    raise ResolveError(name, result.tried)


@contextmanager
def _without_cwd(root: Path, *, disabled: bool):
    """Drop the working directory from sys.path for the duration.

    `""`, `"."` and the absolute path all mean the same place and all three
    appear in the wild -- `python -m` inserts one spelling, a shell wrapper
    another. Missing any of them makes the exclusion look like it worked
    while the directory is still searched.
    """
    if not disabled:
        yield
        return
    resolved = str(root.resolve())
    saved = list(sys.path)
    sys.path[:] = [p for p in sys.path
                   if p not in ("", ".", resolved)
                   and str(Path(p).resolve()) != resolved]
    try:
        yield
    finally:
        sys.path[:] = saved


def _from_directory(name: str, root: Path) -> tuple[ModuleType, str] | None:
    """Load `root/name.py` or `root/name/__init__.py` by path.

    Loaded by path rather than by putting `root` on `sys.path`, so that
    resolving one plugin does not silently change how every later import in
    the process behaves.
    """
    for candidate in (root / f"{name}.py", root / name / "__init__.py"):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location(name, candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        # Registered before execution, because a package's own submodule
        # imports resolve through sys.modules and would otherwise re-execute
        # the module -- registering everything in it twice.
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module, str(candidate)
    return None


def _pip_install(name: str) -> tuple[bool, str]:
    """`pip install name`, through the interpreter running this.

    `sys.executable -m pip` rather than a bare `pip`, which on a machine with
    several interpreters routinely installs into a different one -- and then
    the import fails for a package the user just watched succeed.
    """
    done = subprocess.run(
        [sys.executable, "-m", "pip", "install", name],
        capture_output=True, text=True)
    if done.returncode == 0:
        return True, ""
    tail = (done.stderr or done.stdout or "").strip().splitlines()
    return False, (tail[-1] if tail else f"exit {done.returncode}")
