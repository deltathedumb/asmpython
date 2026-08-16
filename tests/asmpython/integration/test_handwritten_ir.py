"""Hand-written IR, through the backends.

`docs/BACKENDS.md` tells backend authors to start from hand-written IR rather
than compiled Python -- twenty lines you wrote yourself is a better first test
than a program needing a frontend, a runtime and your backend to all be right
at once. That means hand-written IR is a supported input, and it reaches
places the frontend never does.

The frontend names blocks `entry0`, `then2`, `endif3` -- always with a counter
suffix, so a block is never called `switch` and a function is never called
`double`. Nothing stops hand-written IR from doing either, and the C backend
emitted both verbatim: `switch:;` and `r7 = double(r1);`, syntax errors in
generated code pointing at lines nobody wrote.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from io import StringIO

from tests import harness

from asmpython import target as target_registry
from asmpython.backend import get as get_backend, load_builtin
from asmpython.ir import verify
from asmpython.ir.interpreter import Interpreter
from asmpython.ir.printer import parse_module, print_module

HAS_CC = shutil.which("gcc") or shutil.which("cc")
HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"

#: Every name here is a C keyword or otherwise hostile to a C identifier: the
#: function, its parameter's block labels, and the callee.
KEYWORD_IR = """\
module keywords

func double(%0: i64) -> i64 {
entry:
    %1 = i64.const 2
    %2 = i64.mul %0, %1
    ret %2
}

func register(%0: i64) -> i64 {
switch:
    %1 = i64.const 1
    %2 = i64.add %0, %1
    ret %2
}

export func main() -> i64 {
default:
    %0 = i64.const 10
    %1 = i64.call register(%0)
    %2 = i64.call double(%1)
    %3 = i1.const 1
    branch %3, const, volatile
const:
    jump volatile
volatile:
    ret %2
}
"""

SIMPLE_IR = """\
module simple

export func main() -> i64 {
entry:
    %0 = i64.const 6
    %1 = i64.const 7
    %2 = i64.mul %0, %1
    ret %2
}
"""


def interpret(module) -> int:
    out = StringIO()
    return Interpreter(module, out=out).run("main")


def compile_and_run(module, backend_name: str, target_name: str, tmp_path):
    load_builtin()
    # Round-tripped through the text on the way in: a backend receives
    # whatever the printer/parser pair produced, so testing the object graph
    # directly would skip the stage most likely to have lost something.
    module = parse_module(print_module(module))
    artifacts = get_backend(backend_name).emit(
        module, target_registry.get(target_name))
    inputs = []
    for name, data in artifacts.items():
        p = tmp_path / name
        p.write_bytes(data)
        inputs.append(str(p))
    if backend_name != "c":
        from asmpython.link import write_runtime
        inputs.append(str(write_runtime(tmp_path)))
    exe = tmp_path / "out.exe"
    built = subprocess.run([HAS_CC, *inputs, "-o", str(exe)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True)


class TestItParsesAndVerifies:
    def test_hand_written_ir_round_trips(self):
        module = parse_module(KEYWORD_IR)
        verify(module)
        assert print_module(parse_module(print_module(module))) == \
            print_module(module)

    def test_the_interpreter_runs_it(self):
        assert interpret(parse_module(KEYWORD_IR)) == 22   # (10 + 1) * 2

    def test_a_simple_module_too(self):
        assert interpret(parse_module(SIMPLE_IR)) == 42


@harness.skip_if(not HAS_CC, reason="no C compiler available")
class TestBackendsSurviveHostileNames:
    """Functions and blocks named after C keywords."""

    @harness.cases("backend", ["c", "x86-64"])
    def test_it_compiles_and_returns_the_right_value(self, backend, tmp_path):
        module = parse_module(KEYWORD_IR)
        verify(module)
        target = "c" if backend == "c" else HOST_TARGET
        ran = compile_and_run(module, backend, target, tmp_path)
        assert ran.returncode == interpret(parse_module(KEYWORD_IR)) & 0xFF

    def test_both_backends_agree_with_the_interpreter(self, tmp_path):
        expected = interpret(parse_module(KEYWORD_IR)) & 0xFF
        for backend, target in (("c", "c"), ("x86-64", HOST_TARGET)):
            d = tmp_path / backend.replace("-", "_")
            d.mkdir()
            ran = compile_and_run(parse_module(KEYWORD_IR), backend, target, d)
            assert ran.returncode == expected, f"{backend} disagreed"


class TestTheNameManglingItself:
    def test_c_keywords_are_renamed(self):
        from asmpython.backends.c.emit import _cname
        for keyword in ("double", "int", "switch", "default", "register",
                        "const", "volatile", "signed", "goto", "return"):
            assert _cname(keyword) != keyword, f"{keyword} emitted verbatim"

    def test_ordinary_names_are_left_alone(self):
        from asmpython.backends.c.emit import _cname
        for name in ("square", "total", "my_func", "f2"):
            assert _cname(name) == name

    def test_a_leading_digit_is_handled(self):
        from asmpython.backends.c.emit import _cname
        assert not _cname("2fast")[0].isdigit()

    def test_labels_cannot_collide_with_a_keyword(self):
        from asmpython.backends.c.emit import _label
        for keyword in ("switch", "default", "const", "volatile", "if"):
            assert _label(keyword) != keyword

    def test_the_generated_c_contains_no_bare_keyword_label(self, tmp_path):
        load_builtin()
        module = parse_module(KEYWORD_IR)
        source = get_backend("c").emit(
            module, target_registry.get("c"))["out.c"].decode()
        for bad in ("\nswitch:;", "\ndefault:;", "\nconst:;", "\nvolatile:;"):
            assert bad not in source, f"emitted {bad.strip()!r} as a label"
