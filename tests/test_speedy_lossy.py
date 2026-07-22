from __future__ import annotations

from types import SimpleNamespace

import asmpython
from asmpython._backends import get_backend
from asmpython._compiler.build_options import extract_speedy_lossy, speedy_lossy_mode
from asmpython._compiler.ir import ModuleBackend
from asmpython._linkers import get_linker


def test_extract_speedy_lossy_removes_repeated_flags() -> None:
    argv, enabled = extract_speedy_lossy([
        "build", "app.py", "--speedy-lossy", "--keep", "--speedy-lossy",
    ])
    assert argv == ["build", "app.py", "--keep"]
    assert enabled is True


def test_module_backend_receives_explicit_flag_for_codegen_and_link() -> None:
    observed: list[tuple[str, bool]] = []

    module = SimpleNamespace(
        requested_args=[],
        default_linker="test",
        production_suitable=False,
        run_backend_codegen=lambda ir, args: (
            observed.append(("compile", args["speedy_lossy"]))
            or {"output.o": b"object"}
        ),
        run_backend_link=lambda objects, args: (
            observed.append(("link", args["speedy_lossy"]))
            or {"output": b"binary"}
        ),
    )
    backend = ModuleBackend(module)

    with speedy_lossy_mode(False):
        backend.compile(object(), {})
        backend.link([b"object"], {})
    with speedy_lossy_mode(True):
        backend.compile(object(), {})
        backend.link([b"object"], {})

    assert observed == [
        ("compile", False),
        ("link", False),
        ("compile", True),
        ("link", True),
    ]
    assert backend.production_suitable is False


def test_public_backend_and_linker_adapters_inject_flag() -> None:
    backend_calls: list[tuple[str, bool]] = []
    linker_calls: list[bool] = []

    class BackendImpl:
        requested_args: list[dict] = []
        default_linker = "speedy-test-linker"

        def compile(self, module: object, args: dict) -> dict[str, bytes]:
            backend_calls.append(("compile", args["speedy_lossy"]))
            return {"output.o": b"object"}

        def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
            backend_calls.append(("link", args["speedy_lossy"]))
            return {"output": b"binary"}

    class LinkerImpl:
        requested_args: list[dict] = []

        def link(self, ctx: dict) -> bytes:
            linker_calls.append(ctx["speedy_lossy"])
            return b"binary"

    backend_registration = asmpython.backend.Backend(
        "speedy-test-backend", BackendImpl(), production_suitable=False
    )
    linker_registration = asmpython.linker.Linker(
        "speedy-test-linker", LinkerImpl(), production_suitable=False
    )

    registered_backend = get_backend("speedy-test-backend")
    registered_linker = get_linker("speedy-test-linker")
    assert registered_backend is not None
    assert registered_linker is not None

    with speedy_lossy_mode(True):
        registered_backend.compile(object(), {})
        registered_backend.link([b"object"], {})
        registered_linker.link({"objects": [b"object"], "target_os": "linux"})

    assert backend_calls == [("compile", True), ("link", True)]
    assert linker_calls == [True]
    assert backend_registration.impl.__class__ is BackendImpl
    assert linker_registration.impl.__class__ is LinkerImpl
    assert registered_backend.production_suitable is False
    assert registered_linker.production_suitable is False
