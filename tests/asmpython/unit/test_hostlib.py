"""The host installation's packages, as a library point.

`frontends/python/hostlib.py` answers two questions: where does an interpreter
keep what pip installed, and is this name a compiled extension rather than
source. Both are asked while resolving an import, so both are cheap to get
subtly wrong in a way nothing notices.

WHAT IS ACTUALLY AT RISK HERE, and it is not discovery failing loudly.

* **Precedence.** Library points are searched LAST. If they ever came first, a
  package installed years ago would decide what a name in this program means,
  and the program would still compile. That has no symptom -- it is the same
  failure mode `bundled.py` guards against with "the standard library wins the
  name", and it is checked the same way: by putting the same file in two
  places and naming which one must win.
* **Never raising.** Discovery runs a subprocess for `--host-python`. A driver
  that has a program to compile must not die because an interpreter someone
  typed does not exist, so every failure has to arrive as an empty result
  carrying a reason.
* **The prefix is not a library point.** `site.getsitepackages()` returns
  `sys.prefix` itself on Windows, next to `python314.dll` and `Scripts`.
  Searching it lets a stray `.py` beside an executable answer an import.
"""
from __future__ import annotations

import sys
from pathlib import Path

from tests import harness

from asmpython.frontends.python import hostlib
from asmpython.frontends.python.imports import Finder


@harness.fixture
def host() -> hostlib.HostLibrary:
    return hostlib.discover()


class TestDiscovery:
    """Asking an interpreter where its packages are."""

    def test_finds_the_running_interpreter(self, host):
        # The interpreter running this suite has a `site-packages`, because
        # the suite itself was installed into one.
        assert host, "no library point found for the interpreter running this"
        assert host.version.startswith(
            "%d.%d" % sys.version_info[:2])

    def test_every_point_is_a_directory(self, host):
        for point in host.points:
            assert point.path.is_dir(), f"{point.path} is not a directory"

    def test_points_are_deduplicated(self, host):
        paths = [p.path for p in host.points]
        assert len(paths) == len(set(paths)), paths

    def test_the_prefix_is_not_a_point(self, host):
        """`site.getsitepackages()` offers it; it is not one.

        The interpreter's prefix holds the interpreter, not installed
        packages. Searching it would let `<prefix>/foo.py` answer `import
        foo` for a directory nothing was ever installed into.
        """
        if not host.prefix:
            harness.skip("this interpreter reports no prefix")
        prefix = Path(host.prefix).resolve()
        assert prefix not in {p.path for p in host.points}

    def test_extension_suffixes_are_longest_first(self, host):
        """So `.cp314-win_amd64.pyd` is tried before `.pyd`.

        Both match the same file; only the first names it specifically, and a
        diagnostic quoting the general one would point at a path that exists
        by luck.
        """
        lengths = [len(s) for s in host.extension_suffixes]
        assert lengths == sorted(lengths, reverse=True), \
            host.extension_suffixes


class TestDiscoveryNeverRaises:
    """Every failure is an empty result carrying a reason."""

    @harness.cases("interpreter", [
        "/definitely/not/a/python",
        "python-that-is-not-on-the-path-either",
    ])
    def test_a_missing_interpreter_is_reported_not_raised(self, interpreter):
        hostlib.forget()
        try:
            found = hostlib.discover(interpreter)
        finally:
            hostlib.forget()
        assert not found
        assert found.unavailable, "a failure with no reason is not reportable"
        assert found.points == ()
        assert found.roots == ()

    def test_an_empty_name_means_the_running_interpreter(self):
        """And does NOT poison the cache for it.

        `""` is falsy, so it shared a cache key with `None` while taking the
        subprocess path: one `discover("")` cached "could not run ''" as the
        answer for the interpreter running the compiler, for the rest of the
        process. Normalised in `discover`; checked here because nothing else
        would notice until a build mysteriously stopped finding packages.
        """
        hostlib.forget()
        try:
            assert hostlib.discover("") == hostlib.discover(None)
            assert not hostlib.discover(None).unavailable
        finally:
            hostlib.forget()

    def test_an_interpreter_that_is_not_one_is_reported(self, tmp_path=None):
        """A real file that is not a Python.

        The probe runs it and reads JSON back; anything else is a refusal,
        not a traceback out of `json`.
        """
        hostlib.forget()
        try:
            found = hostlib.discover(sys.executable + ".not-an-interpreter")
        finally:
            hostlib.forget()
        assert not found and found.unavailable


