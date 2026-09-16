"""What `--opt-level 2` means, and who is handed it.

THE RULE THIS FILE PINS. A component declares its own flags; the driver
registers them without knowing what any of them mean. Every flag has TWO
spellings -- `--opt-level` and `--pybc:opt-level` -- and the qualified one is
registered whether or not anything collides, so a script written against it
keeps working when a plugin later claims the short name. A name two
components both declare makes the SHORT spelling an error naming both, never
a pick: awarding it by registration order would configure the wrong component
silently, and the user would find out from the artifact.

WHY THE COLLISION IS TESTED WITH STUBS and the rest with real components.
Nothing in the tree collides today -- `module-name`, `opt-level` and the
three jvm flags are each claimed once -- so the collision has no real case to
be tested against, unlike the `.c` that `c` and `cpyext` both write. The
stubs are registered through the toolchain registry's own `register` and
`unregister`, which exist for exactly this, rather than reaching into a
private dict.
"""
from __future__ import annotations

import argparse
import contextlib
import io

from tests import harness

from asmpython import backend as backend_registry
from asmpython import frontend as frontend_registry
from asmpython import link as link_registry
from asmpython.driver.cli import _ALL_KINDS, _add_component_options
from asmpython.link.base import Toolchain
from asmpython.options import Option

backend_registry.load_builtin()
frontend_registry.load_builtin()
link_registry.load_builtin()


def _parser(kinds: tuple[str, ...] = _ALL_KINDS) -> argparse.ArgumentParser:
    """A parser carrying nothing but the component options.

    NOT `build_parser()`: that one also carries the driver's own flags, and a
    test that passed because `--opt-level` happened to be one of THOSE would
    keep passing after the component lost it.
    """
    ap = argparse.ArgumentParser(prog="t")
    _add_component_options(ap, kinds)
    return ap


def _parse(*argv: str) -> argparse.Namespace:
    return _parser().parse_args(list(argv))


def _refuse(*argv: str) -> str:
    """Parse `argv`, require it to stop, and answer what it said.

    THE MESSAGE IS THE POINT. "raised SystemExit" is true of a missing value,
    an unknown flag and a typo alike; the claim here is that the parser said
    the NAME was ambiguous and named the spellings that are not.
    """
    said = io.StringIO()
    with contextlib.redirect_stderr(said):
        with harness.raises(SystemExit):
            _parse(*argv)
    return said.getvalue()


class _Stub(Toolchain):
    """A linker that links nothing, registered only for one test."""

    def __init__(self, name: str, *options: Option) -> None:
        self.name = name
        self.description = "a test stub"
        self.options = options

    def link(self, request):                        # pragma: no cover
        raise AssertionError("the stub is never asked to link")


@harness.fixture
def stubs():
    """Two linkers that both declare `--jobs`, and one that declares `--strip`.

    Torn down whether the test passes or not: the registry is process-wide,
    and a leaked stub would show up in some later test's `--help`.
    """
    made = [_Stub("stub-a", Option("jobs", "how many at once"),
                            Option("strip", "throw the symbols away")),
            _Stub("stub-b", Option("jobs", "a different how many"))]
    for tc in made:
        link_registry.register(tc)
    try:
        yield made
    finally:
        for tc in made:
            link_registry.unregister(tc.name)


class TestBothSpellingsReachTheSameOption:
    def test_the_plain_spelling_is_registered(self):
        args = _parse("--opt-level", "2")
        assert args.backend_options == {"opt-level": "2"}

    def test_the_qualified_spelling_is_registered_too(self):
        args = _parse("--pybc:opt-level", "2")
        assert args.backend_options == {"opt-level": "2"}

    def test_the_qualifier_never_reaches_the_component(self):
        # WHAT PYBC DECLARED IS `opt-level`. The qualifier is the parser's
        # business; a component that had to know which spelling was typed
        # would have to declare both.
        plain = _parse("--opt-level", "2")
        qualified = _parse("--pybc:opt-level", "2")
        assert plain.backend_options == qualified.backend_options

    def test_every_declared_option_has_a_qualified_spelling(self):
        # ALL THREE REGISTRIES, not just the backends: the flags a frontend
        # and a linker need used to sit on the driver's own parser.
        flags = set()
        for action in _parser()._actions:
            flags.update(action.option_strings)
        for registry in (backend_registry, frontend_registry, link_registry):
            for name, component in registry.available().items():
                for option in component.options:
                    assert option.qualified(name) in flags, (name, option.name)


class TestEachKindGetsItsOwnTable:
    def test_a_backend_option_lands_in_the_backends_table(self):
        args = _parse("--jvm:class-version", "55")
        assert args.backend_options == {"class-version": "55"}
        assert getattr(args, "linker_options", None) is None

    def test_a_linker_option_lands_in_the_linkers_table(self, stubs):
        # NOT IN `backend_options`. The driver treats anything in a backend's
        # table that the backend does not declare as the user naming a flag
        # the backend does not take (E9106), so a linker's flag arriving
        # there would be reported as the backend's mistake.
        args = _parse("--strip", "yes")
        assert args.linker_options == {"strip": "yes"}
        assert getattr(args, "backend_options", None) is None


@harness.fixture
def cpyext_linker_wants_the_backends_flag():
    """Make the `cpyext` LINKER declare the flag the `cpyext` BACKEND does.

    THIS IS A REAL SHAPE AND NOT A CONTRIVANCE: `cpyext` is a backend and a
    toolchain both, and `Option.qualified` deliberately names the COMPONENT
    rather than its stage, so `--cpyext:module-name` is one spelling whichever
    half a reader has in mind. Registering it twice is what argparse would
    call a conflict, in the parser's own words, before any user typed
    anything.

    The real toolchain is put back afterwards: `register` overwrites by name,
    so the swap is its own undo.
    """
    real = link_registry.available()["cpyext"]
    link_registry.register(
        _Stub("cpyext", Option("module-name", "what the module is called")))
    try:
        yield
    finally:
        link_registry.register(real)


