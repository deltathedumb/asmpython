"""Resolving a program's own modules: name and level in, path out.

`frontends/python/imports.py` is the first half of compiling more than one
file. It answers "which file does this import statement mean", and nothing
else -- the second half, splicing the source it finds, is `bundled.py`'s
machinery and is not wired up yet.

WHY IT IS TESTED ALONE. The resolution rules are where multi-module
compilation goes quietly wrong: an off-by-one in the relative-import level
reaches a SIBLING of the intended package and finds a real file there, so the
program compiles and means something else. That failure has no symptom until
someone reads the output. Every level and shape asmpython's own source uses is
checked below against asmpython's own tree, which is the largest package to
hand and the one self-hosting has to resolve.
"""
from __future__ import annotations

from pathlib import Path

from tests import harness
from tests.harness import snapshot

from asmpython.frontends.python.imports import Finder, ImportError_

SRC = Path(snapshot.current(Path(__file__).resolve().parents[3]))


@harness.fixture
def finder() -> Finder:
    return Finder((SRC,))


class TestRelativeImports:
    """The arithmetic, which is the part that is easy to get one out."""

    @harness.cases("module,level,package,expected", [
        # The first four are real lines in asmpython's own source; the
        # last two cover the ends of the range.
        ("ir", 3, "asmpython.frontends.python", "asmpython.ir"),
        ("modules", 1, "asmpython.frontends.python",
         "asmpython.frontends.python.modules"),
        ("diagnostics", 3, "asmpython.frontends.python",
         "asmpython.diagnostics"),
        ("link.platform", 3, "asmpython.frontends.python",
         "asmpython.link.platform"),
        # LEVEL 2 GOES UP EXACTLY ONE. From `asmpython.frontends.python`
        # that is `asmpython.frontends`, not `asmpython` -- the expected
        # value here was written as the latter and the resolver was right.
        ("objects", 2, "asmpython.frontends.python",
         "asmpython.frontends.objects"),
        # Absolute is untouched, whatever the package.
        ("json", 0, "asmpython.ir", "json"),
    ])
    def test_a_level_becomes_a_package(self, module, level, package, expected):
        assert Finder().absolute(module, level, package) == expected

    def test_one_dot_means_this_package_not_its_parent(self):
        """The off-by-one that has no symptom.

        `from . import x` inside `a.b` means `a.b.x`. Reading level 1 as "go
        up one" gives `a.x`, which in a tree like asmpython's is very often a
        real module -- so the program compiles, links, and calls the wrong
        function.
        """
        assert Finder().absolute("x", 1, "a.b") == "a.b.x"
        assert Finder().absolute("x", 2, "a.b") == "a.x"
        assert Finder().absolute("x", 3, "a.b") == "x"

    def test_climbing_past_the_root_is_refused(self):
        with harness.raises(ImportError_):
            Finder().absolute("x", 4, "a.b")


class TestFindingTheFile:
    def test_a_module_is_its_file(self, finder):
        assert finder.find("asmpython.ir.types") == SRC / "asmpython/ir/types.py"

    def test_a_package_is_its_init(self, finder):
        assert finder.find("asmpython.ir") == SRC / "asmpython/ir/__init__.py"

    def test_a_name_with_no_file_is_none_not_an_error(self, finder):
        """Not every import names one of the program's modules -- it may be a
        backend namespace, a bundled module, or a mistake the analyser reports
        with a span. Answering None lets each of those still happen."""
        assert finder.find("collections.abc") is None
        assert finder.find("nothing_at_all") is None

    def test_the_search_order_is_the_path_order(self, tmp_path):
        first, second = tmp_path / "a", tmp_path / "b"
        for d in (first, second):
            d.mkdir()
            (d / "shared.py").write_text(f"# {d.name}\n", encoding="utf-8")
        assert Finder((first, second)).find("shared") == first / "shared.py"
        assert Finder((second, first)).find("shared") == second / "shared.py"

    def test_a_directory_named_twice_is_searched_once(self, tmp_path):
        (tmp_path / "m.py").write_text("", encoding="utf-8")
        assert Finder((tmp_path, tmp_path)).roots == (tmp_path.resolve(),)


class TestThePackageAFileIsIn:
    """What a relative import in that file resolves against."""

    def test_a_module_is_in_its_directory(self, finder):
        assert finder.package_of(SRC / "asmpython/frontends/python/lower.py") \
            == "asmpython.frontends.python"

    def test_an_init_is_in_its_own_package_not_the_parent(self, finder):
        """`from . import x` in `ir/__init__.py` means `ir.x`. Treating the
        file as beside the package looks one level too far out."""
        assert finder.package_of(SRC / "asmpython/ir/__init__.py") \
            == "asmpython.ir"

    def test_a_file_on_no_root_has_no_package(self, finder, tmp_path):
        """And so cannot use a relative import -- which is worth saying with a
        diagnostic rather than resolving against a guess."""
        loose = tmp_path / "loose.py"
        loose.write_text("", encoding="utf-8")
        assert finder.package_of(loose) == ""

    def test_a_file_directly_on_a_root_has_no_package(self, tmp_path):
        (tmp_path / "prog.py").write_text("", encoding="utf-8")
        assert Finder((tmp_path,)).package_of(tmp_path / "prog.py") == ""


class TestReadingIsCached:
    def test_the_same_module_is_read_once(self, finder):
        """The dependency walk asks twice -- once to discover what a module
        imports and once to splice it -- and asmpython's own frontend is
        15,000 lines to re-read."""
        first = finder.read("asmpython.ir.types")
        assert finder.read("asmpython.ir.types") is first

    def test_reading_something_absent_names_where_it_looked(self, finder):
        with harness.raises(ImportError_):
            finder.read("nothing_at_all")
