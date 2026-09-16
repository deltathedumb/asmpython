"""The command line, driven as a user drives it.

This exists because the CLI had no tests and two bugs got through in one
session: an edit to `cmd_build` that silently did not apply, so `-o prog.exe`
wrote C source into a file named `.exe`; and a syntax error in `cmd_targets`
that nothing caught until the command was typed. Both are invisible to unit
tests of the pipeline -- the pipeline was fine. What was broken was the layer
between the user and it.

So these run the CLI in a subprocess and assert on exit codes, stdout and the
files that appear. Slower than calling `main()` directly and worth it: an
`argparse` mistake, a bad `__main__`, an f-string that does not parse, and a
`sys.exit` in the wrong branch are all things only a real invocation sees.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from tests import harness
from tests.harness import snapshot

HAS_CC = shutil.which("gcc") or shutil.which("cc")
#: THE TREE THIS RUN IS MEASURING, not `src/` unconditionally: the runner
#: works from a snapshot so `src/` can be edited mid-run, and a subprocess
#: started here has to compile with the same code the rest of the run did.
#: See `tests/harness/snapshot.py`.
SRC = snapshot.current(Path(__file__).resolve().parents[3])

#: `double` is deliberate. It is a C keyword, and emitting it verbatim made
#: the C backend produce `r7 = double(r1);` -- a syntax error in generated
#: code, pointing at a line the user never wrote. A source language has no
#: reason to avoid C's keywords.
PROGRAM = """\
def double(n: int) -> int:
    return n * 2

def main() -> int:
    total: int = 0
    for i in range(5):
        total = total + double(i)
    print(total)
    return 0
"""

BAD_PROGRAM = """\
def main() -> int:
    return undefined_name
"""


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the CLI the way a user would, in its own process."""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "uasm", *args],
                          capture_output=True, text=True, env=env, cwd=cwd)


@harness.fixture
def program(tmp_path) -> Path:
    path = tmp_path / "prog.py"
    path.write_text(PROGRAM, encoding="utf-8")
    return path


class TestItRunsAtAll:
    """`python -m uasm` must reach its own CLI.

    Not a trivial assertion here: the package shares its name with the
    pre-rewrite compiler in `legacy/`, and for one commit `python -m
    uasm` silently ran the wrong program.
    """

    def test_help_names_this_compiler(self):
        r = run_cli("--help")
        assert r.returncode == 0, r.stderr
        assert "uasm" in r.stdout
        # `toolchains` exists only in the rewrite. Without a check like this
        # the test passes against the legacy CLI, which is also `uasm`.
        assert "toolchains" in r.stdout, "this is the old CLI, not the new one"

    def test_build_help_lists_the_build_flags(self):
        r = run_cli("build", "--help")
        assert r.returncode == 0, r.stderr
        for flag in ("--emit-ir", "--emit", "--target", "--toolchain",
                     "--backend", "--workdir"):
            assert flag in r.stdout, f"{flag} missing from `build --help`"

    @harness.cases("command", [
        "ops", "types", "passes", "backends", "frontends", "targets",
        "toolchains",
    ])
    def test_every_informational_command_runs(self, command):
        r = run_cli(command)
        assert r.returncode == 0, f"{command}: {r.stderr}"
        assert r.stdout.strip(), f"{command} printed nothing"

    def test_no_arguments_is_a_usage_error_not_a_traceback(self):
        r = run_cli()
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
        assert "usage:" in (r.stdout + r.stderr).lower()


class TestListings:
    def test_targets_lists_the_builtins_and_host(self):
        out = run_cli("targets").stdout
        for name in ("x86_64-linux", "x86_64-windows", "c", "host"):
            assert name in out, f"{name} missing from `targets`"

    def test_targets_shows_abi_and_format(self):
        """The fields a backend reads. Sniffing the name instead was a bug."""
        out = run_cli("targets").stdout
        assert "abi=win64" in out and "abi=sysv" in out
        assert "format=coff" in out and "format=elf" in out

    def test_toolchains_lists_both(self):
        out = run_cli("toolchains").stdout
        assert "cc" in out and "none" in out

    def test_backends_lists_both(self):
        out = run_cli("backends").stdout
        assert "x86-64" in out and "c" in out

    def test_ops_covers_the_whole_instruction_set(self):
        out = run_cli("ops").stdout
        assert "39 opcodes" in out
        for op in ("add", "call", "branch", "ftoi", "ret"):
            assert op in out, f"{op} missing from `ops`"


