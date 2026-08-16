"""What `import` reaches, and who wins a name.

A backend can make modules importable -- a board offers the board. Two
backends, or a backend and the standard set, will eventually both want the
name `math`, so the resolution has a stated rule and this file is the rule:

    1. `<backend>.<x>` is ALWAYS the backend's module.
    2. a standard module wins a bare name.
    3. the backend's module gets the bare name when nothing else took it.

The third rule is the convenience and the second is the compatibility
promise. Getting rule 2 backwards is the failure that matters: a program that
said `import math` before a backend grew one of its own would silently start
meaning something else, and nothing in the program would have changed.
"""
from __future__ import annotations

from tests import harness

from asmpython.frontends.python import modules as m

#: A name nothing else wants, and one everything wants.
HW = {"pins": ("int", 16)}
THEIRS = {"pi": ("float", 3.0)}


@harness.fixture(autouse=True)
def _no_leaks():
    """Every test states its own backend, and none leaks into the next.

    The table is module state because the driver publishes it once per
    compile; two compiles in one process seeing each other's backends is the
    bug this fixture makes impossible in the tests too.
    """
    yield
    m.use_backend("", {})


class TestWhichModuleANameReaches:
    def test_a_free_name_reaches_the_backends_module(self):
        m.use_backend("board", {"hw": HW})
        assert m.resolve("hw") is HW
        assert m.member("hw", "pins") == ("int", 16)

    def test_a_standard_module_wins_the_bare_name(self):
        m.use_backend("board", {"math": THEIRS})
        assert m.resolve("math") is m.BUILTIN_MODULES["math"]
        # And it is the STANDARD one, not merely something: the member that
        # only the standard module has is there.
        assert m.member("math", "tau") is not None

    def test_the_prefixed_path_always_reaches_the_backend(self):
        m.use_backend("board", {"math": THEIRS, "hw": HW})
        assert m.resolve("board.math") is THEIRS
        # NOT ONLY FOR THE COLLIDING ONE. The prefix is the real name, so it
        # works for a module that also has the bare name to itself.
        assert m.resolve("board.hw") is HW

    def test_an_unknown_name_reaches_nothing(self):
        m.use_backend("board", {"hw": HW})
        assert m.resolve("nosuch") is None
        assert m.resolve("board.nosuch") is None
        assert m.member("hw", "nosuch") is None

    def test_no_backend_leaves_the_standard_set_alone(self):
        m.use_backend("", {})
        assert m.resolve("math") is m.BUILTIN_MODULES["math"]
        assert m.resolve("hw") is None

    def test_a_backend_does_not_outlive_its_compile(self):
        m.use_backend("board", {"hw": HW})
        m.use_backend("other", {})
        # REPLACED, not merged: the second compile must not see the first
        # backend's modules.
        assert m.resolve("hw") is None
        assert m.resolve("board.hw") is None


class TestWhatTheDiagnosticOffers:
    def test_it_lists_the_prefixed_path_for_a_collision(self):
        m.use_backend("board", {"math": THEIRS})
        names = m.importable()
        assert "math" in names            # the standard one
        assert "board.math" in names      # the backend's, reachable
        # The backend's `math` has no bare name of its own, so listing one
        # would be an offer the compiler cannot honour.
        assert names.count("math") == 1

    def test_it_lists_a_free_name_both_ways(self):
        m.use_backend("board", {"hw": HW})
        names = m.importable()
        assert "hw" in names and "board.hw" in names


class TestTheRealBackendOffersItsOwn:
    """The C backend declares modules, so the wiring is exercised by the
    thing itself and not only by a table this file made up."""

    def test_the_c_backend_declares_modules(self):
        from asmpython.backend import get, load_builtin
        load_builtin()
        assert get("c").modules, "the C backend declares no modules"

    def test_the_driver_publishes_them(self, tmp_path):
        from asmpython.diagnostics import DiagnosticSink
        from asmpython.driver import Options, compile_source
        src = tmp_path / "prog.py"
        src.write_text("import cinfo\nprint(cinfo.name)\n", encoding="utf-8")
        sink = DiagnosticSink()
        assert compile_source(Options(source=src, backend="c"), sink).ok, \
            [d.message for d in sink.diagnostics]
