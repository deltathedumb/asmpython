"""Running a program twice in one process gives the same answer twice.

THE RULE THIS PINS: anything a compiled program keeps in a FILE STATIC belongs
on the host. The C keeps its tables in statics and each compiled program is
its own process, so one table is one program. `objects_host.py` runs MANY
programs in one process, and a module-level table hands the second one the
first's rows.

It has been broken four times -- `_types`, `_forms`, `user_exc`, and this
session's position table and task registry -- and it fails in the worst
possible way: a WRONG ANSWER on the second run, which looks like a flaky test
because whether two runs share a worker decides whether anyone sees it. The
position table showed as a traceback reporting a line six too small.

CHECKED BY MUTATION, which is the only way to know a test of this shape works:
putting the shared table back makes `test_after_a_different_program` fail, and
putting the shared TASK registry back makes it HANG -- run two keeps stepping a
task run one never finished. A hang is a poor failure mode and it is the honest
one here: the leak is a live coroutine, not a stale number.

WHAT IS NOT COVERED: `live_agens`. Every `asyncio.run` drains it on the way
out, so a program cannot leave one behind for the next -- it is on the host for
the rule rather than for a bug anyone can reach.
"""
from __future__ import annotations

import pathlib
import tempfile
from io import StringIO
from pathlib import Path

from tests import harness

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter

#: Programs that touch state the runtime keeps for the length of ONE run.
#: Named for what they would leak.
PROGRAMS = {
    "traceback positions": """
try:
    (1).missing
except AttributeError as e:
    print(e.__traceback__.tb_lineno)
    print(e.__traceback__.tb_frame.f_code.co_name)
""",
    "positions in a function": """
def inner():
    return (1).missing

try:
    inner()
except AttributeError as e:
    print(e.__traceback__.tb_lineno, e.__traceback__.tb_frame.f_code.co_name)
""",
    "the task registry": """
import asyncio

async def val(v):
    await asyncio.sleep(0)
    return v

async def main():
    t = asyncio.create_task(val(7))
    return await t

print(asyncio.run(main()))
""",
    "a task group": """
import asyncio

async def val(v):
    await asyncio.sleep(0)
    return v

async def main():
    async with asyncio.TaskGroup() as tg:
        made = [tg.create_task(val(n)) for n in (1, 2)]
    return [t.result() for t in made]

print(asyncio.run(main()))
""",
    "abandoned async generators": """
import asyncio

async def counting():
    try:
        for i in range(5):
            await asyncio.sleep(0)
            yield i
    finally:
        print("closed")

async def main():
    async for v in counting():
        if v == 1:
            break
    return "done"

print(asyncio.run(main()))
""",
    "the virtual clock": """
import asyncio

async def slow():
    await asyncio.sleep(10)
    return "never"

async def main():
    try:
        await asyncio.wait_for(slow(), timeout=0.01)
    except asyncio.TimeoutError:
        return "timeout"

print(asyncio.run(main()))
""",
    # A TASK THE LOOP NEVER FINISHED, which is what makes a shared registry
    # observable: a finished one is skipped, so every other program here
    # leaves nothing behind to leak.
    "an unfinished task": """
import asyncio

log = []

async def slow():
    await asyncio.sleep(5)
    log.append("late")
    return 1

async def main():
    asyncio.create_task(slow())
    await asyncio.sleep(0)
    return "done"

print(asyncio.run(main()), log)
""",
    "interned types": """
print(type(1) is int, type("a") is str)
print(isinstance(KeyError("k"), LookupError))
""",
    "user exception classes": """
class AppError(Exception):
    def __init__(self, code):
        super().__init__(str(code))
        self.code = code

try:
    raise AppError(404)
except AppError as e:
    print(e.code, str(e), type(e).__name__)
""",
}


#: What a subprocess runs: each source in turn, printing only the LAST one's
#: output. Written out rather than imported, because the point is a FRESH
#: process whose first program is the one this chooses.
_DRIVER = """
import sys, tempfile
from io import StringIO
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter
out = ""
for text in sys.argv[2:]:
    path = Path(tempfile.mkdtemp()) / "prog.py"
    path.write_text(text, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path), sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    buf = StringIO()
    Interpreter(result.module, out=buf).run("main")
    out = buf.getvalue()
sys.stdout.write(out)
"""


#: THE PROGRAM THAT GOES FIRST in the order-dependence test. It has to touch
#: the same state the program under test does -- a first program that records
#: no positions shifts no indices, and the test then passes against the very
#: bug it exists for. Five statements before the raise, so the shift is
#: unmistakable.
_GOES_FIRST = """
a = 1
b = 2
c = 3
d = a + b + c
try:
    raise ValueError("the first program")
except ValueError as first:
    print(first.__traceback__ is not None, d)
"""


def _in_subprocess(sources):
    """Run each source in one FRESH process, and answer the LAST one's output.

    A subprocess because the thing being controlled is WHICH PROGRAM RAN
    FIRST, and inside this one something already has.
    """
    import subprocess
    import sys as _sys
    import asmpython
    where = str(pathlib.Path(asmpython.__file__).resolve().parent.parent)
    done = subprocess.run([_sys.executable, "-c", _DRIVER, where, *sources],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def _run_once(source: str) -> list[str]:
    """Compile and run through a FRESH interpreter, as a new process would."""
    path = Path(tempfile.mkdtemp()) / "prog.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path), sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    out = StringIO()
    Interpreter(result.module, out=out).run("main")
    return out.getvalue().split("\n")[:-1]


@harness.cases("name", sorted(PROGRAMS))
class TestASecondRunIsNotTheFirstsLeftovers:
    def test_twice_in_one_process(self, name):
        """The cheap half: state that GROWS between runs -- a task list that
        gets stepped again, an abandoned generator closed a second time --
        shows up here without a subprocess."""
        source = PROGRAMS[name].strip() + "\n"
        first = _run_once(source)
        second = _run_once(source)
        assert first == second, (name, first, second)
        assert first, f"{name} printed nothing, so it proves nothing"

    def test_after_a_different_program(self, name):
        """AND THE ANSWER MUST NOT DEPEND ON WHAT RAN BEFORE IT.

        This is the shape that catches a shared APPEND-ONLY table, and running
        the same program twice is not: such a table resolves index `i` to
        whatever the FIRST program put there, so a program compared with
        ITSELF agrees however broken the sharing is. What breaks it is a
        DIFFERENT program having gone first -- so the run happens in a
        subprocess, which is the only way to control what did.
        """
        source = PROGRAMS[name].strip() + "\n"
        first = _GOES_FIRST.strip() + "\n"
        assert _in_subprocess([source]) == _in_subprocess([first, source])
