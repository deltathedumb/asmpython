"""Each object format gets the directives it actually accepts.

WHY THIS FILE EXISTS. `x86_64-macos` and `aarch64-macos` had been registered
targets since the beginning, and both emitted ELF. The x86-64 backend chose
its dialect with `coff if ... else elf` and the arm64 backend had no notion of
a dialect at all, so `--target x86_64-macos` produced output BYTE-IDENTICAL to
the Linux one: `.type`, `.size` and `.note.GNU-stack`, none of which a Mach-O
assembler will take, and no leading underscore, so nothing would have resolved
even if it had assembled.

Nothing caught it because nothing compared the formats. Every test asked
whether ONE target produced good assembly, and the answer was yes for the two
that were exercised. So the assertions here are DIFFERENTIAL -- what one
format has and another must not -- because that is the shape of the bug.

The text checks need no toolchain and are the regression guard. The assembly
check needs clang, which can target all three formats from any host, and is
the one that would notice a directive this file has not thought to name.
"""
from __future__ import annotations

import shutil
import subprocess

from tests import harness

from uasm import target as target_registry
from uasm.diagnostics import DiagnosticSink
from uasm.driver import Options, compile_source

HAS_CLANG = bool(shutil.which("clang"))


#: A program with a call and a definition, so both a symbol's DEFINITION and
#: its USE are in the output -- the prefix has to reach both, and applying it
#: to only one is the failure mode that links to nothing.
SOURCE = """\
def add(a: int, b: int) -> int:
    return a + b


def main() -> int:
    return add(3, 4)
"""

#: backend, target, and the triple clang assembles that target's output with.
MATRIX = [
    ("x86-64", "x86_64-linux", "x86_64-linux-gnu"),
    ("x86-64", "x86_64-windows", "x86_64-windows-gnu"),
    ("x86-64", "x86_64-macos", "x86_64-apple-darwin"),
    ("arm64", "aarch64-linux", "aarch64-linux-gnu"),
    ("arm64", "aarch64-macos", "arm64-apple-darwin"),
    ("arm64", "aarch64-none", "aarch64-linux-gnu"),
]


def _artifact(tmp_path, backend: str, target: str) -> tuple[str, bytes]:
    """What this backend writes for this target, named and unread."""
    path = tmp_path / "prog.py"
    path.write_text(SOURCE, encoding="utf-8")
    result = compile_source(Options(
        source=path, backend=backend,
        target=target_registry.get(target)), DiagnosticSink())
    assert result.ok, f"{backend}/{target} did not compile"
    (name, body), = result.artifacts.items()
    return name, body


def _asm(tmp_path, backend: str, target: str) -> str:
    """The assembly this backend writes for this target.

    ONLY FOR A TARGET THAT STILL GOES THROUGH TEXT. Once a backend writes the
    object itself there is no assembly to read and the dialect's job has moved
    into the symbol table -- see `TestTheElfObjectSaysWhatTheDirectivesDid`.
    """
    name, body = _artifact(tmp_path, backend, target)
    assert name.endswith(".s"), (
        f"{backend}/{target} emits {name}, not assembly; this claim belongs "
        f"in the object-file tests")
    return body.decode("utf-8")


#: Pairs whose object writer exists, so nothing assembles them from text.
OBJECT_PAIRS = {(backend, target) for backend, target, _ in MATRIX}

#: The subset of those that are ELF, for the claims that read ELF structure.
ELF_PAIRS = {p for p in OBJECT_PAIRS
             if target_registry.get(p[1]).object_format == "elf"}

#: Pairs still assembled from text. THIS SHRINKS as backends grow encoders,
#: and the claims about each pair move rather than disappear.
TEXT_MATRIX = [row for row in MATRIX if (row[0], row[1]) not in OBJECT_PAIRS]


