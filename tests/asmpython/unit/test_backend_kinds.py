"""A binary backend emits bytes; a language backend emits source. Nothing else.

THE RULE. A backend is one of two things and the difference decides whether
text may come out of it:

  * a LANGUAGE backend emits source in another language -- C, LLVM IR -- and
    something downstream compiles it. Text is the artifact.
  * a BINARY backend emits machine code or bytecode. The output is bytes a
    loader takes directly.

ASSEMBLY IS NEITHER, and that is the case this file exists to catch. A backend
emitting `.s` looks finished from the outside -- the file appears, `cc` links
it, the program runs -- while it has never encoded an instruction: the last
stage was handed to `as`. Nothing else in the suite can tell the difference,
because every test asks whether the program produced the right answer and the
answer is right either way.

So the check is on the ARTIFACT, not on the backend's own description of
itself. `KNOWN_TEXT_EMITTERS` is the list of binary backends that still emit
assembly; it is expected to shrink to empty, and a backend leaving it must
also be removed from that list or `test_the_gap_list_is_not_stale` fails.
"""
from __future__ import annotations

from tests import harness

from asmpython import backend as backend_registry
from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source

backend_registry.load_builtin()
BACKENDS = sorted(backend_registry.available())

#: BACKENDS THAT CLAIM TO WORK. An unfinished one declares `ready = False` and
#: refuses to emit, so asking it for artifacts tests nothing about its output
#: -- it has none. What it owes instead is a clear refusal, which
#: `TestAnUnfinishedBackendRefuses` checks.
READY = [b for b in BACKENDS if backend_registry.get(b).ready]

SOURCE = """\
def add(a: int, b: int) -> int:
    return a + b


def main() -> int:
    return add(3, 4)
"""

#: BINARY BACKENDS THAT STILL EMIT ASSEMBLY TEXT. Each is a backend that has
#: not got an instruction encoder or an object writer yet, so it stops at
#: `.s` and lets `as` finish. THIS LIST MUST ONLY EVER SHRINK.
KNOWN_TEXT_EMITTERS = {"x86-64", "arm64"}

#: What a LANGUAGE backend is allowed to name its output.
TEXT_SUFFIXES = (".c", ".h", ".ll", ".wat")

#: Source or assembly. A binary backend may emit NONE of these -- each one
#: means a stage was left to an external assembler or compiler.
CODE_SUFFIXES = (".c", ".h", ".ll", ".wat", ".s", ".asm", ".S")


def _artifacts(backend: str) -> dict[str, bytes]:
    be = backend_registry.get(backend)
    target = target_registry.get(be.default_target)
    import tempfile
    import pathlib
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "prog.py"
        path.write_text(SOURCE, encoding="utf-8")
        result = compile_source(
            Options(source=path, backend=backend, target=target),
            DiagnosticSink())
        assert result.ok, f"{backend} did not compile"
        return dict(result.artifacts)


def _looks_like_text(data: bytes) -> bool:
    """Whether these bytes are source a human wrote conventions for.

    Decoding as UTF-8 is not the test on its own: a class file decodes often
    enough by accident. What settles it is a NUL byte, which no source file
    has and nearly every binary format does in its first few words.
    """
    if b"\x00" in data[:512]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


class TestEveryBackendDeclaresWhatItIs:
    @harness.cases("backend", BACKENDS)
    def test_the_kind_is_one_of_the_two(self, backend):
        kind = backend_registry.get(backend).kind
        assert kind in ("language", "binary"), f"{backend}: kind={kind!r}"


class TestLanguageBackendsEmitSource:
    @harness.cases("backend", [b for b in READY
                               if backend_registry.get(b).kind == "language"])
    def test_the_artifact_is_text(self, backend):
        for name, data in _artifacts(backend).items():
            assert _looks_like_text(data), f"{backend}/{name} is not source"
            assert name.endswith(TEXT_SUFFIXES), f"{backend}/{name}: odd suffix"


