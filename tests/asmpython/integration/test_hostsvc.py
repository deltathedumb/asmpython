"""Host services: the contract between a frontend and a backend.

`link/hostsvc.py` names a set of operations -- open a file, read the clock,
ask for entropy -- with fixed signatures, and each backend satisfies them
however it can. These tests are for the three things that arrangement is FOR,
in the order they matter:

1. THE SAME PROGRAM WORKS ON EVERY BACKEND THAT HAS THE CAPABILITY, and the
   frontend never learns which one it is compiling for. That is the whole
   claim, and it is what `ctypes` could not do: `frontends/python/cffi.py`
   resolves a symbol at COMPILE time, so `_open` is a promise only a linking
   backend can keep and `bundled/pathlib.py` is stuck on the C backend.
2. A BACKEND WITHOUT THE CAPABILITY REFUSES AT COMPILE TIME, naming the group.
   Not an undefined symbol at link time naming an object file, and not a wrong
   answer at run time.
3. NOTHING HERE KNOWS WHAT A PYTHON VALUE IS. That is the floor's rule and it
   is inherited: the moment one of these takes a `str` rather than a pointer
   and a length, every backend implementing it owes the language.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from tests import harness
from tests.harness import snapshot

SRC = snapshot.current(Path(__file__).resolve().parents[3])


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "asmpython", *args],
                          capture_output=True, text=True, env=env)


def write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "prog.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def build_and_run(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    out = tmp_path / "prog.exe"
    built = _cli("build", str(write(tmp_path, source)), "--backend", "c",
                 "-o", str(out), "--workdir", str(tmp_path / "wd"))
    assert built.returncode == 0, built.stdout + built.stderr
    return subprocess.run([str(out)], capture_output=True, text=True,
                          cwd=str(tmp_path))


def interpret(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    return _cli("run", str(write(tmp_path, source)))


#: A round trip through the file group, reporting WHICH step failed as the
#: exit status. A single pass/fail would say "the filesystem is broken"; a
#: number says which of thirteen claims is the false one.
ROUND_TRIP = """\
    def put(p: ptr, i: i64, c: i64) -> i64:
        store(u8, u8(c), offset(p, i))
        return 0

    def main() -> int:
        path: ptr = alloca(32)
        put(path, 0, 104)
        put(path, 1, 115)
        put(path, 2, 46)
        put(path, 3, 116)
        put(path, 4, 109)
        put(path, 5, 112)
        n: i64 = 6

        if host_file_kind(path, n) != 0:
            host_file_remove(path, n)
        if host_file_kind(path, n) != 0:
            return 1

        fd: i64 = host_file_open(path, n, 1)
        if fd < 0:
            return 2
        buf: ptr = alloca(16)
        put(buf, 0, 65)
        put(buf, 1, 66)
        put(buf, 2, 67)
        if host_file_write(fd, buf, 3) != 3:
            return 3
        if host_file_close(fd) != 0:
            return 4

        if host_file_kind(path, n) != 1:
            return 5
        if host_file_size(path, n) != 3:
            return 6

        fd = host_file_open(path, n, 0)
        if fd < 0:
            return 7
        back: ptr = alloca(16)
        got: i64 = host_file_read(fd, back, 16)
        host_file_close(fd)
        if got != 3:
            return 8
        if load(u8, offset(back, 0)) != 65:
            return 9
        if load(u8, offset(back, 2)) != 67:
            return 10

        if host_file_remove(path, n) != 0:
            return 11
        if host_file_kind(path, n) != 0:
            return 12
        if host_file_open(path, n, 0) != -2:
            return 13
        return 0
