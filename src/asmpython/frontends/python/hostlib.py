"""The host Python installation's packages, as a LIBRARY POINT.

    pip install requests
    asmpython build prog.py          # `import requests` now resolves

## What a library point is, and why it is not just another `--import-path`

A LIBRARY POINT is a search root that came from an INTERPRETER rather than
from the command line: `site-packages`, discovered by asking a Python
installation where it keeps what pip put there. The distinction is not
cosmetic. An `--import-path` is the user naming a directory and meaning it; a
library point is a directory the user never typed, whose contents change when
they run pip, and which may hold several thousand modules written against
CPython rather than against this compiler. Those differences all show up in
diagnostics, so the two are kept apart and `asmpython libraries` prints the
ones in force.

## Where it sits in the search order, and why it is LAST

`imports.py` searches, first hit wins:

1. the bundled standard library
2. the directory of the file being compiled
3. each `--import-path`, in the order given
4. **each library point, in the order the interpreter reports them**

Last, and this is the whole of the safety argument. A pip package named
`typing` or `enum34` must not displace the bundled module of that name, and a
program's own `helpers.py` must not lose to something installed years ago.
Putting library points at the end makes both impossible rather than unlikely:
nothing that resolved before this change resolves differently after it.

## What "resolving" means here, and it is the same splice as everything else

A pip package is ORDINARY PYTHON SOURCE, so it is spliced exactly as a
program's own modules are and as the bundled standard library is -- mangled,
ordered so a dependency precedes its importer, and merged into the one module
the rest of the frontend knows. There is no run-time import system, no
`sys.path`, and nothing of the package survives into the program but the
definitions it actually reached.

That has a consequence worth stating before anyone is surprised by it: **the
whole transitive closure must compile.** `import requests` is also `urllib3`,
`idna`, `charset_normalizer` and `certifi`, and a construct any one of them
uses that this compiler does not accept is an error about that file. The
compiler's own rule applies unchanged -- a construct a library cannot use is a
gap worth closing rather than a reason to drop back to C.

## What is NOT resolved here

A COMPILED EXTENSION MODULE -- `.pyd` on Windows, `.so` on Linux -- is a
native binary built against CPython's C API, and there is no source to splice.
This module FINDS them, so that the failure is `E0129` naming the file and the
distribution it came from, rather than `E0083: no module named '_socket'`
about a file that is plainly sitting right there. Finding it is the whole of
what happens here; `objects/hostsvc.py` holds what loading one would require.

## Which interpreter

The one running the compiler, by default -- which is the one whose `pip` the
user just ran, in the overwhelmingly common case where asmpython was installed
into the same environment. `--host-python PATH` asks a different one, by
running it: an installation's layout is a property of that installation, and
guessing it from a path is how a virtualenv's `site-packages` gets missed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: What the probe below asks an interpreter, and the ONLY thing asked of it.
#:
#: RUN RATHER THAN INSPECTED. `sysconfig`'s answers depend on the running
#: interpreter's prefix, its platform, whether it is a virtualenv and whether
#: the layout was relocated. Deriving that from a path would be reimplementing
#: `site.py` against every layout it has ever had; running the interpreter
#: costs one process and is correct by construction.
_PROBE = r"""
import json, sys, sysconfig
try:
    import site
except Exception:
    site = None
try:
    from importlib.machinery import EXTENSION_SUFFIXES as _ext
except Exception:
    _ext = [".pyd"] if sys.platform == "win32" else [".so"]
paths = sysconfig.get_paths()
points = []
for kind in ("purelib", "platlib"):
    p = paths.get(kind)
    if p:
        points.append([kind, p])
if site is not None:
    try:
        for p in site.getsitepackages():
            points.append(["site", p])
    except Exception:
        pass
    try:
        if site.ENABLE_USER_SITE:
            p = site.getusersitepackages()
            if isinstance(p, str):
                points.append(["user", p])
    except Exception:
        pass
