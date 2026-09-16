"""Resolving a program's own modules: name and level in, path out.

`frontends/python/imports.py` is the first half of compiling more than one
file. It answers "which file does this import statement mean", and nothing
else -- the second half, splicing the source it finds, is `bundled.py`'s
machinery.

IT IS WIRED UP, and this said it was not. `imports.splice` is called from
`PythonFrontend.compile` and has been since the commit after the one that
added it; the sentence stayed for every commit since, telling a reader that
the resolver they were about to change had no consumer.
`tests/uasm/integration/test_imports_corpus.py` is where the spliced
result is measured against CPython.

WHY IT IS TESTED ALONE. The resolution rules are where multi-module
compilation goes quietly wrong: an off-by-one in the relative-import level
reaches a SIBLING of the intended package and finds a real file there, so the
program compiles and means something else. That failure has no symptom until
someone reads the output. Every level and shape uasm's own source uses is
checked below against uasm's own tree, which is the largest package to
hand and the one self-hosting has to resolve.
"""
from __future__ import annotations

from pathlib import Path

from tests import harness
from tests.harness import snapshot

from uasm.frontends.python.imports import Finder, ImportError_

SRC = Path(snapshot.current(Path(__file__).resolve().parents[3]))


@harness.fixture
def finder() -> Finder:
    return Finder((SRC,))


class TestRelativeImports:
    """The arithmetic, which is the part that is easy to get one out."""

    @harness.cases("module,level,package,expected", [
        # The first four are real lines in uasm's own source; the
        # last two cover the ends of the range.
        ("ir", 3, "uasm.frontends.python", "uasm.ir"),
        ("modules", 1, "uasm.frontends.python",
         "uasm.frontends.python.modules"),
        ("diagnostics", 3, "uasm.frontends.python",
         "uasm.diagnostics"),
        ("objects.floor", 3, "uasm.frontends.python",
         "uasm.objects.floor"),
        # LEVEL 2 GOES UP EXACTLY ONE. From `uasm.frontends.python`
        # that is `uasm.frontends`, not `uasm` -- the expected
        # value here was written as the latter and the resolver was right.
        ("objects", 2, "uasm.frontends.python",
         "uasm.frontends.objects"),
        # Absolute is untouched, whatever the package.
        ("json", 0, "uasm.ir", "json"),
    ])
    def test_a_level_becomes_a_package(self, module, level, package, expected):
        assert Finder().absolute(module, level, package) == expected

    def test_one_dot_means_this_package_not_its_parent(self):
        """The off-by-one that has no symptom.

        `from . import x` inside `a.b` means `a.b.x`. Reading level 1 as "go
        up one" gives `a.x`, which in a tree like uasm's is very often a
        real module -- so the program compiles, links, and calls the wrong
        function.
        """
        assert Finder().absolute("x", 1, "a.b") == "a.b.x"
        assert Finder().absolute("x", 2, "a.b") == "a.x"

    def test_climbing_past_the_root_is_refused(self):
        """AND LEVEL 3 OUT OF `a.b` IS PAST IT, which this asserted was `x`.

        The guard was `level - 1 > len(parts)`, one level looser than
        CPython's `level > len(parts)`, so the last legal climb was allowed
        to go one further and returned a bare top-level name. In a tree of
        any depth that name usually exists, so `from ... import x` inside
        `a.b` resolved to a real module and nothing said otherwise.
        """
        with harness.raises(ImportError_):
            Finder().absolute("x", 3, "a.b")
        with harness.raises(ImportError_):
            Finder().absolute("x", 4, "a.b")

    def test_the_boundary_is_cpythons_exactly(self):
        """`level > len(parts)` refuses, and one less is the deepest legal."""
        for package, deepest in (("a", 1), ("a.b", 2), ("a.b.c", 3)):
            parts = package.split(".")
            assert Finder().absolute("x", deepest, package) == \
                ".".join(parts[:len(parts) - (deepest - 1)] + ["x"])
            with harness.raises(ImportError_):
                Finder().absolute("x", deepest + 1, package)

    def test_the_two_refusals_are_cpythons_own_sentences(self):
        """WHICH refusal it is says what the file's situation was, so the
        text is CPython's rather than a third wording of our own."""
        from uasm.frontends.python.imports import BEYOND_TOP, NO_PARENT

        with harness.raises(ImportError_) as no_package:
            Finder().absolute("x", 1, "")
        assert no_package.value.reason == NO_PARENT

        with harness.raises(ImportError_) as too_far:
            Finder().absolute("x", 3, "a.b")
        assert too_far.value.reason == BEYOND_TOP


class TestFindingTheFile:
    def test_a_module_is_its_file(self, finder):
        assert finder.find("uasm.ir.types") == SRC / "uasm/ir/types.py"

    def test_a_package_is_its_init(self, finder):
        assert finder.find("uasm.ir") == SRC / "uasm/ir/__init__.py"

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
        assert finder.package_of(SRC / "uasm/frontends/python/lower.py") \
            == "uasm.frontends.python"

    def test_an_init_is_in_its_own_package_not_the_parent(self, finder):
        """`from . import x` in `ir/__init__.py` means `ir.x`. Treating the
        file as beside the package looks one level too far out."""
        assert finder.package_of(SRC / "uasm/ir/__init__.py") \
            == "uasm.ir"

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
        imports and once to splice it -- and uasm's own frontend is
        15,000 lines to re-read."""
        first = finder.read("uasm.ir.types")
        assert finder.read("uasm.ir.types") is first

    def test_reading_something_absent_names_where_it_looked(self, finder):
        with harness.raises(ImportError_):
            finder.read("nothing_at_all")


class TestOneSymbolPerMemberAndNeverTwo:
    """`_mangled` must be injective, because a collision is SILENT.

    Two definitions minting one symbol is not a crash and not a diagnostic:
    the second overwrites the first and the program prints the survivor. All
    three shapes below did exactly that.
    """

    def test_a_dot_and_an_underscore_are_different_modules(self):
        from uasm.frontends.python.bundled import _mangled
        assert _mangled("a.b", "X") != _mangled("a_b", "X")

    def test_the_module_and_the_member_cannot_run_together(self):
        """`a.b` + `X` and `a` + `b_X` flattened to the same symbol, because
        nothing said where the module component ended."""
        from uasm.frontends.python.bundled import _mangled
        assert _mangled("a.b", "X") != _mangled("a", "b_X")

    def test_it_is_injective_over_a_sweep(self):
        from uasm.frontends.python.bundled import _mangled
        modules = ["a", "a.b", "a_b", "a._b", "a.b.c", "a__b", "copy",
                   "collections.abc", "_pylex", "a.b_c", "a_b.c"]
        members = ["X", "b_X", "Error", "_p", "a_b_X", "c_X"]
        seen: dict[str, tuple[str, str]] = {}
        for module in modules:
            for member in members:
                symbol = _mangled(module, member)
                assert symbol not in seen, (
                    f"{(module, member)} and {seen[symbol]} both mint "
                    f"{symbol!r}")
                seen[symbol] = (module, member)

    def test_the_real_name_is_still_recoverable_by_prefix(self):
        """Both splices restore `__name__` by stripping `_mangled(module, '')`
        off the front, so that has to stay a prefix of the whole symbol."""
        from uasm.frontends.python.bundled import _mangled
        for module, member in (("copy", "Error"), ("a.b", "X"),
                               ("a_b", "X"), ("collections.abc", "Iterable")):
            full = _mangled(module, member)
            head = _mangled(module, "")
            assert full.startswith(head)
            assert full[len(head):] == member
