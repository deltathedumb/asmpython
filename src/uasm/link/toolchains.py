"""The toolchains that ship with uasm.

All ordinary registrations. `cc` assembles and links through a C compiler
driver; `jar` packages what a JVM backend emits; `pyc` writes the `pybc`
backend's single artifact under the right name; `cpyext` is `cc` again but
`-shared -fPIC` against the Python headers, for a real CPython extension
module; `none` exists so "emit the artifacts and stop" is a toolchain
rather than a special case threaded through the driver.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .. import target as target_registry
from ..target import Target
from ..options import Option
from .base import LinkError, LinkRequest, Toolchain, find_tool, run
from .registry import register

#: DECLARED ONCE AND SHARED by the three toolchains that really link. The same
#: flag on `jar` or `pyc` would be a promise neither can keep: one packages
#: class files and one writes a `.pyc`, and an object file handed to either has
#: nowhere to go. That used to be found out at link time, as a LinkError about
#: inputs; a declaration lets it be said before anything is built.
LINK_INPUT = Option("link-input", metavar="INPUT", repeat=True,
                    help="extra object, archive or -l name for the link step")

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
    #: A native executable, which on a Unix has no extension at all --
    #: so this is what `-o thing` means and what nothing else can be
    #: told apart from by spelling. Windows names it `.exe`, and that
    #: belongs here once the target decides the answer.
    artifacts = ()
    backends = ("c", "x86-64", "arm64", "x86-32", "arm32")
    options = (LINK_INPUT,)
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
        # `-ldl` for the same reason: `dlopen` moved into libc in glibc 2.34,
        # and was libdl before that -- so the flag is needed on older systems
        # and is a harmless empty stub on newer ones, which is why it is
        # unconditional rather than probed. Windows needs neither, because
        # both `fmod` and `LoadLibraryA` are in what the CRT links already.
        #
        # A source target (the C backend's default, `object_format="source"`)
        # has no object format of its own -- the actual format is whatever the
        # `cc` on PATH produces, which is the HOST's unless the target names
        # its own cross driver. Reading `object_format` straight off a source
        # target here always says "not elf", so every default `uasm
        # build` -- the README's very first example -- failed to link on
        # Linux the moment the spliced-in runtime referenced `floor` or
        # `fmod`, which is every program, not just ones using floats.
        object_format = (target_registry.host().object_format
                         if request.target.is_source
                         else request.target.object_format)
        system_libs = ["-lm", "-ldl"] if object_format == "elf" else []
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
    #: A runnable jar.
    artifacts = (".jar",)
    backends = ("jvm",)
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


class CPyExtToolchain(Toolchain):
    """Compile and link a CPython extension module: `.so` on Linux, `.pyd`
    on Windows.

    Almost `CcToolchain` -- the same driver, the same "write artifacts,
    then invoke it" shape -- but a `-shared -fPIC` build against the
    Python headers is not something `cc prog.c -o prog` produces, and
    bolting an `is_shared` branch onto `CcToolchain.link()` would make
    every ordinary executable build carry a check for a flag it never
    needs. A second, small toolchain reads as what it is instead.

    THE HEADERS COME FROM `sysconfig`, not a `python3-config` subprocess:
    this compiler is ALREADY RUNNING under the exact CPython whose C-API
    the extension is built against (see `pybc/emit.py`'s docstring for the
    same fact used the same way), so `sysconfig.get_paths()["include"]` is
    not a guess about which Python -- it is the one asking.
    """

    name = "cpyext"
    #: The extension module itself. `-o thing.so` asks for this and
    #: reaches the `cpyext` backend only through it.
    artifacts = (".so", ".pyd")
    backends = ("cpyext",)
    options = (LINK_INPUT,)
    description = "compile and link a CPython extension module (.so/.pyd)"

    def supports(self, target: Target) -> bool:
        return target.object_format == "source" and target.abi == "cpyext"

    def link(self, request: LinkRequest) -> Path:
        import sysconfig

        candidates = request.target.cc_names or CcToolchain.CANDIDATES
        cc = find_tool(candidates, what=f"compiler for {request.target.name}",
                       install="install a suitable C compiler and put it on "
                               "PATH -- a Windows .pyd needs a Windows-"
                               "targeting cross compiler such as MinGW-w64's "
                               "x86_64-w64-mingw32-gcc, since it links "
                               "against a Windows Python's import library")
        work = request.workdir
        work.mkdir(parents=True, exist_ok=True)

        inputs: list[str] = []
        for name, data in request.artifacts.items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if path.suffix in _COMPILABLE:
                inputs.append(str(path))
        if not inputs:
            raise LinkError(
                "the cpyext backend produced nothing this toolchain can "
                "compile",
                detail="artifacts: " + (", ".join(request.artifacts) or "(none)"))

        include_dir = sysconfig.get_paths()["include"]
        argv = [cc, *inputs, "-shared", "-fPIC",
                "-I", include_dir, "-o", str(request.output),
                *request.extra_inputs]
        if request.target.os == "windows":
            # A WINDOWS .pyd LINKS AGAINST THE INTERPRETER'S IMPORT
            # LIBRARY -- `PyInit_x` alone does not resolve `PyLong_*` et al.
            # at load time the way a Linux .so's undefined symbols resolve
            # against the process that dlopen'd it. `-fPIC` is a harmless
            # no-op under MinGW (position independence is the default);
            # left in rather than branched around, so the two platforms'
            # argv differ only by what Windows genuinely needs more of.
            libdir = sysconfig.get_config_var("installed_base") or ""
            major, minor = sys.version_info[:2]
            argv += ["-L", str(Path(libdir) / "libs"), f"-lpython{major}{minor}"]
        run(request, argv, what="compiling and linking the extension module")
        if not request.output.exists():
            raise LinkError(f"{cc} reported success but wrote no "
                            f"{request.output.name}")
        return request.output


class PycToolchain(Toolchain):
    """Write the `pybc` backend's single `.pyc` artifact to the output path.

    There is nothing to assemble or link -- a `.pyc` is one file, already
    complete, and the only question is where it goes. `NoToolchain` would
    write it under its own artifact name inside a directory; this instead
    honours `-o`/the target's default naming the same way `cc` and `jar` do,
    so `uasm build prog.py --backend pybc` produces `prog.pyc` next to
    `prog.py` like every other backend produces its own default output name.
    """

    name = "pyc"
    #: The bytecode file, written where it was asked for.
    artifacts = (".pyc",)
    backends = ("pybc",)
    description = "write the pybc backend's .pyc artifact to the output path"

    def supports(self, target: Target) -> bool:
        return target.object_format == "pyc"

    def link(self, request: LinkRequest) -> Path:
        pyc = [n for n in request.artifacts if n.endswith(".pyc")]
        if len(pyc) != 1:
            raise LinkError(
                "the pyc toolchain expects exactly one .pyc artifact",
                detail="artifacts: " + (", ".join(request.artifacts) or "(none)"),
                help="this toolchain packages what the pybc backend emits; "
                     "use --toolchain none to inspect raw artifacts instead")
        if request.extra_inputs:
            raise LinkError(
                "the pyc toolchain takes no extra link inputs",
                detail=", ".join(request.extra_inputs),
                help="a .pyc has nothing to link against; a program that "
                     "imports another module resolves it at run time")
        output = request.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(request.artifacts[pyc[0]])
        request.commands.append(["(write)", str(output)])
        return output


class NoToolchain(Toolchain):
    """Write the artifacts out and stop.

    Not a no-op: it is what `--emit` means, and having it here rather than as a
    branch in the driver keeps "produce a program" a single code path with a
    pluggable end.
    """

    name = "none"
    #: NOTHING OF ITS OWN. This writes the backend's artifacts under
    #: whatever names the backend gave them, so the output spelling is
    #: the BACKEND's to claim and never this toolchain's.
    artifacts = ()
    backends = ()
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
    register(PycToolchain())
    register(CPyExtToolchain())
    register(NoToolchain())