json.dump({
    "executable": sys.executable,
    "version": "%d.%d.%d" % sys.version_info[:3],
    "prefix": sys.prefix,
    "points": points,
    "extension_suffixes": list(_ext),
}, sys.stdout)
"""


@dataclass(frozen=True)
class LibraryPoint:
    """One directory an interpreter keeps installed packages in.

    `kind` is `sysconfig`'s word for it -- `purelib` for pure Python,
    `platlib` for the tree that may hold compiled extensions, `site` and
    `user` for what `site.py` adds. Kept because a diagnostic reads better
    naming what a directory IS than repeating its path.
    """

    path: Path
    kind: str

    def __str__(self) -> str:
        return f"{self.path} ({self.kind})"


@dataclass(frozen=True)
class HostLibrary:
    """An interpreter, and where its packages are.

    Empty and harmless when discovery failed: `points` is `()`, every lookup
    answers None, and the compiler behaves exactly as it did before library
    points existed. A missing interpreter is not an error until someone
    imports something that needed it.
    """

    executable: str = ""
    version: str = ""
    prefix: str = ""
    points: tuple[LibraryPoint, ...] = ()
    #: The host's own `importlib.machinery.EXTENSION_SUFFIXES`, longest first
    #: so `.cp314-win_amd64.pyd` is tried before `.pyd` and the answer names
    #: the specific file rather than a shadow of it.
    extension_suffixes: tuple[str, ...] = ()
    #: Why there is nothing here, when there is nothing here.
    unavailable: str = ""

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(p.path for p in self.points)

    def __bool__(self) -> bool:
        return bool(self.points)


#: Discovery may be a subprocess, and a compilation asks more than once.
_CACHE: dict[str, HostLibrary] = {}


def discover(python: str | None = None) -> HostLibrary:
    """Where `python`'s installed packages live. The running one by default.

    Never raises. An interpreter that cannot be run, answers nothing, or
    answers something unparseable produces an empty `HostLibrary` carrying the
    reason in `unavailable` -- because the caller is a compiler driver that has
    a program to compile either way, and a broken `--host-python` should be a
    diagnostic about that flag rather than a traceback out of the frontend.
    """
    # AN EMPTY NAME IS NO NAME, and normalising it here rather than at the one
    # call site is what keeps the cache honest: `""` is falsy, so it shared a
    # key with `None` while taking the subprocess path, and one `discover("")`
    # would have cached "could not run ''" as the answer for THE RUNNING
    # INTERPRETER, for the rest of the process.
    python = python or None
    key = python or "\0self"
    if key in _CACHE:
        return _CACHE[key]
    found = _discover_uncached(python)
    _CACHE[key] = found
    return found


def forget() -> None:
    """Drop the discovery cache, so a test may probe twice and differently."""
    _CACHE.clear()


def _discover_uncached(python: str | None) -> HostLibrary:
    raw = _probe_self() if python is None else _probe_subprocess(python)
    if isinstance(raw, str):
        return HostLibrary(unavailable=raw)
    prefix = str(raw.get("prefix", ""))
    # THE PREFIX IS NOT A LIBRARY POINT, and `site.getsitepackages()` returns
    # it as one on Windows. It is where the interpreter itself lives -- next
    # to `Lib`, `DLLs` and `python314.dll` -- so searching it would let a
    # stray `.py` beside an executable answer an import, for a directory
    # nothing was ever installed into.
    excluded = {p for p in (_resolved(prefix),) if p is not None}
    points: list[LibraryPoint] = []
    seen: set[Path] = set()
    for entry in raw.get("points", ()):
        try:
            kind, raw_path = entry
        except (TypeError, ValueError):
            continue
        path = _resolved(raw_path)
        # DEDUPLICATED, FIRST SPELLING WINS. `purelib` and a `site` entry are
        # the same directory in almost every layout, and a root searched twice
        # is two `is_file()` calls per import for one answer.
        if path is None or path in seen or path in excluded:
            continue
        seen.add(path)
        if not path.is_dir():
            continue
        points.append(LibraryPoint(path, str(kind)))
    suffixes = tuple(sorted(
        (str(s) for s in raw.get("extension_suffixes", ())),
        key=len, reverse=True))
    return HostLibrary(
        executable=str(raw.get("executable", "")),
        version=str(raw.get("version", "")),
        prefix=prefix,
        points=tuple(points),
        extension_suffixes=suffixes,
    )


def _resolved(raw_path) -> Path | None:
    if not raw_path:
        return None
    try:
        return Path(raw_path).resolve()
    except (OSError, TypeError, ValueError):
        return None


def _probe_self() -> dict | str:
    """The running interpreter, without spawning one.

    Not merely an optimisation: asmpython may be running from a frozen or
    embedded build whose `sys.executable` is not a Python at all, and asking
    THAT to run the probe would fail for a question already answerable here.
    """
    import sysconfig
    try:
        from importlib.machinery import EXTENSION_SUFFIXES as extensions
    except Exception:
        extensions = [".pyd"] if sys.platform == "win32" else [".so"]
    paths = sysconfig.get_paths()
    points: list[list[str]] = []
    for kind in ("purelib", "platlib"):
        found = paths.get(kind)
        if found:
            points.append([kind, found])
    try:
        import site
        for found in site.getsitepackages():
            points.append(["site", found])
        if site.ENABLE_USER_SITE:
            user = site.getusersitepackages()
            if isinstance(user, str):
                points.append(["user", user])
    except Exception:
        # `site` is absent under -S, and its absence is not a failure: the
        # `sysconfig` paths above are the ones that matter.
        pass
    return {
        "executable": sys.executable,
        "version": "%d.%d.%d" % sys.version_info[:3],
        "prefix": sys.prefix,
        "points": points,
        "extension_suffixes": list(extensions),
    }


def _probe_subprocess(python: str) -> dict | str:
    try:
        done = subprocess.run(
            [python, "-I", "-c", _PROBE],
            capture_output=True, text=True, timeout=30,
            # THE ENVIRONMENT MINUS THE ONE VARIABLE THAT LIES. `PYTHONPATH`
            # is the compiler's, not the probed interpreter's, and letting it
            # through reports directories belonging to whoever launched
            # asmpython. `-I` already ignores it; removing it as well covers
            # an interpreter old enough not to have that flag.
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run {python!r}: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        return (f"{python!r} exited with status {done.returncode}"
                + (f": {detail[-1]}" if detail else ""))
    try:
        answer = json.loads(done.stdout)
    except (ValueError, TypeError):
        return f"{python!r} did not answer with the layout of its installation"
    if not isinstance(answer, dict):
        return f"{python!r} did not answer with the layout of its installation"
    return answer


# ── what a name resolves to, when it does not resolve to source ─────────────

@dataclass(frozen=True)
class NativeModule:
    """A compiled extension module found where source was wanted.

    Carries enough for a diagnostic to be worth reading: which file, and which
    installed distribution put it there.
    """

    name: str
    path: Path
    distribution: str = ""

    def __str__(self) -> str:
        if self.distribution:
            return f"{self.path} (from {self.distribution})"
        return str(self.path)


def native_module(name: str, host: HostLibrary) -> NativeModule | None:
    """`name` as a compiled extension module in `host`, or None.

    Both shapes: `_socket` as `_socket.pyd` sitting directly on a point, and
    `numpy.core._multiarray_umath` as a file inside the package's directory.
    """
    if not host.extension_suffixes:
        return None
    rel = name.replace(".", os.sep)
    for point in host.points:
        for suffix in host.extension_suffixes:
            candidate = point.path / (rel + suffix)
            try:
                if candidate.is_file():
                    return NativeModule(name, candidate,
                                        _distribution_of(name))
            except OSError:
                continue
    return None


def _distribution_of(name: str) -> str:
    """The installed distribution owning `name`, best effort and never fatal.

    BEST EFFORT because the mapping is metadata, and metadata can be absent,
    stale, or describe a different interpreter than the one probed --
    `--host-python` reads another installation's directory while this one's
    `importlib.metadata` answers. A wrong attribution in a diagnostic is worse
    than none, so only an unambiguous answer is returned.
    """
    top = name.split(".")[0]
    try:
        from importlib import metadata
        owners = metadata.packages_distributions().get(top) or ()
    except Exception:
        return ""
    if len(owners) != 1:
        return ""
    owner = owners[0]
    try:
        return f"{owner} {metadata.version(owner)}"
    except Exception:
        return owner