class TestCheck:
    def test_a_good_program_checks_clean(self, program):
        r = run_cli("check", str(program))
        assert r.returncode == 0, r.stderr
        assert "ok:" in r.stdout

    def test_a_bad_program_fails_with_a_diagnostic(self, tmp_path):
        path = tmp_path / "bad.py"
        path.write_text(BAD_PROGRAM, encoding="utf-8")
        r = run_cli("check", str(path))
        assert r.returncode == 1
        assert "E0031" in (r.stdout + r.stderr)
        assert "Traceback" not in r.stderr

    def test_a_missing_file_is_a_diagnostic(self, tmp_path):
        r = run_cli("check", str(tmp_path / "nope.py"))
        assert r.returncode == 1
        assert "Traceback" not in r.stderr

    def test_check_writes_nothing(self, program, tmp_path):
        before = set(tmp_path.iterdir())
        run_cli("check", str(program))
        assert set(tmp_path.iterdir()) == before


class TestRun:
    def test_it_executes_in_the_interpreter(self, program):
        r = run_cli("run", str(program))
        assert r.returncode == 0, r.stderr
        assert "20" in r.stdout

    def test_it_runs_ir_text_too(self, program, tmp_path):
        """`--emit-ir` writes text `run` accepts. That round trip is what
        makes a backend debuggable, so it is checked rather than assumed."""
        ir = tmp_path / "prog.ir"
        assert run_cli("build", str(program), "--emit-ir", "-o",
                       str(ir)).returncode == 0
        assert ir.exists()
        r = run_cli("run", str(ir))
        assert r.returncode == 0, r.stderr
        assert "20" in r.stdout

    def test_the_entry_s_return_value_is_the_exit_status(self, tmp_path):
        """The frontend's own rule, honoured on the path that defines it.

        `E0009`'s note says the entry's return value BECOMES the process exit
        code, and the entry wrapper each backend emits makes that true for
        every compiled backend: `return (int)ir_main();`. This path used
        to report 0 regardless, so `return 7` exited 7 when compiled and 0
        when interpreted -- the oracle every backend is measured against
        disagreeing with all of them about a documented behaviour, which is a
        defect in the reference rather than a quirk of it.
        """
        path = tmp_path / "seven.py"
        path.write_text("def main() -> int:\n    return 7\n", encoding="utf-8")
        assert run_cli("run", str(path)).returncode == 7

    def test_an_entry_override_is_an_answer_not_a_status(self, tmp_path):
        """`--entry` runs something that is not a program.

        Its result is a VALUE -- `--entry fib prog.py 30` is a question -- and
        turning it into an exit status would report 40 for 832040, because
        that is what the low eight bits happen to be. Only the default entry
        is a program.
        """
        path = tmp_path / "fib.py"
        path.write_text(
            "def fib(n: int) -> int:\n"
            "    if n < 2:\n"
            "        return n\n"
            "    return fib(n - 1) + fib(n - 2)\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n", encoding="utf-8")
        r = run_cli("run", str(path), "--entry", "fib", "30", "--print-result")
        assert r.returncode == 0, r.stderr
        assert "832040" in r.stdout

    def test_invalid_ir_is_reported_not_raised(self, tmp_path):
        bad = tmp_path / "bad.ir"
        bad.write_text("func i64 @main() {\nentry:\n  ret\n}\n", encoding="utf-8")
        r = run_cli("run", str(bad))
        assert r.returncode != 0
        assert "Traceback" not in r.stderr


