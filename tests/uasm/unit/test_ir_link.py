"""Joining modules at the IR: what survives, what collides, what is dropped.

THE RULES ARE A LINKER'S and this pins all three. A definition beats a
declaration, two definitions are an error, and a declaration nobody defines
stays a declaration -- because the platform linker may still resolve it
against something this compiler never saw.

WHY THE MESSAGES ARE TESTED AND NOT JUST THE FAILURE. "defined twice" with no
file named sends the reader to grep for the symbol; the whole reason this
lives above the verifier is that the verifier cannot say which input it came
from.
"""
from __future__ import annotations

from uasm.diagnostics import DiagnosticSink
from uasm.ir import types as T
from uasm.ir.link import merge
from uasm.ir.module import Block, Function, Global, Module


def _module(name: str, *, defines=(), declares=(), globals_=()) -> Module:
    """A module with the named functions, and nothing else in it.

    BUILT BY HAND rather than compiled: this file is about the JOIN, and a
    merge that only worked on bodies some frontend happens to produce would
    be tested against the frontend rather than against its own rules. The
    bodies are empty because nothing here reads one.
    """
    m = Module(name=name)
    for fname in defines:
        m.functions.append(
            Function(name=fname, ret=T.I64, blocks=[Block(label="entry")]))
    for fname in declares:
        m.functions.append(Function(name=fname, ret=T.I64, external=True))
    for gname in globals_:
        m.globals.append(Global(name=gname, size=8))
    return m


def _names(module) -> list[str]:
    return [f.name for f in module.functions]


class TestWhatComesOutOfADisjointJoin:
    def test_every_definition_survives(self):
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", defines=("one",))),
                     ("b.ir", _module("b", defines=("two",)))], sink)
        assert out is not None and not sink.failed
        assert _names(out) == ["one", "two"]

    def test_the_order_is_the_order_given(self):
        # SO THAT JOINING THE SAME FILES TWICE GIVES THE SAME FILE. A merge
        # that sorted, or that rebuilt from a dict, would make the output
        # depend on something the user did not choose.
        sink = DiagnosticSink()
        out = merge([("b.ir", _module("b", defines=("two",))),
                     ("a.ir", _module("a", defines=("one",)))], sink)
        assert _names(out) == ["two", "one"]

    def test_the_globals_come_too(self):
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", globals_=("g",))),
                     ("b.ir", _module("b", globals_=("h",)))], sink)
        assert [g.name for g in out.globals] == ["g", "h"]

    def test_the_inputs_are_recorded(self):
        # PROVENANCE, because the merged file is the one a backend reads and
        # "where did this come from" has no other answer once it is written.
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", defines=("one",))),
                     ("b.ir", _module("b", defines=("two",)))], sink)
        assert out.metadata["linked"] == "a.ir, b.ir"


class TestADefinitionBeatsADeclaration:
    def test_the_declaration_is_dropped(self):
        # ONE NAME AND TWO FUNCTIONS is what the verifier rejects, and it
        # would report that against the symbol rather than against the link.
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", declares=("shared",))),
                     ("b.ir", _module("b", defines=("shared",)))], sink)
        assert not sink.failed
        assert _names(out) == ["shared"]
        assert not out.functions[0].external

    def test_it_is_dropped_whichever_side_declared(self):
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", defines=("shared",))),
                     ("b.ir", _module("b", declares=("shared",)))], sink)
        assert not sink.failed
        assert _names(out) == ["shared"]
        assert not out.functions[0].external

    def test_many_declarations_collapse_to_one(self):
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", declares=("libc",))),
                     ("b.ir", _module("b", declares=("libc",))),
                     ("c.ir", _module("c", declares=("libc",)))], sink)
        assert not sink.failed
        # STILL A DECLARATION, because nothing here defines it and the
        # PLATFORM linker may resolve it against something this compiler
        # never saw. Dropping it entirely would break that link.
        assert _names(out) == ["libc"]
        assert out.functions[0].external


class TestTwoDefinitionsAreAnError:
    def test_it_names_both_inputs(self):
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", defines=("main",))),
                     ("b.ir", _module("b", defines=("main",)))], sink)
        assert out is None and sink.failed
        said = "\n".join(d.message + "\n" + "\n".join(d.notes)
                         for d in sink.diagnostics)
        assert "main" in said and "a.ir" in said and "b.ir" in said

    def test_a_global_is_the_same_rule(self):
        # UNLIKE A FUNCTION there is no "declared" spelling for storage, so
        # two globals of one name are always two things.
        sink = DiagnosticSink()
        out = merge([("a.ir", _module("a", globals_=("g",))),
                     ("b.ir", _module("b", globals_=("g",)))], sink)
        assert out is None and sink.failed

    def test_every_clash_is_reported_and_not_just_the_first(self):
        # ONE ROUND OF FIXING, not one per clash: a user who put the wrong
        # two files together wants to be told so once.
        sink = DiagnosticSink()
        merge([("a.ir", _module("a", defines=("one", "two"))),
               ("b.ir", _module("b", defines=("one", "two")))], sink)
        assert sink.error_count == 2


class TestNothingToLink:
    def test_an_empty_list_is_refused(self):
        sink = DiagnosticSink()
        assert merge([], sink) is None
        assert sink.failed
