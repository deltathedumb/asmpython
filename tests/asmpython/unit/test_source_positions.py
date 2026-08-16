"""PEP 657: what a traceback reports, and what it costs when nobody asks.

TWO PROPERTIES, and the second is the one that makes the first affordable.

The positions have to be TRUE -- a `co_positions()` that yields four-tuples of
nothing would pass the conformance case and lie -- so the line numbers below
are checked against where the statements actually are.

And recording has to be FREE for a program that never looks at a traceback,
because it is a call per statement. The frontend emits it only when the source
mentions `__traceback__` or an attribute reached through one, and the test for
that is counting instructions rather than trusting the rule.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter
from io import StringIO


def _compile(source: str):
    path = Path(tempfile.mkdtemp()) / "prog.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path), sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    return result.module


def _run(source: str) -> list[str]:
    out = StringIO()
    Interpreter(_compile(source), out=out).run("main")
    return out.getvalue().split("\n")[:-1]


def _position_calls(module) -> int:
    return sum(1 for fn in module.functions for block in fn.blocks
               for ins in block.instructions
               if getattr(ins, "sym", None) in ("apy_at", "apy_pos_add"))


class TestNothingIsRecordedUnlessAsked:
    def test_an_ordinary_program_records_none(self):
        """A CALL PER STATEMENT is not a cost to impose on a program that
        never looks at a traceback -- and this counts rather than trusting
        the rule that decides it."""
        module = _compile(
            "x = 1\n"
            "for i in range(3):\n"
            "    x = x + i\n"
            "try:\n"
            "    raise ValueError('v')\n"
            "except ValueError as e:\n"
            "    print(str(e), x)\n")
        assert _position_calls(module) == 0

    def test_no_table_function_is_emitted(self):
        module = _compile("print(1)\n")
        assert not [fn for fn in module.functions
                    if fn.name == "pyf__positions"]

    def test_a_program_that_asks_records(self):
        module = _compile(
            "try:\n"
            "    raise ValueError('v')\n"
            "except ValueError as e:\n"
            "    print(e.__traceback__ is not None)\n")
        assert _position_calls(module) > 0
        assert [fn for fn in module.functions if fn.name == "pyf__positions"]


class TestThePositionsAreTrue:
    def test_the_line_is_where_the_statement_is(self):
        """Not merely present: the number has to be the line the failing
        statement is actually on, which is what makes it worth reporting."""
        assert _run(
            "try:\n"
            "    (1).missing\n"                    # line 2
            "except AttributeError as e:\n"
            "    print(e.__traceback__.tb_lineno)\n") == ["2"]

    def test_each_failure_reports_its_own_line(self):
        assert _run(
            "def where(fn):\n"
            "    try:\n"
            "        fn()\n"
            "    except Exception as e:\n"
            "        return e.__traceback__.tb_lineno\n"
            "\n"
            "try:\n"
            "    d = {}\n"
            "    d['missing']\n"                   # line 9
            "except KeyError as e:\n"
            "    print(e.__traceback__.tb_lineno)\n"
            "try:\n"
            "    n = 1 / 0\n"                      # line 13
            "except ZeroDivisionError as e:\n"
            "    print(e.__traceback__.tb_lineno)\n") == ["9", "13"]

    def test_positions_are_four_tuples_that_span_forward(self):
        assert _run(
            "try:\n"
            "    (1).missing\n"
            "except AttributeError as e:\n"
            "    rows = list(e.__traceback__.tb_frame.f_code.co_positions())\n"
            "    print(len(rows) > 0)\n"
            "    print(all(len(p) == 4 for p in rows))\n"
            "    print(all(p[0] <= p[1] for p in rows))\n"
            "    print(all(p[2] <= p[3] for p in rows))\n"
            "    print(e.__traceback__.tb_lineno in [p[0] for p in rows])\n"
        ) == ["True"] * 5

    def test_the_code_object_names_its_function(self):
        assert _run(
            "def inner():\n"
            "    return (1).missing\n"
            "\n"
            "try:\n"
            "    inner()\n"
            "except AttributeError as e:\n"
            "    print(e.__traceback__.tb_frame.f_code.co_name)\n") == ["inner"]

    def test_an_unraised_exception_has_no_traceback(self):
        """NOT AN EMPTY TUPLE, which is what it used to be -- a program
        telling a caught exception from one it merely built read `is not
        None` and got the wrong answer."""
        assert _run(
            "e = ValueError('never raised')\n"
            "print(e.__traceback__)\n"
            "try:\n"
            "    raise e\n"
            "except ValueError as caught:\n"
            "    print(caught.__traceback__ is not None)\n") == ["None", "True"]

    def test_the_traceback_is_one_frame_deep(self):
        """There is no call stack, so the chain has a single link -- and
        saying so is better than inventing frames the runtime never had."""
        assert _run(
            "try:\n"
            "    (1).missing\n"
            "except AttributeError as e:\n"
            "    print(e.__traceback__.tb_next)\n") == ["None"]