class TestBuild:
    def test_emit_ir_to_stdout(self, program):
        r = run_cli("build", str(program), "--emit-ir")
        assert r.returncode == 0, r.stderr
        assert "func" in r.stdout and "main" in r.stdout

    def test_emit_writes_artifacts_and_does_not_link(self, program, tmp_path):
        out = tmp_path / "out"
        r = run_cli("build", str(program), "--emit", "-o", str(out))
        assert r.returncode == 0, r.stderr
        assert (tmp_path / "out").exists()

    def test_optimise_changes_the_ir(self, program):
        plain = run_cli("build", str(program), "--emit-ir").stdout
        opt = run_cli("build", str(program), "--emit-ir", "-O").stdout
        assert plain != opt, "-O had no effect on the IR"

    def test_an_unknown_backend_is_a_clean_error(self, program):
        r = run_cli("build", str(program), "--backend", "nonexistent")
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
        assert "nonexistent" in (r.stdout + r.stderr)

    def test_an_unknown_target_is_a_clean_error(self, program):
        r = run_cli("build", str(program), "--target", "vax-bsd")
        assert r.returncode != 0
        assert "Traceback" not in r.stderr

    def test_an_unknown_toolchain_is_a_clean_error(self, program):
        r = run_cli("build", str(program), "--toolchain", "magic")
        assert r.returncode != 0
        assert "Traceback" not in r.stderr

    def test_time_passes_reports(self, program):
        r = run_cli("build", str(program), "--emit-ir", "-O", "--time-passes")
        assert r.returncode == 0, r.stderr
        assert "constfold" in (r.stdout + r.stderr)

    def test_show_spans_annotates(self, program):
        plain = run_cli("build", str(program), "--emit-ir").stdout
        spanned = run_cli("build", str(program), "--emit-ir",
                          "--show-spans").stdout
        assert len(spanned) > len(plain)


@harness.needs("cc")
class TestBuildProducesAProgram:
    """`-o prog.exe` must produce something that runs.

    The bug: an edit that did not apply left the artifact-writing branch in
    place, so `-o prog.exe` wrote C source to a file called `.exe`. It
    reported "wrote prog.exe (7704 bytes)" and exited 0.
    """

    def build_and_run(self, program, tmp_path, *extra):
        exe = tmp_path / "prog.exe"
        r = run_cli("build", str(program), "-o", str(exe), *extra)
        assert r.returncode == 0, r.stderr + r.stdout
        assert exe.exists(), "no output file"
        # UTF-8: this is the PROGRAM's output, and a str is stored as
        # UTF-8 by this runtime -- the locale codec turns every
        # non-ASCII character into mojibake. See the conformance shim.
        ran = subprocess.run([str(exe)], capture_output=True, text=True,
                             encoding="utf-8")
        return ran

    def test_default_backend(self, program, tmp_path):
        ran = self.build_and_run(program, tmp_path)
        assert ran.stdout.strip() == "20"
        assert ran.returncode == 0

    def test_x86_64_backend(self, program, tmp_path):
        ran = self.build_and_run(program, tmp_path, "--backend", "x86-64")
        assert ran.stdout.strip() == "20"

    def test_optimised(self, program, tmp_path):
        ran = self.build_and_run(program, tmp_path, "-O")
        assert ran.stdout.strip() == "20"

    def test_the_output_is_an_executable_not_source(self, program, tmp_path):
        """What the missed edit produced: the artifact, renamed."""
        exe = tmp_path / "prog.exe"
        run_cli("build", str(program), "-o", str(exe))
        head = exe.read_bytes()[:64]
        assert b"#include" not in head and b"Generated by" not in head, \
            "-o wrote backend source, not a program"

    def test_verbose_shows_the_commands(self, program, tmp_path):
        r = run_cli("build", str(program), "-o", str(tmp_path / "p.exe"), "-v")
        assert r.returncode == 0
        assert "$ " in r.stderr, "--verbose printed no commands"

    def test_intermediates_land_in_the_workdir(self, program, tmp_path):
        work = tmp_path / "scratch"
        r = run_cli("build", str(program), "-o", str(tmp_path / "p.exe"),
                    "--workdir", str(work))
        assert r.returncode == 0, r.stderr
        assert work.is_dir() and any(work.iterdir())

    def test_a_diagnostic_program_produces_no_executable(self, tmp_path):
        path = tmp_path / "bad.py"
        path.write_text(BAD_PROGRAM, encoding="utf-8")
        exe = tmp_path / "bad.exe"
        r = run_cli("build", str(path), "-o", str(exe))
        assert r.returncode == 1
        assert not exe.exists(), "a failed build left an executable behind"


