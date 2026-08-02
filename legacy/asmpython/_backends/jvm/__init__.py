"""asmpython's JVM backend: compiles an IRModule to JVM bytecode.

Emits one class of static methods plus a small Java runtime, packaged as a
runnable jar. Where the x86-64 backend allocates registers and emits machine
code, this one leans on the JVM: every IR value gets its own local slot and the
JIT does the register allocation.

The interesting design point is memory. asmpython's IR assumes a flat address
space (`alloca`/`load`/`store`/`gep` over integer addresses), which the JVM does
not have, so `Memory.java` provides one as a ByteBuffer with a bump allocator.
A pointer is then just a `long` index into it, which is why no generated
bytecode ever handles a JVM reference.

That choice is what forces the rest. Generated code reads container headers
DIRECTLY -- a `for x in xs` loads the list's length from +8 and its buffer from
+16 and indexes that itself, never calling the ABI -- so `Containers.java` uses
the native runtime's exact layouts rather than any convenient JVM structure. A
different layout would not fail; it would read the wrong words.

Exceptions are the other place the JVM's shape shows through. asmpython lowers
`try`/`except` to setjmp/longjmp, which the JVM does not have -- but `athrow`
already unwinds the stack, which is the hard half done for free. What is left
is landing in the right place, so each `setjmp` stamps a module-unique site id
into its jmp_buf and one landing pad per method reads the live handler's id and
jumps to the matching block. An id belonging to another frame is rethrown, so a
function that merely CONTAINS a try does not swallow its caller's exceptions.

Status: scalars, control flow, calls, strings, lists, dicts, classes and
exceptions all match CPython (`tests/jvm_differential.py`, which diffs against
CPython rather than against the x86-64 backend, since two backends agreeing is
also what being wrong the same way looks like).

Loadable by a host
------------------
A jar is often not just run but *loaded* -- by a plugin system, a mod loader, a
container -- which finds a class, checks its annotations, and constructs it.
Four options shape the jar for that, and none of them names a particular
framework: this backend emits what it is told to.

    --jvm-class NAME             the generated class's name
    --jvm-annotation A(k=v)      a runtime-visible class annotation
    --jvm-instantiate            a public no-arg constructor running the module
    --jvm-resource PATH=FILE     a file to carry in the jar
    --jvm-runtime-package PKG    where the bundled runtime is compiled to

The last one matters more than it looks. Two jars carrying the runtime under
the same package are a SPLIT PACKAGE, which the module system rejects outright,
so two independently compiled programs could not be loaded side by side.
Relocating gives each its own copy, which is what makes a self-contained jar
actually self-contained.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .. import register_backend
from ..._compiler.ssa.ir import ModuleBackend
from .classfile import resolve_class_version
from .codegen import DEFAULT_RUNTIME
from .module import DEFAULT_CLASS, compile_module
from . import bindings as _bindings

# `import java` only means something when this backend is in play, so the
# module is contributed by the backend rather than shipped in the core stdlib.
_bindings.install()

production_suitable = False
# This backend packages its own jar in run_backend_link, so no external linker
# is ever invoked. The name still has to be one capability negotiation
# recognises, hence "builtin" rather than something honest like "none".
default_linker = "builtin"

# Declared metadata for `asmpython backends info`, which serialises it as
# JSON -- so `type` is the NAME of a type, not the type object. A bare `str`
# here makes that command fail with "Object of type type is not JSON
# serializable", and nothing reads the field as a callable.
requested_args: list[dict] = [
    {
        "name": "--jvm-class",
        "help": "Fully qualified name of the generated class "
                f"(default {DEFAULT_CLASS.replace('/', '.')})",
        "default": DEFAULT_CLASS.replace("/", "."),
        "type": "str",
    },
    {
        "name": "--jvm-javac",
        "help": "javac used to build the runtime support class (default: javac on PATH)",
        "default": "javac",
        "type": "str",
    },
    {
        "name": "--class-version",
        "help": "Class-file major version to emit (45-69; 52 = Java 8, 65 = Java 21). "
                "Overrides --java-version.",
        "default": "",
        "type": "str",
    },
    {
        "name": "--jvm-annotation",
        "help": "Add a runtime-visible class annotation to the generated class, "
                "e.g. com.example.Plugin(value=demo). Repeatable.",
        "default": "",
        "type": "str",
    },
    {
        "name": "--jvm-resource",
        "help": "Add a file to the output jar: ARCHIVE_PATH=FILE. Repeatable.",
        "default": "",
        "type": "str",
    },
    {
        "name": "--jvm-instantiate",
        "help": "Also emit a public constructor that runs the module body, for "
                "frameworks that load a class by constructing it. An optional "
                "comma-separated parameter-type list says what it takes.",
        "default": None,
        "type": "str",
    },
    {
        "name": "--jvm-runtime-package",
        "help": "Package to compile the bundled runtime into (default asmpython.jvm). "
                "Relocate it so two compiled jars can be loaded side by side.",
        "default": "",
        "type": "str",
    },
    {
        "name": "--java-version",
        "help": "Target Java release (e.g. 8, 17, 21) — emits the highest "
                "class-file version that release produces",
        "default": "",
        "type": "str",
    },
]

_RUNTIME_SOURCE = Path(__file__).with_name("runtime")


def run_backend_codegen(ir: Any, args: dict) -> dict[str, bytes]:
    """IRModule -> the generated program class."""
    class_name = _class_name(args)
    version = resolve_class_version(args.get("class_version"), args.get("java_version"))
    runtime = _runtime_class(args)
    return {
        class_name + ".class": compile_module(
            ir, class_name, version, runtime,
            annotations=parse_annotations(args.get("jvm_annotation")),
            instantiate=args.get("jvm_instantiate"),
            runtime_package=runtime_package(args).replace(".", "/"),
        )
    }


def parse_annotations(specs) -> "list[tuple[str, dict]]":
    """Parse `--jvm-annotation` values into (descriptor, elements) pairs.

    Written Java-side-up rather than as a class-file descriptor, because the
    person passing it is reading a framework's documentation, not the JVM spec:

        com.example.Plugin
        com.example.Plugin(value=demo)
        com.example.Plugin(value=demo, category=tools)

    Only String elements. That covers the marker annotations a loader scans for
    to FIND a class, which is the reason a generated class needs one at all;
    anything richer is better served by a hand-written Java class.
    """
    parsed: list[tuple[str, dict]] = []
    for spec in _as_list(specs):
        text = str(spec).strip()
        if not text:
            continue
        elements: dict[str, str] = {}
        if text.endswith(")") and "(" in text:
            name, _, argument = text[:-1].partition("(")
            for pair in argument.split(","):
                if not pair.strip():
                    continue
                key, sep, value = pair.partition("=")
                if not sep:
                    raise ValueError(
                        f"--jvm-annotation {text!r}: element {pair.strip()!r} needs a "
                        "key=value form"
                    )
                elements[key.strip()] = value.strip().strip("'\"")
        else:
            name = text
        parsed.append(("L" + name.strip().replace(".", "/") + ";", elements))
    return parsed


def parse_resources(specs) -> "dict[str, bytes]":
    """Parse `--jvm-resource ARCHIVE_PATH=FILE` into jar entries.

    A jar is often only loadable when it also carries metadata a framework
    reads -- a descriptor, a service file, a manifest of its own. Naming the
    archive path explicitly keeps that the caller's decision rather than a
    layout this backend invents.
    """
    entries: dict[str, bytes] = {}
    for spec in _as_list(specs):
        text = str(spec).strip()
        if not text:
            continue
        archive_path, sep, source = text.partition("=")
        if not sep:
            raise ValueError(
                f"--jvm-resource {text!r}: expected ARCHIVE_PATH=FILE"
            )
        path = Path(source.strip())
        if not path.is_file():
            raise FileNotFoundError(f"--jvm-resource {text!r}: no such file: {path}")
        entries[archive_path.strip().replace("\\", "/")] = path.read_bytes()
    return entries


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def run_backend_link(objects: list[bytes], args: dict) -> dict[str, bytes]:
    """Package the generated class plus the compiled runtime into a jar.

    The runtime is Java source compiled here rather than checked-in bytecode: a
    committed .class would be an opaque binary nobody can review, and anything
    targeting the JVM already has a toolchain to hand.
    """
    class_name = _class_name(args)
    javac = str(args.get("jvm_javac") or "javac")
    version = resolve_class_version(args.get("class_version"), args.get("java_version"))
    runtime_classes = _build_runtime(javac, version, runtime_package(args))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as jar:
        jar.writestr(
            "META-INF/MANIFEST.MF",
            "Manifest-Version: 1.0\r\n"
            f"Main-Class: {class_name.replace('/', '.')}\r\n\r\n",
        )
        for index, blob in enumerate(objects):
            name = class_name + ".class" if index == 0 else f"{class_name}${index}.class"
            jar.writestr(name, blob)
        for path, blob in runtime_classes.items():
            jar.writestr(path, blob)
        # Last, so a caller-supplied file wins over anything generated above --
        # including the manifest, which a framework may want to own.
        for path, blob in parse_resources(args.get("jvm_resource")).items():
            jar.writestr(path, blob)
    return {"program.jar": buffer.getvalue()}


def _runtime_class(args: dict) -> str:
    """Internal name of the class generated code calls into.

    An explicit --jvm-runtime wins: that names a HOST-supplied class, which
    this backend must not generate or relocate. Otherwise it is the bundled
    Runtime, in whichever package it was relocated to.
    """
    explicit = str(args.get("jvm_runtime") or "").strip()
    if explicit:
        return explicit.replace(".", "/")
    return (runtime_package(args) + ".Runtime").replace(".", "/")


def _class_name(args: dict) -> str:
    raw = args.get("jvm_class") or DEFAULT_CLASS.replace("/", ".")
    return str(raw).replace(".", "/")


DEFAULT_RUNTIME_PACKAGE = "asmpython.jvm"


def runtime_package(args: dict) -> str:
    """The package the bundled runtime is compiled into."""
    return str(args.get("jvm_runtime_package") or DEFAULT_RUNTIME_PACKAGE).strip()


def _build_runtime(javac: str, class_version: int,
                   package: str = DEFAULT_RUNTIME_PACKAGE) -> dict[str, bytes]:
    """Compile the runtime sources, returning {class-file path: bytes}.

    `package` relocates them. Two jars that each bundle the runtime under the
    same package are a SPLIT PACKAGE, which the module system rejects outright
    -- "Modules a and b export package asmpython.jvm" -- so two independently
    compiled programs could not be loaded side by side. Relocating is what lets
    each carry its own copy, which is the point of a self-contained jar.
    """
    if shutil.which(javac) is None:
        raise RuntimeError(
            f"the JVM backend needs {javac!r} to build its runtime support class; "
            "pass --jvm-javac to point at one"
        )
    with tempfile.TemporaryDirectory() as work:
        out = Path(work) / "classes"
        out.mkdir()
        sources = _relocated_sources(Path(work) / "src", package)
        result = subprocess.run(
            [javac, "-nowarn", "--release", str(_java_release(class_version)),
             "-d", str(out), *sources],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("failed to compile the JVM runtime:\n" + result.stderr)
        classes: dict[str, bytes] = {}
        for path in out.rglob("*.class"):
            classes[str(path.relative_to(out)).replace(os.sep, "/")] = path.read_bytes()
        return classes


def _relocated_sources(root: Path, package: str) -> "list[str]":
    """Stage the runtime sources under `package`, returning their paths.

    A textual package rewrite rather than bytecode relocation: the runtime is a
    handful of files in ONE package that reference each other unqualified, so
    changing the declaration is the whole job. Compiling from source is already
    how this backend produces the runtime.
    """
    if package == DEFAULT_RUNTIME_PACKAGE:
        return sorted(str(p) for p in _RUNTIME_SOURCE.rglob("*.java"))

    target = root / Path(*package.split("."))
    target.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for source in sorted(_RUNTIME_SOURCE.rglob("*.java")):
        text = source.read_text(encoding="utf-8")
        text = text.replace(f"package {DEFAULT_RUNTIME_PACKAGE};", f"package {package};")
        # Fully qualified self-references appear in javadoc and in the one
        # place a class names its own package (Runtime extends Containers is
        # unqualified, but `asmpython.jvm.Runtime` shows up in prose).
        text = text.replace(f"{DEFAULT_RUNTIME_PACKAGE}.", f"{package}.")
        destination = target / source.name
        destination.write_text(text, encoding="utf-8")
        staged.append(str(destination))
    return staged


__module_backend__ = ModuleBackend(sys.modules[__name__])
backend = __module_backend__

register_backend("jvm", __module_backend__, aliases=("JVM", "jar"))

__all__ = ["__module_backend__", "backend", "run_backend_codegen", "run_backend_link"]


def _java_release(class_version: int) -> int:
    """The Java release matching a class-file version, floored at 8.

    javac cannot target releases below 8 any more, and the runtime only has to
    be loadable by the JVM that runs the program -- not to match the generated
    class byte for byte.
    """
    return max(8, class_version - 44)


# ── Public low-level surface (asmpython.lllib.jvm) ────────────────────────────
# One opt-in symbol; see asmpython/lllib/__init__.py. A bytecode backend has no
# register allocator, no relocations and no ELF, so it publishes a genuinely
# different shape -- which is the point of the per-backend layer. The universal
# `asmpython.lllib` surface (the neutral IR) still applies to it unchanged.
class __lllib__:                       # noqa: N801  (a namespace, not a class)
    """JVM class-file generation internals."""

    from . import bindings, classfile, codegen, module

    compile_module = staticmethod(run_backend_codegen)
    link_objects = staticmethod(run_backend_link)
