"""Input the compiler should refuse rather than die on.

One property, in two places: a compiler answers or reports, and never shows
the user its own stack. A traceback says "you found a bug in the compiler"
when the truth is usually "that input is not supported", and the two send
people to completely different places.

Both cases here were found by generating hostile input rather than imagining
it -- a long expression, and 30000 mutations of a valid IR module.
"""
from __future__ import annotations

import random

from tests import harness

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.printer import ParseError, parse_module

VALID_IR = """\
module m

global t = "\\01\\02" readonly

func helper(%0: i64) -> i64 {
entry:
    %1 = i64.const 3
    %2 = i64.mul %0, %1
    ret %2
}

export func main() -> i64 {
entry:
    %0 = i64.const 7
    %1 = i64.call @helper(%0)
    %2 = f64.const 1.5
    %3 = i64.ftoi %2
    %4 = i64.add %1, %3
    i64.switch %4, default other [0 -> other]
other:
    ret %4
}
"""

MUTATION_CHARS = list("0123456789abcdefghij%@[]{}(),.:-> \n\t=\"")


def mutate(text: str, rng: random.Random) -> str:
    chars = list(text)
    for _ in range(rng.randint(1, 8)):
        i = rng.randrange(len(chars))
        action = rng.random()
        if action < 0.4:
            chars[i] = rng.choice(MUTATION_CHARS)
        elif action < 0.7:
            chars[i] = ""
        else:
            chars[i] = chars[i] + rng.choice(MUTATION_CHARS)
    return "".join(chars)


class TestTheIRParserOnlyRaisesParseError:
    """Corrupt IR text must produce a ParseError naming the line.

    The parser is full of `int(token[1:])`, `parts[1]` and `T.parse(word)`,
    and every one is a way for corrupt text to raise something else --
    reaching `asmpython run prog.ir` as a traceback. Wrapping each site as it
    turned up would have left the next one, so the guard is per LINE.
    """

    @harness.cases("seed", range(6))
    def test_mutations_never_raise_anything_else(self, seed):
        rng = random.Random(seed)
        for _ in range(500):
            text = mutate(VALID_IR, rng)
            try:
                parse_module(text)
            except ParseError:
                pass
            except Exception as exc:                       # noqa: BLE001
                harness.fail(f"{type(exc).__name__}: {exc}\n--- input ---\n"
                            f"{text}")

    def test_a_parse_error_names_the_line(self):
        with harness.raises(ParseError) as exc:
            parse_module("module m\n\nfunc f() -> i64 {\nentry:\n"
                         "    %0 = i64.frobnicate %1\n    ret %0\n}\n")
        assert "line 5" in str(exc.value)

    def test_truncated_input_is_a_parse_error(self):
        for cut in range(1, len(VALID_IR), 37):
            try:
                parse_module(VALID_IR[:cut])
            except ParseError:
                pass
            except Exception as exc:                       # noqa: BLE001
                harness.fail(f"truncating at {cut} raised "
                            f"{type(exc).__name__}: {exc}")

    def test_a_valid_module_still_parses_completely(self):
        """The fuzz above is vacuous if the parser stops parsing.

        Adding the guard, I indented the append under a `raise` and made it
        dead code. Every function then parsed as empty, no mutation could
        provoke anything, and the fuzz reported a clean 30000 -- while the
        parser silently produced nothing. So this counts instructions, not
        just functions: an empty module is exactly what the broken version
        returned.
        """
        module = parse_module(VALID_IR)
        assert module.function("main") is not None
        assert module.function("helper") is not None
        assert len(module.globals) == 1
        instructions = sum(len(b.instructions)
                           for f in module.functions for b in f.blocks)
        assert instructions == 10, f"parsed {instructions} instructions, not 10"

    def test_the_fuzz_corpus_is_mostly_rejected(self):
        """If nearly everything parsed, the mutations would not be hostile
        and the property would be untested."""
        rng = random.Random(99)
        rejected = 0
        for _ in range(400):
            try:
                parse_module(mutate(VALID_IR, rng))
            except ParseError:
                rejected += 1
        assert rejected > 200, (
            f"only {rejected}/400 mutations were rejected -- either the "
            f"mutations are too gentle or the parser stopped parsing")


class TestDeepExpressions:
    """A long expression is a deep tree, and the frontend walks it
    recursively. `1 + 2 + ... + 999` exhausted the interpreter stack and
    reached the user as a traceback ending in `_binop`."""

    def chain(self, terms: int) -> str:
        return ("def main() -> int:\n    return "
                + " + ".join(str(i) for i in range(terms)) + "\n")

    def compile_text(self, src: str, tmp_path):
        path = tmp_path / "deep.py"
        path.write_text(src, encoding="utf-8")
        sink = DiagnosticSink()
        return compile_source(Options(source=path), sink), sink

    def test_a_moderate_chain_compiles(self, tmp_path):
        result, sink = self.compile_text(self.chain(100), tmp_path)
        assert result.ok, [d.message for d in sink.diagnostics]

    @harness.cases("terms", [2000, 5000])
    def test_an_enormous_chain_is_a_diagnostic(self, terms, tmp_path):
        result, sink = self.compile_text(self.chain(terms), tmp_path)
        assert not result.ok
        assert any(d.code == "E9105" for d in sink.diagnostics), \
            [d.code for d in sink.diagnostics]

    def test_the_diagnostic_says_what_to_do(self, tmp_path):
        _, sink = self.compile_text(self.chain(5000), tmp_path)
        d = next(d for d in sink.diagnostics if d.code == "E9105")
        assert d.helps, "a limit the user can work around needs a suggestion"

    def test_deep_parenthesis_nesting_is_python_s_own_error(self, tmp_path):
        """CPython's parser refuses this before we see it, and its message is
        better than anything we would invent."""
        src = "def main() -> int:\n    return " + "(" * 600 + "1" + ")" * 600
        result, sink = self.compile_text(src, tmp_path)
        assert not result.ok
        assert sink.diagnostics
