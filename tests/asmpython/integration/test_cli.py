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

import pytest

HAS_CC = shutil.which("gcc") or shutil.which("cc")
SRC = Path(__file__).resolve().parents[3] / "src"

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
    return subprocess.run([sys.executable, "-m", "asmpython", *args],
                          capture_output=True, text=True, env=env, cwd=cwd)


@pytest.fixture
def program(tmp_path) -> Path:
    path = tmp_path / "prog.py"
    path.write_text(PROGRAM, encoding="utf-8")
    return path


class TestItRunsAtAll:
    """`python -m asmpython` must reach its own CLI.

    Not a trivial assertion here: the package shares its name with the
    pre-rewrite compiler in `legacy/`, and for one commit `python -m
    asmpython` silently ran the wrong program.
    """

    def test_help_names_this_compiler(self):
        r = run_cli("--help")
        assert r.returncode == 0, r.stderr
        assert "asmpython" in r.stdout
        # `toolchains` exists only in the rewrite. Without a check like this
        # the test passes against the legacy CLI, which is also `asmpython`.
        assert "toolchains" in r.stdout, "this is the old CLI, not the new one"

    def test_build_help_lists_the_build_flags(self):
        r = run_cli("build", "--help")
        assert r.returncode == 0, r.stderr
        for flag in ("--emit-ir", "--emit", "--target", "--toolchain",
                     "--backend", "--workdir"):
            assert flag in r.stdout, f"{flag} missing from `build --help`"

    @pytest.mark.parametrize("command", [
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


@pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
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
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
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