class TestTheDialectsDiffer:
    @harness.needs("llvm-aarch64")
    @harness.cases("backend,target", [(b, t) for b, t, _ in MATRIX
                                      if t.endswith("macos")])
    def test_macho_symbols_wear_an_underscore(self, backend, target, tmp_path):
        """Mach-O's C ABI prefixes every symbol, at the definition AND the use.

        READ OFF THE SYMBOL TABLE now that the artifact is an object. The
        claim is the same one the text version made, and it is the one that
        matters: a prefix applied at the definition and not at the call site
        links to nothing.
        """
        name, body = _artifact(tmp_path, backend, target)
        (tmp_path / name).write_bytes(body)
        out = subprocess.run(["llvm-nm", str(tmp_path / name)],
                             capture_output=True, text=True, check=True).stdout
        names = [line.split()[-1] for line in out.splitlines() if line.strip()]
        assert "_uasm_main" in names, "the entry point is not prefixed"
        assert "_add" in names, "a defined symbol is not prefixed"
        assert not any(n in ("uasm_main", "add") for n in names), (
            "an unprefixed name survives into Mach-O")

    @harness.needs("llvm-aarch64")
    @harness.cases("backend,target", [(b, t) for b, t, _ in MATRIX
                                      if t.endswith("macos")])
    def test_macho_sections_are_in_their_segments(self, backend, target,
                                                  tmp_path):
        """What "no ELF directives" meant, once there are no directives.

        The text test asserted that `.type`, `.size` and `.note.GNU-stack` did
        not survive. An object has none of those by construction, so the
        equivalent claim is that the sections are Mach-O's own: `__text` in
        `__TEXT`, and no section still called `.text`.
        """
        name, body = _artifact(tmp_path, backend, target)
        (tmp_path / name).write_bytes(body)
        out = subprocess.run(["llvm-objdump", "-h", str(tmp_path / name)],
                             capture_output=True, text=True, check=True).stdout
        assert "__text" in out, out
        assert ".text" not in out, "an ELF section name survives into Mach-O"

    def test_coff_marks_a_function_as_one(self, tmp_path):
        """What `.def/.scl/.endef` was for, read off the symbol record.

        THE CLAIM MOVED WITH THE OUTPUT. Those directives existed to tell an
        assembler that a symbol names code and what its storage class is;
        writing the object directly means setting `Type` and `StorageClass`,
        so the test reads those two fields. Keeping the text version would
        have tested a path nothing takes any more.
        """
        import struct
        _, body = _artifact(tmp_path, "x86-64", "x86_64-windows")
        symtab, count = struct.unpack_from("<II", body, 8)
        found = {}
        strings_at = symtab + 18 * count
        for index in range(count):
            at = symtab + 18 * index
            raw = body[at:at + 8]
            if raw[:4] == b"\0\0\0\0":
                # THE OFFSET COUNTS FROM THE TABLE'S FIRST BYTE, which is the
                # four-byte length -- so it is never less than four, and never
                # needs that four subtracted again.
                offset, = struct.unpack_from("<I", raw, 4)
                end = body.index(b"\0", strings_at + offset)
                name = body[strings_at + offset:end].decode()
            else:
                name = raw.rstrip(b"\0").decode()
            _, _, kind, storage, _ = struct.unpack_from("<IhHBB", body, at + 8)
            found[name] = (kind, storage)
        kind, storage = found["uasm_main"]
        assert kind == 0x20, "the entry point is not marked as a function"
        assert storage == 2, "the entry point is not external"

    @harness.cases("backend,a,b", [
        ("x86-64", "x86_64-linux", "x86_64-macos"),
        ("x86-64", "x86_64-linux", "x86_64-windows"),
        ("arm64", "aarch64-linux", "aarch64-macos"),
    ])
    def test_two_formats_are_not_the_same_text(self, backend, a, b, tmp_path):
        """THE ASSERTION THAT WOULD HAVE CAUGHT IT.

        Byte-identical output for two object formats is the whole bug, and it
        is checkable without knowing which directive is wrong.
        """
        # COMPARED AS BYTES, because one side of a pair may be an object and
        # the other assembly: decoding would fail on the object rather than
        # answer the question, and the question is only whether they differ.
        assert _artifact(tmp_path, backend, a)[1] \
            != _artifact(tmp_path, backend, b)[1]


class TestTheElfObjectSaysWhatTheDirectivesDid:
    """Where `.type` and `.size` go once nothing writes assembly.

    THE CLAIM IS THE SAME ONE, moved. `.type add, @function` and `.size add,
    .-add` exist to put a kind and a length in the symbol table; a backend
    writing the table itself sets those fields directly, so the test reads the
    fields rather than the directives that used to produce them. Dropping the
    check with the text would have retired the only assertion that the ELF
    path describes its symbols at all.
    """

    def _object(self, tmp_path, backend="x86-64", target="x86_64-linux"):
        name, body = _artifact(tmp_path, backend, target)
        assert name.endswith(".o"), name
        path = tmp_path / name
        path.write_bytes(body)
        return path

    @harness.needs("readelf")
    @harness.cases("backend,target", sorted(ELF_PAIRS))
    def test_the_symbols_carry_their_kind_and_size(self, backend, target,
                                                   tmp_path):
        out = subprocess.run(
            ["readelf", "-sW", str(self._object(tmp_path, backend, target))],
            capture_output=True, text=True, check=True).stdout
        # `Num: Value Size Type Bind Vis Ndx Name`. The header row starts
        # with `Num:` and would otherwise parse as a symbol called `Name`.
        rows = {}
        for line in out.splitlines():
            fields = line.split()
            if len(fields) >= 8 and fields[0][:-1].isdigit():
                rows[fields[7]] = (fields[3], int(fields[2], 0))
        for wanted in ("add", "uasm_main"):
            assert wanted in rows, f"{wanted} is not in the symbol table"
            kind, size = rows[wanted]
            assert kind == "FUNC", f"{wanted} is {kind}, not FUNC"
            assert size > 0, f"{wanted} has no size"
        # NO LEADING UNDERSCORE, which is the Mach-O convention and would
        # resolve to nothing here. The text test asserted this too.
        assert "_uasm_main" not in rows

    @harness.needs("readelf")
    def test_the_stack_is_marked_non_executable(self, tmp_path):
        """`.note.GNU-stack` as a section rather than a directive.

        Without it a linker assumes the worst and marks the whole program's
        stack executable -- a real difference in the binary produced, and one
        no other test would notice.
        """
        out = subprocess.run(["readelf", "-SW", str(self._object(tmp_path))],
                             capture_output=True, text=True, check=True).stdout
        assert ".note.GNU-stack" in out


