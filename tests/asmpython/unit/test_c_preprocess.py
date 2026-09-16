"""The C preprocessor: phases 1 to 4.

THE STANDARD'S OWN EXAMPLES ARE THE CENTRE OF THIS FILE. 6.10.3.5 prints the
expected expansion of five programs, and they were chosen by the committee
precisely because every wrong-but-plausible macro expander gets one of them
wrong. A preprocessor that passes all five is not proved correct, but one that
fails any is proved wrong, and against a document rather than against an
opinion.

The rest are cases this implementation got wrong at some point. Every one of
them PARSED before it was fixed -- that is the failure mode here. A macro
expander does not crash on a program it mishandles; it produces different
tokens, and the first thing that notices is a compile error somewhere else
entirely, or nothing at all.
"""
from __future__ import annotations

from tests import harness

from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.frontends.c.lexer import lex, phase12
from asmpython.frontends.c.literals import (
    LiteralError, decode_char, join_strings, parse_number,
)
from asmpython.frontends.c.ppexpr import evaluate
from asmpython.frontends.c.preprocess import Preprocessor, _spell
from asmpython.frontends.c.tokens import Kind


def pp(source: str) -> str:
    """The token spelling a translation unit preprocesses to."""
    sink = DiagnosticSink()
    tokens = Preprocessor(sink).run(SourceFile(source, "t.c"))
    assert not sink.failed, [d.message for d in sink.diagnostics]
    return _spell([t for t in tokens if t.kind is not Kind.EOF])


def diagnostics(source: str) -> list[str]:
    sink = DiagnosticSink()
    Preprocessor(sink).run(SourceFile(source, "t.c"))
    return [d.code for d in sink.diagnostics]


def tokens(source: str):
    sink = DiagnosticSink()
    out = lex(SourceFile(source, "t.c"), sink)
    return [t for t in out if t.kind is not Kind.EOF], sink


# ── phases 1 and 2 ──────────────────────────────────────────────────────────
class TestTheLogicalSourceLine:
    def test_a_splice_joins_one_identifier(self):
        """`va\\<newline>lue` is ONE name, and its caret goes on the `v`."""
        got, _ = tokens("va\\\nlue")
        assert [t.text for t in got] == ["value"]
        assert got[0].span.start_loc.line == 1
        assert got[0].span.start_loc.column == 1

    def test_the_span_covers_the_original_text(self):
        """The span indexes the file the USER wrote, splices included."""
        got, _ = tokens("int ma\\\nin;")
        name = got[1]
        assert name.text == "main"
        assert name.span.text == "ma\\\nin"

    def test_crlf_and_lf_tokenise_alike(self):
        a, _ = tokens("int x;\r\nint y;\r\n")
        b, _ = tokens("int x;\nint y;\n")
        assert [t.text for t in a] == [t.text for t in b]

    def test_trigraphs_are_off_unless_asked(self):
        assert phase12("a??=b")[0] == "a??=b"
        assert phase12("a??=b", trigraphs=True)[0] == "a#b"

    def test_the_offset_map_has_an_entry_past_the_end(self):
        """"Expected `}` at end of file" needs somewhere to point."""
        text, orig = phase12("ab\\\ncd")
        assert len(orig) == len(text) + 1


# ── phase 3 ─────────────────────────────────────────────────────────────────
class TestPreprocessingTokens:
    @harness.cases("text", ["1.2.3", "0xEp+q", "1e+5", ".5", "0x1p-3",
                            "1'000'000"])
    def test_a_pp_number_is_one_token(self, text):
        """6.4.8. Only some of these are constants, and phase 3 may not care:
        `#if 0` might delete the text before anything asks."""
        got, sink = tokens(text)
        assert [t.kind for t in got] == [Kind.NUMBER]
        assert got[0].text == text
        assert not sink.failed

    def test_a_comment_is_one_space_and_not_nothing(self):
        got, _ = tokens("a/**/b")
        assert [t.text for t in got] == ["a", "b"]

    def test_a_comment_across_lines_does_not_end_a_directive(self):
        assert pp("#define X 1 /* a\n */ + 2\nX\n") == "1 + 2"

    def test_a_digraph_is_the_token_it_spells(self):
        got, _ = tokens("a<:1:> <% %>")
        assert [t.text for t in got] == ["a", "[", "1", "]", "{", "}"]

    def test_an_encoding_prefix_belongs_to_its_literal(self):
        got, _ = tokens('u8"a" L\'c\' u8 "b"')
        assert [t.kind for t in got] == [
            Kind.STRING, Kind.CHARCONST, Kind.IDENT, Kind.STRING]

    def test_an_unterminated_literal_is_reported_not_raised(self):
        got, sink = tokens('"abc\n')
        assert [d.code for d in sink.diagnostics] == ["E1003"]

    def test_maximal_munch_finds_the_longest_punctuator(self):
        got, _ = tokens("a>>=b ... c")
        assert [t.text for t in got] == ["a", ">>=", "b", "...", "c"]