class TestNativeModules:
    """Telling a compiled extension from a module that is simply absent."""

    def test_a_name_that_is_not_installed_is_not_native(self, host):
        assert hostlib.native_module(
            "there_is_no_package_called_this_one", host) is None

    def test_an_empty_host_answers_none(self):
        """The `--no-site-packages` arrangement, and it must not search."""
        assert hostlib.native_module("_socket", hostlib.HostLibrary()) is None

    def test_a_compiled_extension_is_found_and_named(self, host):
        found = _some_extension(host)
        if found is None:
            harness.skip("no compiled extension is installed to find")
        name, path = found
        native = hostlib.native_module(name, host)
        assert native is not None, f"{name} is a {path.suffix} and was missed"
        assert native.path == path
        assert native.name == name


class TestPrecedence:
    """Library points are searched LAST. This is the whole safety argument."""

    def test_an_explicit_root_wins_over_a_library_point(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            mine, installed = base / "mine", base / "installed"
            mine.mkdir()
            installed.mkdir()
            # THE SAME NAME IN BOTH. The program's own directory must win, or
            # a package installed years ago decides what this name means.
            (mine / "shared.py").write_text("WHO = 'mine'\n", encoding="utf-8")
            (installed / "shared.py").write_text("WHO = 'installed'\n",
                                                 encoding="utf-8")
            finder = Finder((mine, installed))
            assert finder.find("shared") == (mine / "shared.py").resolve()

    def test_a_library_point_still_answers_what_nothing_else_has(self):
        import tempfile
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            mine, installed = base / "mine", base / "installed"
            mine.mkdir()
            installed.mkdir()
            (installed / "only_there.py").write_text("X = 1\n",
                                                     encoding="utf-8")
            finder = Finder((mine, installed))
            assert finder.find("only_there") is not None


class TestFinderIntegration:
    """What the frontend actually holds while a compilation runs."""

    def test_a_finder_with_no_host_never_reports_a_native_module(self):
        assert Finder(()).native("_socket") is None

    def test_the_finder_reports_the_native_module_the_host_has(self, host):
        found = _some_extension(host)
        if found is None:
            harness.skip("no compiled extension is installed to find")
        name, _ = found
        finder = Finder((), host)
        native = finder.native(name)
        assert native is not None and native.name == name

    def test_source_is_preferred_to_an_extension_of_the_same_name(self, host):
        """`find` answers source; `native` is asked only after it says no.

        Kept as a test because the two lookups are separate methods and
        nothing structurally stops a future caller asking them the other way
        round -- at which point a package shipping both a `.py` and a `.pyd`
        would be refused rather than compiled.
        """
        import tempfile
        found = _some_extension(host)
        if found is None:
            harness.skip("no compiled extension is installed to find")
        name, _ = found
        top = name.split(".")[0]
        with tempfile.TemporaryDirectory() as raw:
            mine = Path(raw)
            (mine / f"{top}.py").write_text("X = 1\n", encoding="utf-8")
            finder = Finder((mine,), host)
            assert finder.find(top) is not None, \
                "source next to the program lost to an installed extension"


def _some_extension(host) -> tuple[str, Path] | None:
    """Any compiled extension on any point, as (module name, path).

    Discovered rather than named: which wheels are installed differs between
    machines, and a test naming one is a test that is skipped everywhere
    except where it was written.
    """
    for point in host.points:
        for suffix in host.extension_suffixes:
            for path in point.path.glob(f"*{suffix}"):
                if path.is_file():
                    return path.name[: -len(suffix)], path
            for path in point.path.glob(f"*/*{suffix}"):
                if path.is_file():
                    package = path.parent.name
                    return f"{package}.{path.name[: -len(suffix)]}", path
    return None
