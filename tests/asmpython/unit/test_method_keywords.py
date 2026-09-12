"""Which keywords a built-in method takes, and what happens to the rest.

THE BUG THIS IS FOR. `DYN_METHOD_TABLE` dispatches on ARGUMENT COUNT, and a
keyword argument does not change the count -- so `"a,b,c".split(",",
maxsplit=1)` looked like a one-argument call, took the one-argument symbol,
and the limit was DROPPED. Not refused: dropped. The program printed
`['a', 'b', 'c']` and nothing said why.

Eight of the ten built-in methods that take a keyword were wrong that way, and
an unknown keyword was ignored too.

THE TABLE IS CHECKED AGAINST CPYTHON HERE, not just used. `METHOD_PARAMS`
records each method's parameters in positional order with their defaults, and
every one of those three facts can rot independently -- a parameter renamed, a
default changed, a positional-only marker added. `inspect.signature` is the
same oracle the table was read out of, so asking it again is what keeps them
from drifting.
"""
from __future__ import annotations

import inspect

from tests import harness

from asmpython.frontends.python.methods import (
    DYN_METHOD_TABLE, METHOD_PARAMS, POSITIONAL_ONLY, REQUIRED,
    KeywordError, fold_keywords,
)

#: Which builtin type to ask about each method. All but one have parameters
#: that agree between owners, so one owner each is enough --
#: `test_no_owner_disagrees` is what keeps that true.
OWNER = {
    "split": str, "rsplit": str, "splitlines": str, "replace": str,
    "expandtabs": str, "encode": str, "decode": bytes, "to_bytes": int,
    # `bytes.translate(table, /, delete=b"")` against `str.translate(table)`.
    # BYTES IS THE OWNER THE TABLE DESCRIBES, because a keyword can only be
    # meant for the owner that has one; see `DIFFERS_BY_OWNER`.
    "translate": bytes,
    # `list.sort(*, key=None, reverse=False)`. In the table for the BY-NAME
    # spelling only -- the written one has a branch of its own, because both
    # parameters travel as VALUES rather than into slots.
    "sort": list,
}

#: A method whose CPython signature really does differ between the types that
#: have it, so the one-table-per-method shortcut does not hold and the entry
#: describes a single named owner. `translate` is the only one: `str` takes
#: the table and nothing else, `bytes` takes a `delete` after it. The receiver
#: is a run-time question here, so the difference is settled in the runtime --
#: see `METHOD_KW_SYMBOL` and `apy_bytes_translate`.
DIFFERS_BY_OWNER = {"translate"}

#: A method that takes a keyword in CPython and is DELIBERATELY absent from
#: `METHOD_PARAMS`, with the reason. There are none left.
UNIMPLEMENTED: set[str] = set()

OWNERS = (str, bytes, bytearray, list, dict, set, frozenset, tuple, int, float)


def _signature(method: str):
    return inspect.signature(getattr(OWNER[method], method))


class TestTheTableMatchesCPython:

    @harness.cases("method", sorted(METHOD_PARAMS))
    def test_the_parameters_are_in_positional_order(self, method):
        want = [p.name for p in _signature(method).parameters.values()
                if p.name != "self"]
        got = [name for name, _ in METHOD_PARAMS[method]]
        assert len(got) == len(want), f"{method}: {got} against {want}"
        for mine, theirs in zip(got, want):
            # A POSITIONAL-ONLY PARAMETER IS RECORDED AS UNNAMEABLE, so only
            # the ones that CAN be written by name have to match by name.
            if mine is not POSITIONAL_ONLY:
                assert mine == theirs, f"{method}: {got} against {want}"

    @harness.cases("method", sorted(METHOD_PARAMS))
    def test_the_defaults_are_the_same(self, method):
        for (name, default), p in zip(METHOD_PARAMS[method],
                                      [p for p in
                                       _signature(method).parameters.values()
                                       if p.name != "self"]):
            if default is REQUIRED:
                assert p.default is p.empty, f"{method}.{p.name} has a default"
            else:
                assert default == p.default, (
                    f"{method}.{p.name}: {default!r} against {p.default!r}")

    @harness.cases("method", sorted(METHOD_PARAMS))
    def test_a_positional_only_parameter_is_marked_as_one(self, method):
        for (name, _), p in zip(METHOD_PARAMS[method],
                                [p for p in
                                 _signature(method).parameters.values()
                                 if p.name != "self"]):
            only = p.kind is p.POSITIONAL_ONLY
            assert (name is POSITIONAL_ONLY) == only, (
                f"{method}.{p.name}: table says "
                f"{'positional-only' if name is POSITIONAL_ONLY else 'nameable'}")

    def test_no_owner_disagrees_about_a_method(self):
        """One table per METHOD rather than per method per type, which is only
        sound while the owners agree. They do; this is what says so."""
        for method in sorted(METHOD_PARAMS):
            if method in DIFFERS_BY_OWNER:
                continue
            shapes = set()
            for owner in OWNERS:
                fn = getattr(owner, method, None)
                if fn is None:
                    continue
                try:
                    sig = inspect.signature(fn)
                except (ValueError, TypeError):
                    continue
                shapes.add(tuple(p.name for p in sig.parameters.values()
                                 if p.name != "self"))
            assert len(shapes) <= 1, f"{method} differs by owner: {shapes}"

    def test_every_named_method_is_a_method_the_compiler_knows(self):
        assert set(METHOD_PARAMS) <= set(DYN_METHOD_TABLE)

    def test_no_method_with_a_keyword_is_missing_from_the_table(self):
        """THE OTHER DIRECTION, which is the one that lets a bug back in. A
        method that grows a nameable parameter in a later CPython and is not
        added here goes back to dropping it in silence."""
        missing = []
        for method in sorted(DYN_METHOD_TABLE):
            if (method in METHOD_PARAMS or method in UNIMPLEMENTED
                    or method in ("sort", "update")):
                continue
            for owner in OWNERS:
                fn = getattr(owner, method, None)
                if fn is None:
                    continue
                try:
                    sig = inspect.signature(fn)
                except (ValueError, TypeError):
                    continue
                if any(p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
                       for p in sig.parameters.values() if p.name != "self"):
                    missing.append(f"{owner.__name__}.{method}")
        assert not missing, (
            f"these take a keyword and are not in METHOD_PARAMS: {missing}")