# ── the standard's macro examples ───────────────────────────────────────────
class TestTheStandardsOwnExamples:
    """C99 6.10.3.5. Each is printed in the document with its expansion."""

    def test_example_3_rescanning_and_replacement(self):
        source = (
            "#define x 3\n"
            "#define f(a) f(x * (a))\n"
            "#undef x\n"
            "#define x 2\n"
            "#define g f\n"
            "#define z z[0]\n"
            "#define h g(~\n"
            "#define m(a) a(w)\n"
            "#define w 0,1\n"
            "#define t(a) a\n"
            "f(y+1) + f(f(z)) % t(t(g)(0) + t)(1);\n"
        )
        assert pp(source) == (
            "f(2 * (y+1)) + f(2 * (f(2 * (z[0])))) % f(2 * (0)) + t(1);")

    def test_example_4_stringising_and_pasting(self):
        source = (
            "#define hash_hash # ## #\n"
            "#define mkstr(a) # a\n"
            "#define in_between(a) mkstr(a)\n"
            "#define join(c, d) in_between(c hash_hash d)\n"
            "char p[] = join(x, y);\n"
        )
        assert pp(source) == 'char p[] = "x ## y";'

    def test_example_5_placemarkers(self):
        source = (
            "#define t(x,y,z) x ## y ## z\n"
            "int j[] = { t(1,2,3), t(,4,5), t(6,,7), t(8,9,),\n"
            "t(10,,), t(,11,), t(,,12), t(,,) };\n"
        )
        assert pp(source) == (
            "int j[] = { 123, 45, 67, 89, 10, 11, 12, };")

    def test_the_three_a_recursion_guard_gets_wrong(self):
        """Hide sets, not a depth counter. See the module docstring."""
        assert pp("#define f(a) a*g\n#define g(a) f(a)\nf(2)(9)\n") == "2*9*g"
        assert pp("#define foo bar\n#define bar foo\nfoo\n") == "foo"
        assert pp("#define str(x) #x\n#define xstr(x) str(x)\n"
                  "#define N 10\nstr(N) xstr(N)\n") == '"N" "10"'


# ── variadic macros ─────────────────────────────────────────────────────────
class TestVariadicMacros:
    def test_va_args_takes_every_remaining_comma(self):
        assert pp("#define f(a,...) g(a)(__VA_ARGS__)\nf(1,2,3,4)\n") \
            == "g(1)(2,3,4)"

    def test_va_opt_appears_only_when_something_was_passed(self):
        source = ("#define G(fmt,...) p(fmt __VA_OPT__(,) __VA_ARGS__)\n"
                  'G("a") G("a",1)\n')
        assert pp(source) == 'p("a") p("a",1)'

    def test_the_gnu_comma_swallow(self):
        """`, ## __VA_ARGS__` is not a paste at all: with arguments it stands
        aside, and without them it deletes the comma. Every BSD and glibc
        header uses it, and `,##1` is not even a token."""
        source = ('#define F(fmt,...) p(fmt, ##__VA_ARGS__)\n'
                  'F("a") F("a",1,2)\n')
        assert pp(source) == 'p("a") p("a",1,2)'

    def test_a_function_like_name_without_a_paren_is_left_alone(self):
        assert pp("#define min(a,b) ((a)<(b)?(a):(b))\nint min;\n") == "int min;"


# ── conditionals ────────────────────────────────────────────────────────────
class TestConditionalInclusion:
    def test_an_undefined_name_is_zero_and_not_an_error(self):
        """6.10.1p4, and the most-used property of the whole mechanism."""
        assert pp("#if NOT_DEFINED\nyes\n#else\nno\n#endif\n") == "no"

    def test_defined_survives_expansion(self):
        assert pp("#define X 1\n#if defined X\nyes\n#endif\n") == "yes"

    def test_a_dead_branch_is_not_even_read(self):
        """A `#if 0` around another language's source is ordinary. Phase 3
        keeps a stray `@` without complaint and only phase 7 refuses one, so
        nothing is reported about text the program deleted."""
        source = "#if 0\n#this is not a directive\n@@@\n#endif\nok\n"
        assert pp(source) == "ok"
        assert diagnostics(source) == []

    def test_nesting_inside_a_dead_branch_still_matches(self):
        source = ("#ifdef NO\n#if 1\na\n#else\nb\n#endif\n#else\nc\n#endif\n")
        assert pp(source) == "c"

    def test_elif_stops_after_the_first_true_branch(self):
        assert pp("#if 0\na\n#elif 1\nb\n#elif 1\nc\n#else\nd\n#endif\n") == "b"

    @harness.cases("expr,want", [
        ("1", True), ("0", False), ("-1 < 0u", False), ("1 ? 2 : 3", True),
        ("0 && 1/0", False), ("1 || 1/0", True), ("(1+2)*3 == 9", True),
        ("'a' == 97", True), ("~0 == -1", True), ("5/2 == 2", True),
        ("-5/2 == -2", True), ("-5%2 == -1", True), ("1<<3 == 8", True),
    ])
    def test_the_arithmetic(self, expr, want):
        sink = DiagnosticSink()
        toks = [t for t in lex(SourceFile(expr, "t"), sink)
                if t.kind is not Kind.EOF]
        assert evaluate(toks, sink) is want

    def test_division_by_zero_in_a_dead_branch_is_not_reported(self):
        """`#if defined(X) && 100/X > 2` is ordinary, portable C."""
        assert diagnostics("#if 0 && 1/0\n#endif\n") == []

    def test_an_unterminated_conditional_is_reported(self):
        assert "E1110" in diagnostics("#if 1\nx\n")