class TestBinaryBackendsEmitBytes:
    @harness.cases("backend", [b for b in READY
                               if backend_registry.get(b).kind == "binary"
                               and b not in KNOWN_TEXT_EMITTERS])
    def test_the_artifact_is_not_text(self, backend):
        """The claim `kind = "binary"` makes, checked against the bytes.

        TWO CHECKS, because "no text at all" is the wrong rule. A jar carries
        a `META-INF/MANIFEST.MF`, which is text and is not code -- packaging
        metadata that has to be readable. What a binary backend must not emit
        is SOURCE OR ASSEMBLY, and it must emit at least one thing that really
        is bytes; a backend passing only the first check could emit nothing.
        """
        artifacts = _artifacts(backend)
        for name in artifacts:
            assert not name.endswith(CODE_SUFFIXES), (
                f"{backend}/{name} is source or assembly; a binary backend "
                f"must encode its own output rather than leave it to `as`")
        assert any(not _looks_like_text(d) for d in artifacts.values()), (
            f"{backend} emitted nothing binary: {sorted(artifacts)}")

    @harness.cases("backend", sorted(KNOWN_TEXT_EMITTERS))
    def test_the_gap_list_is_not_stale(self, backend):
        """A backend that has grown an encoder must LEAVE the list.

        Otherwise the list stops describing anything and starts being a place
        where exemptions accumulate -- which is how a known gap becomes a
        permanent one.
        """
        emitted = _artifacts(backend)
        assert any(n.endswith(CODE_SUFFIXES) for n in emitted), (
            f"{backend} no longer emits text -- remove it from "
            f"KNOWN_TEXT_EMITTERS")

    def test_the_gap_is_only_the_two_machine_backends(self):
        """Nothing may be ADDED to the list without this test being edited."""
        assert KNOWN_TEXT_EMITTERS == {"x86-64", "arm64"}
        for backend in KNOWN_TEXT_EMITTERS:
            assert backend in BACKENDS, f"{backend} is not a backend any more"


class TestAnUnfinishedBackendRefuses:
    """A registered name that cannot emit must SAY so, not crash.

    The six stubs exist so `asmpython backends` shows the whole matrix rather
    than four names, with the unfinished half marked. The price of that is a
    name a user can select, so the refusal is part of the contract: it names
    the backend and what is missing, and it arrives as a diagnostic rather
    than a traceback.
    """

    UNFINISHED = sorted(b for b in BACKENDS
                        if not backend_registry.get(b).ready)

    def test_there_are_stubs_and_they_are_marked(self):
        assert self.UNFINISHED, "no unfinished backends; update this file"
        for name in self.UNFINISHED:
            assert not backend_registry.get(name).ready, (
                f"{name} is in UNFINISHED but declares itself ready")

    @harness.cases("backend", UNFINISHED)
    def test_it_refuses_with_a_reason(self, backend):
        from asmpython.backend.base import BackendUnsupported
        be = backend_registry.get(backend)
        try:
            be.emit(None, None)
        except BackendUnsupported as exc:
            assert "not written yet" in str(exc) or "not identified" in str(exc)
            assert len(str(exc)) > 40, "the refusal does not say what is missing"
        else:
            raise AssertionError(f"{backend} emitted something")

    @harness.cases("backend", UNFINISHED)
    def test_it_still_declares_a_kind(self, backend):
        """Known before it is written, because it decides the whole design."""
        assert backend_registry.get(backend).kind in ("language", "binary")


class TestCSymbolsAreMangled:
    """A Python name that is a C keyword must not reach the C backend raw.

    FOUND BY `funcaddr`. The C backend has three places that write a symbol --
    `GLOBAL_ADDR`, `CALL` and `FUNC_ADDR` -- and two of them went through
    `_cname`. The third did not, and survived because NOTHING EMITTED
    `Op.FUNC_ADDR` from the static path until the subset grew an indirect
    call. It was reachable the whole time from ordinary Python: a program
    defining `def double(...)` and putting it in a list produced C reading
    `(uintptr_t)&double`, which gcc rejects with "expected expression".

    So this is not a test of the new intrinsic. It is a test that a user's
    choice of function name cannot break the backend, which is what the bug
    actually was.
    """

    KEYWORDS = ["double", "int", "float", "long", "register", "static",
                "signed", "union", "switch"]

    @harness.cases("name", KEYWORDS)
    def test_a_function_named_after_a_c_keyword_compiles(self, name, tmp_path):
        import pathlib
        source = pathlib.Path(tmp_path) / "prog.py"
        source.write_text(
            f"def {name}(x):\n"
            f"    return x * 2\n"
            f"\n"
            f"fs = [{name}]\n"
            f"print(fs[0](21))\n", encoding="utf-8")
        result = compile_source(
            Options(source=source, backend="c",
                    target=target_registry.get("c")), DiagnosticSink())
        assert result.ok, f"a function named {name!r} did not compile"
        (text,) = [d.decode("utf-8") for d in result.artifacts.values()]
        # THE ASSERTION IS ON THE C, not on the program running: taking the
        # address is what was wrong, and it appears whether or not the
        # toolchain is present to build it.
        assert f"&{name};" not in text, (
            f"the C takes the address of {name!r} unmangled")
