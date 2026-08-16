"""The toolchains that ship with asmpython.

All three are ordinary registrations. `cc` assembles and links through a C
compiler driver; `jar` packages what a JVM backend emits; `none` exists so
"emit the artifacts and stop" is a toolchain rather than a special case
threaded through the driver.
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
        # A cross target names its own driver; the host's gcc cannot produce
        # code for it, and guessing the name from the architecture is the
        # kind of sniffing that put System V code on a Windows target.
        candidates = request.target.cc_names or self.CANDIDATES
        cc = find_tool(candidates, what=f"compiler for {request.target.name}",
                       install="install a suitable toolchain and put it on "
                               "PATH, or pass --toolchain none")
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
        # libm is a separate library on ELF systems and part of libc
        # elsewhere. Float remainder compiles to an `fmod` call, so a program
        # using `%` on floats fails to link without this -- and only that
        # program, which makes it look like a frontend bug.
        system_libs = ["-lm"] if request.target.object_format == "elf" else []
        argv = [cc, *inputs, "-o", str(output), *request.extra_inputs,
                *system_libs]
        run(request, argv, what="linking")
        if not output.exists():
            raise LinkError(f"{cc} reported success but wrote no {output.name}")
        return output


class JarToolchain(Toolchain):
    """Package class files into a jar you can run with `java -jar`.

    No JDK required, and that is the point: a jar is a zip with a manifest, and
    Python has both. Shelling out to the `jar` tool would make building for the
    JVM need a JDK installed, when the only thing that genuinely needs one is
    RUNNING the result.

    The manifest is not written here. The backend emits it as an artifact,
    because the backend is what knows which class holds the entry point, and a
    toolchain that reconstructed that would be a second place deciding it.
    """

    name = "jar"
    description = "package class files into a runnable jar (no JDK needed)"

    def supports(self, target: Target) -> bool:
        return target.object_format == "class"

    def link(self, request: LinkRequest) -> Path:
        import zipfile

        if not any(name.endswith(".class") for name in request.artifacts):
            raise LinkError(
                "no class files to package",
                detail="artifacts: " + (", ".join(request.artifacts) or "(none)"),
                help="the jar toolchain packages what a JVM backend emits; "
                     "use --toolchain cc for a backend emitting C or assembly")
        if request.extra_inputs:
            raise LinkError(
                "the jar toolchain takes no extra link inputs",
                detail=", ".join(request.extra_inputs),
                help="a class file resolves its dependencies at load time; "
                     "put extra classes on the class path when you run it")

        output = request.output
        output.parent.mkdir(parents=True, exist_ok=True)
        # The manifest goes in FIRST. A JVM finds it by name either way, but
        # every other tool that reads jars expects it at the front, and one of
        # them is `jar tf` when someone is working out what went wrong.
        order = sorted(request.artifacts,
                       key=lambda n: (n != "META-INF/MANIFEST.MF", n))
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as jar:
            for name in order:
                # A fixed timestamp: two builds of one program should produce
                # identical bytes, and a zip entry's mtime is the one field
                # that would otherwise stop them.
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                jar.writestr(info, request.artifacts[name])
        request.commands.append(["(zip)", str(output), *order])
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
    register(JarToolchain())
    register(NoToolchain())