"""


class TestOneProgramTwoBackends:
    @harness.needs("gcc")
    def test_the_file_group_compiled(self, tmp_path):
        """Open, write, close, ask, read back, remove -- through the C."""
        got = build_and_run(tmp_path, ROUND_TRIP)
        assert got.returncode == 0, (
            f"step {got.returncode} of the round trip failed"
            f"{got.stdout}{got.stderr}")

    def test_the_file_group_interpreted(self, tmp_path):
        """THE SAME SOURCE, on a path with no linker and no libc.

        This is the claim `ctypes` cannot make. The interpreter answers these
        names from Python's `os`; the C backend answers them from libc; the
        program says neither.
        """
        got = interpret(tmp_path, ROUND_TRIP)
        assert got.returncode == 0, (
            f"step {got.returncode} of the round trip failed"
            f"{got.stdout}{got.stderr}")

    def test_a_missing_file_is_the_layers_code_and_not_an_errno(self,
                                                                tmp_path):
        """-2 on both paths, which is the point of having a table at all.

        `ENOENT` is 2 on Linux and 2 on Windows and nothing guarantees the
        next platform agrees -- and `EACCES`/`EPERM` already differ in which
        one a directory refusal produces. A caller that branches on the number
        must get the same number everywhere, so the layer defines its own and
        each backend translates once.
        """
        source = """\
            def main() -> int:
                path: ptr = alloca(16)
                store(u8, u8(122), offset(path, 0))
                store(u8, u8(122), offset(path, 1))
                store(u8, u8(122), offset(path, 2))
                return int(0 - host_file_open(path, 3, 0))
        """
        assert interpret(tmp_path, source).returncode == 2


class TestABackendWithoutTheCapability:
    def test_it_refuses_at_compile_time_naming_the_group(self, tmp_path):
        """The JVM backend has no `file`, and says so.

        NAMED BY GROUP AND NOT ONLY BY FUNCTION, because the answer is never
        to implement one operation -- a target with files has all of them or
        none. Naming `host_file_write` alone invites someone to add one
        function and find the next one missing.
        """
        source = """\
            def main() -> int:
                buf: ptr = alloca(8)
                store(u8, u8(65), offset(buf, 0))
                return int(host_file_write(1, buf, 1))
        """
        got = _cli("build", str(write(tmp_path, source)), "--backend", "jvm",
                   "-o", str(tmp_path / "p.jar"),
                   "--workdir", str(tmp_path / "wd"))
        assert got.returncode != 0
        text = got.stdout + got.stderr
        assert "'file'" in text, text
        assert "host_file_write" in text, text
        assert "jvm" in text, text

    def test_a_program_that_uses_none_is_unaffected(self, tmp_path):
        """A backend with no host services is still a complete backend.

        The whole reason these are declared rather than mandatory: stage 2 of
        docs/INERT-RUNTIME.md got the floor from five functions to three, and
        a mandatory thirty would undo it.
        """
        source = """\
            def main() -> int:
                return 7
        """
        got = _cli("build", str(write(tmp_path, source)), "--backend", "jvm",
                   "-o", str(tmp_path / "p.jar"),
                   "--workdir", str(tmp_path / "wd"))
        assert got.returncode == 0, got.stdout + got.stderr


class TestTheTableItself:
    def test_the_floor_is_the_core_group_and_not_a_copy(self):
        """One list, because two drifted three times in one afternoon."""
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import hostsvc, platform
        finally:
            del sys.path[0]
        assert hostsvc.GROUPS["core"] == dict(platform.FLOOR)
        assert hostsvc.MANDATORY == ("core",)

    def test_nothing_takes_or_answers_an_object(self):
        """THE FLOOR'S RULE, INHERITED. Every signature is machine words and
        pointers to bytes. The moment one takes a `str`, every backend that
        implements it owes the language rather than the machine -- which is
        the argument `link/platform.py` makes for why the floor is three
        functions and not the five it used to be."""
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import hostsvc
        finally:
            del sys.path[0]
        allowed = {"i64", "f64", "ptr", "void"}
        for name, (args, ret) in hostsvc.ALL.items():
            assert set(args) <= allowed, (name, args)
            assert ret in allowed, (name, ret)

    def test_every_operation_belongs_to_exactly_one_group(self):
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import hostsvc
        finally:
            del sys.path[0]
        seen: dict[str, str] = {}
        for group, ops in hostsvc.GROUPS.items():
            for name in ops:
                assert name not in seen, (name, seen[name], group)
                seen[name] = group
        assert set(seen) == set(hostsvc.ALL)
        assert seen == hostsvc.GROUP_OF

    def test_the_c_backend_implements_what_it_declares(self):
        """A declared group with no C is an undefined symbol at link time,
        which names an object file rather than the group that was claimed."""
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import hostsvc
            from asmpython.backends.c.emit import CBackend
        finally:
            del sys.path[0]
        c = hostsvc.c_source(sorted(CBackend.host_services))
        for group in CBackend.host_services:
            for name in hostsvc.GROUPS[group]:
                assert name in c, f"{name} declared by the C backend, not emitted"

    def test_the_interpreter_implements_what_it_declares(self):
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import hostsvc
            from asmpython.ir import hostsvc_host
        finally:
            del sys.path[0]
        for group in hostsvc_host.GROUPS:
            for name in hostsvc.GROUPS[group]:
                assert name in hostsvc_host._TABLE, (
                    f"{name} is in the {group!r} group the interpreter "
                    f"declares, and it has no binding")