# ── directives ──────────────────────────────────────────────────────────────
class TestDirectives:
    def test_line_moves_what_line_reports(self):
        assert pp('#line 100 "z.c"\n__LINE__ __FILE__\n') == '100 "z.c"'

    def test_counter_increases(self):
        assert pp("__COUNTER__ __COUNTER__ __COUNTER__\n") == "0 1 2"

    def test_error_is_a_diagnostic_and_not_a_crash(self):
        assert "E1112" in diagnostics("#error nope\n")

    def test_a_redefinition_warns_and_takes_the_new_one(self):
        sink = DiagnosticSink()
        out = Preprocessor(sink).run(
            SourceFile("#define A 1\n#define A 2\nA\n", "t.c"))
        assert [d.code for d in sink.diagnostics] == ["W1125"]
        assert _spell([t for t in out if t.kind is not Kind.EOF]) == "2"

    def test_an_identical_redefinition_is_silent(self):
        assert diagnostics("#define A 1\n#define A 1\n") == []

    def test_include_finds_a_bundled_header_and_a_guard_works(self):
        once = pp("#include <stddef.h>\n")
        twice = pp("#include <stddef.h>\n#include <stddef.h>\n")
        assert "size_t" in once
        assert twice == once

    def test_has_include_answers_both_ways(self):
        source = ("#if __has_include(<stddef.h>)\nyes\n#endif\n"
                  "#if __has_include(<no_such_header.h>)\nbad\n#endif\n")
        assert pp(source) == "yes"

    def test_a_missing_header_names_where_it_looked(self):
        sink = DiagnosticSink()
        Preprocessor(sink).run(SourceFile('#include "nope.h"\n', "t.c"))
        assert [d.code for d in sink.diagnostics] == ["E1134"]
        assert "searched" in sink.diagnostics[0].notes[0]


# ── literals, shared by the preprocessor and the parser ────────────────────
class TestLiterals:
    @harness.cases("text,value,bits,signed", [
        ("1", 1, 32, True),
        ("0xFFFFFFFF", 0xFFFFFFFF, 32, False),
        ("4294967295", 4294967295, 64, True),
        ("1u", 1, 32, False),
        ("1L", 1, 64, True),
        ("0x7fffffff", 0x7FFFFFFF, 32, True),
        ("0x80000000", 0x80000000, 32, False),
        ("0b1011", 11, 32, True),
        ("0777", 511, 32, True),
    ])
    def test_an_integer_constants_type_is_part_of_its_value(
            self, text, value, bits, signed):
        """`-1 < 0xFFFFFFFFu` is false and `-1 < 4294967295` is true, and the
        only difference is which type the constant got."""
        got = parse_number(text)
        assert (got.value, got.bits, got.signed) == (value, bits, signed)

    @harness.cases("text,value", [
        ("'a'", 97), ("'\\n'", 10), ("'\\0'", 0), ("'\\xff'", -1),
        ("'\\377'", -1), ("'\\x41'", 65), ("L'\\u00e9'", 0xE9),
    ])
    def test_character_constants(self, text, value):
        assert decode_char(text).value == value

    def test_plain_char_is_signed_here(self):
        """One decision, made in `literals.py`, followed by `<limits.h>`."""
        assert decode_char("'\\xff'").value == -1

    def test_adjacent_literals_take_the_wide_prefix(self):
        prefix, values = join_strings(['"\\xff"', 'L"a"'])
        assert prefix == "L"
        assert values == [0xFF, 0x61]

    def test_mixing_two_wide_prefixes_is_refused(self):
        with harness.raises(LiteralError, match="encoding prefix"):
            join_strings(['u"a"', 'U"b"'])

    @harness.cases("text", ["1.2.3", "0xEp+q", "1e", "0x1", "'\\q'"])
    def test_a_bad_constant_is_an_error_and_not_a_crash(self, text):
        try:
            if text.startswith("'"):
                decode_char(text)
            else:
                parse_number(text)
        except LiteralError:
            return
        if text == "0x1":
            return                  # this one IS valid; it is here as a control
        harness.fail(f"{text!r} was accepted")
