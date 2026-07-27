"""asmpython's JVM backend: compiles an IRModule to JVM bytecode.

Emits one class of static methods plus a small Java runtime, packaged as a
runnable jar. Where the x86-64 backend allocates registers and emits machine
code, this one leans on the JVM: every IR value gets its own local slot and the
JIT does the register allocation.

The interesting design point is memory. asmpython's IR assumes a flat address
space (`alloca`/`load`/`store`/`gep` over integer addresses), which the JVM does
not have, so `Runtime.java` provides one as a ByteBuffer with a bump allocator.
A pointer is then just a `long` index into it, which is why no generated
bytecode ever handles a JVM reference.

Status: covers the scalar, control-flow and call core of the IR. Container and
object ABI helpers (`_abi_list_*`, `_abi_dict_*`, ...) are not ported yet, so a
program using lists, dicts, classes or exceptions fails with a missing runtime
method rather than misbehaving silently.
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
from ..._compiler.ir import ModuleBackend
from .classfile import resolve_class_version
from .codegen import DEFAULT_RUNTIME
from .module import DEFAULT_CLASS, compile_module

production_suitable = False
# This backend packages its own jar in run_backend_link, so no external linker
# is ever invoked. The name still has to be one capability negotiation
# recognises, hence "builtin" rather than something honest like "none".
default_linker = "builtin"

requested_args: list[dict] = [
    {
        "name": "--jvm-class",
        "help": "Fully qualified name of the generated class "
                f"(default {DEFAULT_CLASS.replace('/', '.')})",
        "default": DEFAULT_CLASS.replace("/", "."),
        "type": str,
    },
    {
        "name": "--jvm-javac",
        "help": "javac used to build the runtime support class (default: javac on PATH)",
        "default": "javac",
        "type": str,
    },
    {
        "name": "--class-version",
        "help": "Class-file major version to emit (45-69; 52 = Java 8, 65 = Java 21). "
                "Overrides --java-version.",
        "default": "",
        "type": str,
    },
    {
        "name": "--java-version",
        "help": "Target Java release (e.g. 8, 17, 21) — emits the highest "
                "class-file version that release produces",
        "default": "",
        "type": str,
    },
]

_RUNTIME_SOURCE = Path(__file__).with_name("runtime")


def run_backend_codegen(ir: Any, args: dict) -> dict[str, bytes]:
    """IRModule -> the generated program class."""
    class_name = _class_name(args)
    version = resolve_class_version(args.get("class_version"), args.get("java_version"))
    runtime = str(args.get("jvm_runtime") or DEFAULT_RUNTIME).replace(".", "/")
    return {class_name + ".class": compile_module(ir, class_name, version, runtime)}


def run_backend_link(objects: list[bytes], args: dict) -> dict[str, bytes]:
    """Package the generated class plus the compiled runtime into a jar.

    The runtime is Java source compiled here rather than checked-in bytecode: a
    committed .class would be an opaque binary nobody can review, and anything
    targeting the JVM already has a toolchain to hand.
    """
    class_name = _class_name(args)
    javac = str(args.get("jvm_javac") or "javac")
    version = resolve_class_version(args.get("class_version"), args.get("java_version"))
    runtime_classes = _build_runtime(javac, version)

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
    return {"program.jar": buffer.getvalue()}


def _class_name(args: dict) -> str:
    raw = args.get("jvm_class") or DEFAULT_CLASS.replace("/", ".")
    return str(raw).replace(".", "/")


def _build_runtime(javac: str, class_version: int) -> dict[str, bytes]:
    """Compile Runtime.java, returning {class-file path: bytes}."""
    if shutil.which(javac) is None:
        raise RuntimeError(
            f"the JVM backend needs {javac!r} to build its runtime support class; "
            "pass --jvm-javac to point at one"
        )
    with tempfile.TemporaryDirectory() as work:
        out = Path(work) / "classes"
        out.mkdir()
        sources = [str(p) for p in _RUNTIME_SOURCE.rglob("*.java")]
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