class TestOneNameSpanningTwoKinds:
    def test_the_qualified_spelling_is_registered_once(
            self, cpyext_linker_wants_the_backends_flag):
        # `_parser()` BUILDING AT ALL is half the claim: a second
        # registration of `--cpyext:module-name` raises ArgumentError out of
        # argparse, which no user could act on.
        spellings = [flag for action in _parser()._actions
                     for flag in action.option_strings
                     if flag == "--cpyext:module-name"]
        assert spellings == ["--cpyext:module-name"]

    def test_both_halves_are_handed_the_value(
            self, cpyext_linker_wants_the_backends_flag):
        # NOT A GUESS BETWEEN THEM. Both declared the name, so both get it;
        # the pipeline hands each component only what that component
        # declared, so this cannot reach a third party.
        args = _parse("--cpyext:module-name", "spam")
        assert args.backend_options == {"module-name": "spam"}
        assert args.linker_options == {"module-name": "spam"}

    def test_the_short_spelling_is_not_ambiguous(
            self, cpyext_linker_wants_the_backends_flag):
        # ONE NAME CLAIMS IT, in two kinds. Refusing `--module-name` here
        # would be telling the user to disambiguate between `cpyext` and
        # `cpyext`.
        args = _parse("--module-name", "spam")
        assert args.backend_options == {"module-name": "spam"}
        assert args.linker_options == {"module-name": "spam"}


class TestATieIsRefusedRatherThanAwarded:
    def test_the_refusal_names_both_claimants_and_both_escapes(self, stubs):
        said = _refuse("--jobs", "4")
        assert "--jobs is ambiguous" in said
        assert "stub-a, stub-b" in said
        assert "--stub-a:jobs or --stub-b:jobs" in said

    def test_a_bare_ambiguous_flag_stops_for_being_ambiguous(self, stubs):
        # `nargs="?"`, so the refusal is about the NAME rather than argparse
        # complaining that a value was missing -- which would be the wrong
        # thing to fix.
        assert "ambiguous" in _refuse("--jobs")

    def test_the_qualified_spellings_still_work(self, stubs):
        assert _parse("--stub-a:jobs", "4").linker_options == {"jobs": "4"}
        assert _parse("--stub-b:jobs", "8").linker_options == {"jobs": "8"}

    def test_a_name_only_one_of_them_claims_keeps_its_short_spelling(self,
                                                                    stubs):
        # AMBIGUITY IS PER FLAG, not per component. `stub-a` declaring one
        # contested name does not cost it the other.
        assert _parse("--strip", "yes").linker_options == {"strip": "yes"}


class TestAFlagHasTheShapeItWasDeclaredWith:
    def test_a_repeatable_option_keeps_every_occurrence(self):
        # NOT "THE LAST ONE WINS". A search path is a sequence by nature, and
        # a rule that silently kept only the last `--import-path` is not one
        # anybody would guess from the flag.
        args = _parse("--import-path", "a", "--import-path", "b")
        assert args.frontend_options == {"import-path": ["a", "b"]}

    def test_the_two_spellings_fill_one_list(self):
        args = _parse("--import-path", "a", "--python:import-path", "b")
        assert args.frontend_options == {"import-path": ["a", "b"]}

    def test_a_switch_is_true_and_takes_no_value(self):
        assert _parse("--library").frontend_options == {"library": True}
        # `prog.py` IS NOT SWALLOWED: a switch that consumed the next word
        # would eat the source file, and the failure would be reported
        # against the file that was left. This parser carries no positional,
        # so the word coming back unclaimed is the whole of the claim.
        args, rest = _parser().parse_known_args(["--library", "prog.py"])
        assert args.frontend_options == {"library": True}
        assert rest == ["prog.py"]

    def test_a_short_letter_reaches_the_same_key(self):
        assert _parse("-P").frontend_options == {"safe-path": True}
        assert _parse("--safe-path").frontend_options == {"safe-path": True}
        assert _parse("--python:safe-path").frontend_options == {"safe-path": True}


class TestOneDeclarationSharedIsNotACollision:
    def test_three_linkers_share_one_link_input(self):
        # `cc`, `cpyext` and `baremetal` all declare `--link-input`, and it
        # means the same thing to all three. Refusing it as ambiguous would
        # be telling the user to choose between three spellings of one flag;
        # WHICH linker may honour it is settled later, against the linker
        # that was actually chosen.
        shared = {name for name, tc in link_registry.available().items()
                  if any(o.name == "link-input" for o in tc.options)}
        assert len(shared) > 1
        assert _parse("--link-input", "libfoo.a").linker_options == {
            "link-input": ["libfoo.a"]}

    def test_differing_declarations_of_one_name_still_collide(self, stubs):
        # The stubs' two `jobs` options carry different help text, which is a
        # different declaration and so a real question.
        assert "ambiguous" in _refuse("--jobs", "4")


class TestAVerbOffersOnlyTheStagesItHas:
    def test_run_takes_the_frontends_flags(self):
        only = _parser(("frontend",))
        assert only.parse_args(["--import-path", "a"]).frontend_options == {
            "import-path": ["a"]}

    def test_run_does_not_take_a_backends(self):
        # `run` STOPS AT THE IR, so `--class-version` on it would name a
        # stage this command never reaches -- and, worse, could make a
        # frontend's flag ambiguous against one nothing there could use.
        rest = _parser(("frontend",)).parse_known_args(["--class-version", "55"])[1]
        assert rest == ["--class-version", "55"]