class TestFoldingPutsAKeywordInItsSlot:

    def test_a_keyword_after_a_positional(self):
        assert fold_keywords("split", 1, ["maxsplit"]) == [
            ("pos", 0), ("kw", "maxsplit")]

    def test_a_gap_is_filled_with_the_default(self):
        """`"a b".split(maxsplit=1)` means `split(None, 1)`, which the runtime
        already answers correctly -- that is what folding leans on."""
        assert fold_keywords("split", 0, ["maxsplit"]) == [
            ("default", None), ("kw", "maxsplit")]

    def test_no_keywords_needs_no_plan(self):
        assert fold_keywords("split", 1, []) is None

    def test_padding_fills_the_tail_too(self):
        """`to_bytes` has one entry point taking all three, so every arity of
        it pads rather than dispatching to a family of symbols."""
        assert fold_keywords("to_bytes", 0, [], pad_to=3) == [
            ("default", 1), ("default", "big"), ("default", False)]
        assert fold_keywords("to_bytes", 2, ["signed"], pad_to=3) == [
            ("pos", 0), ("pos", 1), ("kw", "signed")]


class TestWhatIsRefused:
    """Each message is CPython 3.14's own wording: a program may read it."""

    def test_an_unknown_keyword(self):
        with harness.raises(KeywordError,
                            match="got an unexpected keyword argument"):
            fold_keywords("split", 1, ["nope"])

    def test_a_keyword_given_twice(self):
        with harness.raises(KeywordError, match="given by name"):
            fold_keywords("split", 1, ["sep"])

    def test_a_method_that_takes_none(self):
        with harness.raises(KeywordError, match="takes no keyword arguments"):
            fold_keywords("count", 1, ["x"])

    def test_more_positionals_than_parameters(self):
        with harness.raises(KeywordError, match="at most"):
            fold_keywords("splitlines", 3, ["keepends"])

    def test_a_required_parameter_cannot_be_defaulted_around(self):
        """`"a".replace(count=1)` leaves `old` and `new` unfilled, and neither
        has a default to fill them with.

        REPORTED AS THE POSITIONALS IT DID NOT GET, which is CPython's wording
        and its ORDER: a missing required positional beats the keyword that
        was given, and beats an unknown one. The message this used to carry
        named the parameter, which for a POSITIONAL-ONLY one is None -- and
        `replace() missing required argument None` is what a program saw.
        """
        with harness.raises(KeywordError,
                            match="takes at least 2 positional arguments"):
            fold_keywords("replace", 0, ["count"])

    def test_the_suggestion_is_cpythons_own_edit_distance(self):
        """A near-miss keyword gets `. Did you mean 'sep'?` and a far one gets
        nothing.

        PORTED RATHER THAN APPROXIMATED. A suggestion this compiler makes
        where CPython makes none is a new divergence, not a kindness -- and
        the line falls in a place no simple rule finds: `SEP` finds `sep`
        because a case change is half the cost of a real one, and `TABSIZE`
        does NOT find `tabsize`, because seven of them exceed the budget that
        two longer names allow.
        """
        with harness.raises(KeywordError, match="Did you mean 'sep'"):
            fold_keywords("split", 0, ["SEP"])
        with harness.raises(KeywordError, match="Did you mean 'maxsplit'"):
            fold_keywords("split", 0, ["masplit"])
        with harness.raises(KeywordError, match="Did you mean 'tabsize'"):
            fold_keywords("expandtabs", 0, ["tabsiz"])
        try:
            fold_keywords("expandtabs", 0, ["TABSIZE"])
        except KeywordError as exc:
            assert "Did you mean" not in str(exc), str(exc)
        else:
            raise AssertionError("TABSIZE was accepted")