#: A program that does nothing but report its own command line. Module-level
#: rather than inside a `def main()`: an untyped entry is a definition and not
#: a program here, so the call has to be written.
ARGV_PROGRAM = """\
import sys

print("count:", len(sys.argv))
print("name is a str:", isinstance(sys.argv[0], str))
print("tail:", sys.argv[1:])
"""


class TestTheCommandLineReachesTheProgram:
    """`sys.argv`, from the two places a program can be started.

    THE WORDS CAME FROM NOWHERE. `objects/hostsvc.py` declared
    `host_arg_count` and `host_arg_get` and both answered from statics that
    nothing ever assigned, so every compiled program saw an empty command
    line and `uasm run`'s own trailing arguments went to an entry that
    did not take any. A program could be given arguments by either route and
    read none of them.

    THE TWO ROUTES MUST AGREE, which is why both are here: a binary gets its
    words from C's `main` and the interpreter gets them from this CLI, and a
    program compiled one way and run the other has to see the same list.
    """

    def test_run_passes_its_trailing_arguments(self, tmp_path):
        path = tmp_path / "argv.py"
        path.write_text(ARGV_PROGRAM, encoding="utf-8")
        r = run_cli("run", str(path), "alpha", "beta")
        assert r.returncode == 0, r.stderr
        assert "count: 3" in r.stdout, r.stdout
        assert "tail: ['alpha', 'beta']" in r.stdout, r.stdout

    def test_run_names_the_source_first(self, tmp_path):
        """`argv[0]` is the source as written, as CPython's is the script."""
        path = tmp_path / "argv.py"
        path.write_text("import sys\nprint(sys.argv[0])\n", encoding="utf-8")
        r = run_cli("run", str(path))
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == str(path), r.stdout

    def test_run_with_no_arguments_still_has_a_name(self, tmp_path):
        path = tmp_path / "argv.py"
        path.write_text(ARGV_PROGRAM, encoding="utf-8")
        r = run_cli("run", str(path))
        assert r.returncode == 0, r.stderr
        assert "count: 1" in r.stdout, r.stdout
        assert "tail: []" in r.stdout, r.stdout

    def test_an_entry_that_takes_parameters_still_gets_integers(self,
                                                               tmp_path):
        """The older meaning of the trailing words, which this must not break.

        `--entry fib prog.py 30` calls `fib(30)`. Which reading applies is
        decided by the ENTRY: an IR function's parameters are i64, so one
        that declares some wants numbers, and one that declares none can only
        be reading them as `sys.argv`.
        """
        path = tmp_path / "fib.py"
        path.write_text(
            "def fib(n: int) -> int:\n"
            "    if n < 2:\n"
            "        return n\n"
            "    return fib(n - 1) + fib(n - 2)\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n", encoding="utf-8")
        r = run_cli("run", str(path), "--entry", "fib", "10",
                    "--print-result")
        assert r.returncode == 0, r.stderr
        assert "55" in r.stdout, r.stdout

    @harness.needs("cc")
    def test_a_compiled_binary_reads_its_own_arguments(self, tmp_path):
        """The half only a real process can show.

        C offers a program its command line in `main` and nowhere else, so
        the entry wrapper the C backend emits has to take it and hand it on
        -- see `apy_host_args_take` in `objects/hostsvc.py`. Nothing else in
        the build can supply it, and a unit test of the emitter would only
        assert that a string was written.
        """
        path = tmp_path / "argv.py"
        path.write_text(ARGV_PROGRAM, encoding="utf-8")
        exe = tmp_path / "argv.exe"
        r = run_cli("build", str(path), "-o", str(exe))
        assert r.returncode == 0, r.stderr + r.stdout
        ran = subprocess.run([str(exe), "alpha", "beta"],
                             capture_output=True, text=True, encoding="utf-8")
        assert ran.returncode == 0, ran.stderr
        assert "count: 3" in ran.stdout, ran.stdout
        assert "tail: ['alpha', 'beta']" in ran.stdout, ran.stdout
        # THE PATH IT WAS INVOKED BY, which is what a shell passes and what
        # CPython puts in `argv[0]` for a script.
        assert str(exe) in ran.stdout or "name is a str: True" in ran.stdout

    @harness.needs("cc")
    def test_a_compiled_binary_with_no_arguments_has_a_name(self, tmp_path):
        path = tmp_path / "argv.py"
        path.write_text(ARGV_PROGRAM, encoding="utf-8")
        exe = tmp_path / "argv.exe"
        assert run_cli("build", str(path), "-o", str(exe)).returncode == 0
        ran = subprocess.run([str(exe)], capture_output=True, text=True,
                             encoding="utf-8")
        assert "count: 1" in ran.stdout, ran.stdout
        assert "tail: []" in ran.stdout, ran.stdout

    @harness.needs("cc")
    def test_an_argument_longer_than_the_first_buffer_survives(self, tmp_path):
        """The growing buffer, which is the one thing `_read_arg` can get
        wrong silently: `host_arg_get` answers the length it NEEDED, and a
        caller that took that for the length it WROTE keeps a truncated
        word."""
        path = tmp_path / "argv.py"
        path.write_text(
            "import sys\nprint(len(sys.argv[1]), sys.argv[1][-3:])\n",
            encoding="utf-8")
        exe = tmp_path / "argv.exe"
        assert run_cli("build", str(path), "-o", str(exe)).returncode == 0
        long = "z" * 700 + "end"
        ran = subprocess.run([str(exe), long], capture_output=True, text=True,
                             encoding="utf-8")
        assert ran.stdout.strip() == "703 end", ran.stdout
        r = run_cli("run", str(path), long)
        assert r.stdout.strip() == "703 end", r.stdout

    @harness.needs("cc")
    def test_an_argument_that_is_not_utf_8_survives_as_bytes(self, tmp_path):
        """A command line is BYTES, and a file name typed in another encoding
        reaches `argv` verbatim. CPython decodes it with `surrogateescape` --
        that is what `os.fsdecode` is -- so it re-encodes to what came in. A
        plain `decode("utf-8")` raised instead, and raised in the MODULE BODY,
        which killed every program that imported `sys` rather than only the
        ones that read `argv`."""
        path = tmp_path / "argv.py"
        path.write_text(
            "import sys\n"
            "a = sys.argv[1]\n"
            "print(len(a), [ord(c) for c in a])\n"
            "print(a.encode('utf-8', 'surrogateescape'))\n",
            encoding="utf-8")
        exe = tmp_path / "argv.exe"
        assert run_cli("build", str(path), "-o", str(exe)).returncode == 0
        raw = b"a\xff\xfeb"
        want = "4 [97, 56575, 56574, 98]\nb'a\\xff\\xfeb'"
        ran = subprocess.run([str(exe), raw], capture_output=True)
        assert ran.stdout.decode().strip() == want, ran.stdout
        # AND THE INTERPRETER AGREES, which is the whole point: the bytes go
        # out through `os.fsencode` in `objects/hostsvc_host.py` and come back
        # through the same handler.
        r = subprocess.run(
            [sys.executable, "-m", "uasm", "run", str(path), raw],
            capture_output=True, env={**os.environ, "PYTHONPATH": str(SRC)})
        assert r.stdout.decode().strip() == want, r.stdout