@harness.skip_if(not HAS_CLANG, reason="no clang to assemble with")
class TestItActuallyAssembles:
    """clang cross-assembles all three formats from any host, so this ran
    everywhere rather than only on the platform it describes.

    THERE IS NOTHING LEFT TO ASSEMBLE. `TEXT_MATRIX` is empty: every pair in
    `MATRIX` now writes its own object. The class stays as the place this
    check belongs if a backend ever emits text again -- and its emptiness is
    asserted below rather than left as a silently vacuous run.
    """

    @harness.cases("backend,target,triple", TEXT_MATRIX)
    def test_the_output_assembles(self, backend, target, triple, tmp_path):
        source = tmp_path / "out.s"
        source.write_text(_asm(tmp_path, backend, target), encoding="utf-8")
        done = subprocess.run(
            ["clang", "-target", triple, "-c", str(source),
             "-o", str(tmp_path / "out.o")],
            capture_output=True, text=True)
        assert done.returncode == 0, (
            f"{backend}/{target} did not assemble as {triple}:\n{done.stderr}")


class TestTheAssemblyIsStillReachableOnPurpose:
    """`--emit-asm`, which is why the text path is not dead code.

    WHY IT HAD TO BECOME AN OPTION. Once every target got an object writer,
    the assembly these backends generate was unreachable -- and that loses two
    things. A compiler that cannot show what it generated is much harder to
    debug than one that can, and the x86 lifter's entire input is x86 assembly
    this backend wrote. So the text has a name and a caller rather than
    surviving as a branch nothing takes.
    """

    @harness.cases("backend,target", [("x86-64", "x86_64-linux"),
                                      ("arm64", "aarch64-none")])
    def test_a_machine_backend_can_show_its_assembly(self, backend, target,
                                                     tmp_path):
        from uasm import backend as backend_registry
        backend_registry.load_builtin()
        path = tmp_path / "prog.py"
        path.write_text(SOURCE, encoding="utf-8")
        artifacts = backend_registry.get(backend).assembly(
            compile_source(Options(source=path, backend=backend, emit_ir=True,
                                   target=target_registry.get(target)),
                           DiagnosticSink()).module,
            target_registry.get(target))
        (name, body), = artifacts.items()
        assert name.endswith(".s"), name
        text = body.decode("utf-8")
        assert "add" in text and "uasm_main" in text

    @harness.cases("backend", ["c", "jvm", "pybc"])
    def test_a_backend_whose_artifact_is_already_readable_refuses(self,
                                                                  backend):
        """The honest answer for the C backend: its artifact IS the text."""
        from uasm import backend as backend_registry
        from uasm.backend.base import BackendUnsupported
        backend_registry.load_builtin()
        with harness.raises(BackendUnsupported, match="no assembly form"):
            backend_registry.get(backend).assembly(None, None)

    def test_the_option_reaches_the_driver(self, tmp_path):
        """Through `Options`, the way the CLI sets it -- so the wiring is
        tested rather than only the backend method behind it."""
        path = tmp_path / "prog.py"
        path.write_text(SOURCE, encoding="utf-8")
        result = compile_source(
            Options(source=path, backend="x86-64", emit_asm=True,
                    target=target_registry.get("x86_64-linux")),
            DiagnosticSink())
        assert result.ok
        (name, body), = result.artifacts.items()
        assert name == "out.s"
        assert body.startswith(b"# Generated by the x86-64 backend")


class TestNothingInTheMatrixStopsAtAssembly:
    def test_every_pair_writes_an_object(self):
        """A vacuous parametrised class reports as a pass and tests nothing,
        so the fact that there is nothing left to assemble is asserted."""
        assert TEXT_MATRIX == [], (
            f"these pairs still emit assembly: "
            f"{[(b, t) for b, t, _ in TEXT_MATRIX]}")
