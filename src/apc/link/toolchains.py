"""The toolchains that ship with apc.

Both are ordinary registrations. `cc` is the one that produces programs; `none`
exists so "emit the artifacts and stop" is a toolchain rather than a special
case threaded through the driver.
"""
from __future__ import annotations

from pathlib import Path

from ..target import Target
from .base import LinkError, LinkRequest, Toolchain, find_tool, run
from .registry import register

#: Suffixes a C driver knows how to consume directly. Anything else is passed
#: through as a linker input (an object, an archive, a `-l` name).
_COMPILABLE = {".c", ".s", ".S", ".asm"}


class CcToolchain(Toolchain):
    """Assemble and link with a C compiler driver.

    Using `gcc`/`clang` rather than `as` and `ld` directly is a deliberate
    choice: the driver knows where crt1.o, libc and the dynamic loader live on
    this machine, and reproducing that search is both the hardest part of
    linking and the part with no portable answer. Calling `ld` by hand works on
    the machine it was written on.
    """

    name = "cc"
    description = "assemble and link with the system C compiler driver"

    #: Tried in order. `cc` last: it is usually a symlink to one of the others,
    #: so naming them first makes the reported command say what actually ran.
    CANDIDATES = ("gcc", "clang", "cc")

    def supports(self, target: Target) -> bool:
        return not target.is_source or True

    def link(self, request: LinkRequest) -> Path:
        cc = find_tool(self.CANDIDATES, what="C compiler driver",
                       install="install gcc or clang, or pass --toolchain none")
        work = request.workdir
        work.mkdir(parents=True, exist_ok=True)

        inputs: list[str] = []
        for name, data in request.artifacts.items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if path.suffix in _COMPILABLE or path.suffix == request.target.object_suffix:
                inputs.append(str(path))
        for extra in request.runtime_sources:
            inputs.append(str(extra))

        if not inputs:
            raise LinkError(
                "the backend produced nothing this toolchain can link",
                detail="artifacts: " + (", ".join(request.artifacts) or "(none)"),
                help="a backend emitting something other than C, assembly or "
                     "objects needs its own toolchain")

        output = request.output
        argv = [cc, *inputs, "-o", str(output), *request.extra_inputs]
        run(request, argv, what="linking")
        if not output.exists():
            raise LinkError(f"{cc} reported success but wrote no {output.name}")
        return output


class NoToolchain(Toolchain):
    """Write the artifacts out and stop.

    Not a no-op: it is what `--emit` means, and having it here rather than as a
    branch in the driver keeps "produce a program" a single code path with a
    pluggable end.
    """

    name = "none"
    description = "write backend artifacts to disk; do not assemble or link"

    def link(self, request: LinkRequest) -> Path:
        directory = request.output.parent if request.output.suffix \
            else request.output
        directory.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name, data in request.artifacts.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written.append(path)
        return written[0] if written else directory


def load_builtin() -> None:
    register(CcToolchain())
    register(NoToolchain())
