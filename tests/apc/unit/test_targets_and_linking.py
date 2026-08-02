"""Targets and the link stage.

Two registries with the same job as the backend and frontend ones: keep a list
of platforms out of the compiler, and make the extension path the same path the
built-ins take. A registry whose built-ins bypass it is a registry nobody has
tested.

The end-to-end test at the bottom is the one that matters -- it builds an
actual executable and runs it. Everything above it can pass while `apc build`
still produces something that does not execute, which is what happened: the
compiler emitted correct assembly for months and had never once been linked.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap

import pytest

from apc import link as link_registry
from apc import target as target_registry
from apc.diagnostics import DiagnosticSink
from apc.driver import Options, compile_source
from apc.target import Target

HAS_CC = shutil.which("gcc") or shutil.which("cc")
HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"

PROGRAM = """\
def square(x: int) -> int:
    return x * x

def main() -> int:
    total: int = 0
    for i in range(10):
        if i == 7:
            break
        total = total + square(i)
    print(total)
    print(2 ** 10)
    return 0
"""


class TestTargetRegistry:
    def test_the_builtins_are_registered(self):
        names = target_registry.available()
        assert {"c", "x86_64-linux", "x86_64-windows"} <= set(names)

    def test_aliases_resolve(self):
        assert target_registry.get("win64").name == "x86_64-windows"
        assert target_registry.get("linux").name == "x86_64-linux"

    def test_unknown_target_lists_what_exists(self):
        with pytest.raises(LookupError) as exc:
            target_registry.get("vax-bsd")
        assert "x86_64-linux" in str(exc.value)

    def test_a_third_party_target_registers_the_same_way(self):
        mine = Target("test-machine", arch="test", os="none", abi="sysv",
                      object_format="elf")
        target_registry.register(mine, aliases=("tm",))
        try:
            assert target_registry.get("test-machine") is mine
            assert target_registry.get("tm") is mine
        finally:
            from apc.target import registry
            registry._REGISTRY.pop("test-machine", None)
            registry._ALIASES.pop("tm", None)

    def test_registering_an_existing_name_replaces_it(self):
        """An extension may override a built-in platform."""
        original = target_registry.get("x86_64-linux")
        replacement = Target("x86_64-linux", arch="x86_64", os="linux",
                             abi="sysv", stack_alignment=32)
        target_registry.register(replacement)
        try:
            assert target_registry.get("x86_64-linux").stack_alignment == 32
        finally:
            target_registry.register(original)

    def test_host_matches_the_running_platform(self):
        assert target_registry.host().name == HOST_TARGET


class TestAbiComesFromTheTarget:
    """The field, not the name. Sniffing gave System V to anything unfamiliar."""

    def test_each_builtin_gets_its_declared_abi(self):
        from apc.backends.x86_64.emit import MICROSOFT_X64, SYSTEM_V, abi_for
        assert abi_for(target_registry.get("x86_64-linux")) is SYSTEM_V
        assert abi_for(target_registry.get("x86_64-windows")) is MICROSOFT_X64

    def test_a_windows_abi_under_an_unrelated_name(self):
        """The case name-sniffing got wrong: win64 without "windows" in it."""
        from apc.backends.x86_64.emit import MICROSOFT_X64, abi_for
        odd = Target("uefi-x64", arch="x86_64", os="uefi", abi="win64",
                     object_format="coff")
        assert abi_for(odd) is MICROSOFT_X64

    def test_an_unknown_abi_is_refused_not_guessed(self):
        from apc.backends.x86_64.emit import UnsupportedOperation, abi_for
        exotic = Target("m68k-amiga", arch="m68k", os="amiga", abi="m68k-sysv")
        with pytest.raises(UnsupportedOperation) as exc:
            abi_for(exotic)
        assert "m68k-sysv" in str(exc.value)


class TestToolchainRegistry:
    def test_the_builtins_are_registered(self):
        assert {"cc", "none"} <= set(link_registry.available())

    def test_unknown_toolchain_lists_what_exists(self):
        with pytest.raises(LookupError) as exc:
            link_registry.get("magic")
        assert "cc" in str(exc.value)

    def test_none_writes_artifacts_and_stops(self, tmp_path):
        tc = link_registry.get("none")
        request = link_registry.LinkRequest(
            artifacts={"out.s": b"; nothing\n"},
            target=target_registry.get("x86_64-linux"),
            output=tmp_path / "out", workdir=tmp_path / "work")
        tc.link(request)
        assert (tmp_path / "out" / "out.s").read_bytes() == b"; nothing\n"
        assert request.commands == [], "the null toolchain must run nothing"


class TestRuntime:
    def test_the_entry_symbol_is_shared_not_repeated(self):
        from apc.backend.base import ENTRY_SYMBOL
        from apc.link.runtime import RUNTIME_C, write_runtime
        assert "@ENTRY@" in RUNTIME_C, "the template uses a literal token"

    def test_written_runtime_calls_the_backend_entry_symbol(self, tmp_path):
        from apc.backend.base import ENTRY_SYMBOL
        text = link_registry.write_runtime(tmp_path).read_text()
        assert f"int64_t {ENTRY_SYMBOL}(void)" in text
        assert f"return (int){ENTRY_SYMBOL}()" in text

    def test_c_format_specifiers_survive_substitution(self, tmp_path):
        """`%lld` and `%f` are C, not Python. %-formatting the template raised
        "unsupported format character 'l'" on the first real link."""
        text = link_registry.write_runtime(tmp_path).read_text()
        assert "%lld" in text and "%f" in text

    def test_a_module_that_never_prints_needs_no_runtime(self, tmp_path):
        path = tmp_path / "quiet.py"
        path.write_text("def main() -> int:\n    return 3\n", encoding="utf-8")
        result = compile_source(Options(source=path), DiagnosticSink())
        assert not link_registry.needs_runtime(result.module)


@pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
class TestBuildsSomethingThatRuns:
    """The test the whole link stage exists for."""

    def build(self, tmp_path, backend: str, target: str):
        tmp_path.mkdir(parents=True, exist_ok=True)
        src = tmp_path / "prog.py"
        src.write_text(PROGRAM, encoding="utf-8")
        exe = tmp_path / "prog.exe"
        sink = DiagnosticSink()
        result = compile_source(Options(
            source=src, output=exe, backend=backend,
            target=target_registry.get(target), link=True,
            workdir=tmp_path / "work"), sink)
        assert result.ok, [d.message for d in sink.diagnostics]
        assert result.program is not None, "no program was produced"
        return result

    def run(self, exe) -> tuple[list[str], int]:
        proc = subprocess.run([str(exe)], capture_output=True, text=True)
        return proc.stdout.split("\n")[:-1], proc.returncode

    def test_the_c_backend_produces_a_working_program(self, tmp_path):
        result = self.build(tmp_path, "c", "c")
        out, code = self.run(result.program)
        assert out == ["91", "1024"]
        assert code == 0

    def test_the_x86_64_backend_produces_a_working_program(self, tmp_path):
        result = self.build(tmp_path, "x86-64", HOST_TARGET)
        out, code = self.run(result.program)
        assert out == ["91", "1024"]
        assert code == 0

    def test_both_backends_agree(self, tmp_path):
        c = self.build(tmp_path / "c", "c", "c")
        native = self.build(tmp_path / "n", "x86-64", HOST_TARGET)
        assert self.run(c.program) == self.run(native.program)

    def test_a_missing_toolchain_is_a_diagnostic_not_a_traceback(self, tmp_path):
        """Not having an assembler is an ordinary state for a machine."""
        class Absent(link_registry.Toolchain):
            name = "absent-test"
            description = "looks for a tool that is not there"

            def link(self, request):
                link_registry.find_tool(("definitely-not-a-real-tool-xyz",),
                                        what="assembler")
        link_registry.register(Absent())
        src = tmp_path / "p.py"
        src.write_text(PROGRAM, encoding="utf-8")
        sink = DiagnosticSink()
        result = compile_source(Options(source=src, output=tmp_path / "p.exe",
                                        link=True, toolchain="absent-test",
                                        workdir=tmp_path / "w"), sink)
        assert not result.ok
        d = next(d for d in sink.diagnostics if d.code == "E9104")
        assert "assembler" in d.message
        assert d.notes, "the diagnostic should say what was looked for"

    def test_the_commands_run_are_reported(self, tmp_path):
        """When a build fails the exact invocation is the first thing wanted,
        so it is recorded rather than described."""
        result = self.build(tmp_path, "c", "c")
        assert result.commands, "no commands recorded"
        assert any("-o" in cmd for cmd in result.commands)
