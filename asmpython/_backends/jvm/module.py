"""Assemble a whole IRModule into one JVM class."""

from __future__ import annotations

from .classfile import CLASS_VERSION_MAJOR, ClassBuilder
from .codegen import (
    FunctionEmitter,
    GETSTATIC,
    INVOKESTATIC,
    LCONST_0,
    LDC2_W,
    POP2,
    PUTSTATIC,
    RETURN,
    RUNTIME,
    UnsupportedIR,
    _java_name,
)

DEFAULT_CLASS = "asmpython/jvm/Program"


def compile_module(ir_module, class_name: str = DEFAULT_CLASS,
                   class_version: int = CLASS_VERSION_MAJOR) -> bytes:
    """Emit one class containing every function in the module.

    Data-section globals become ``static long`` fields holding heap addresses.
    A generated ``<clinit>`` writes each global's bytes into the runtime heap
    at class-initialisation time and stores the address, so ``global_addr``
    lowers to a plain ``getstatic``.
    """
    cls = ClassBuilder(class_name, class_version=class_version)
    function_names = {f.name for f in ir_module.funcs}
    FunctionEmitter.cls_functions = {f.name: f for f in ir_module.funcs}

    globals_map: dict[str, str] = {}
    for index, global_ in enumerate(getattr(ir_module, "data", []) or []):
        field = f"g{index}_{_java_name(global_.name)}"
        globals_map[global_.name] = field
        cls.add_field(field, "J")

    for func in ir_module.funcs:
        FunctionEmitter(cls, func, class_name, globals_map).emit()

    _emit_clinit(cls, ir_module, class_name, globals_map)
    _emit_main(cls, class_name, function_names)
    return cls.serialize()


def _global_bytes(global_) -> bytes:
    """The initial contents of a data-section global.

    A string literal contributes its UTF-8 bytes plus a NUL, matching the
    C-style representation the runtime's string helpers expect. Anything else
    reserves a word.
    """
    value = getattr(global_, "value", None)
    if isinstance(value, str):
        return value.encode("utf-8") + b"\x00"
    if isinstance(value, (bytes, bytearray)):
        return bytes(value) + b"\x00"
    return b"\x00" * 8


def _emit_clinit(cls, ir_module, class_name: str, globals_map: dict) -> None:
    method = cls.method("<clinit>", "()V", access=0x0008)  # ACC_STATIC
    for global_ in getattr(ir_module, "data", []) or []:
        field = globals_map[global_.name]
        payload = _global_bytes(global_)
        # Reserve at least a scratch buffer's worth: several ABI helpers write
        # their result into a global (e.g. int-to-string) rather than
        # allocating, and the literal's own length says nothing about that.
        size = max(len(payload), 64)
        method.u1(LDC2_W)
        method.u2(method.pool.long(size))
        method.u1(INVOKESTATIC)
        method.u2(method.pool.methodref(RUNTIME, "allocate", "(J)J"))
        method.u1(0x5C)  # dup2 -- keep the address for the store below
        method.u1(PUTSTATIC)
        method.u2(method.pool.fieldref(class_name, field, "J"))
        # Write the literal bytes at that address.
        for offset, byte in enumerate(payload):
            method.u1(0x5C)  # dup2 (address)
            method.u1(LDC2_W)
            method.u2(method.pool.long(offset))
            method.u1(0x61)  # ladd
            method.u1(LDC2_W)
            method.u2(method.pool.long(byte))
            method.u1(INVOKESTATIC)
            method.u2(method.pool.methodref(RUNTIME, "storeByte", "(JJ)V"))
        method.u1(POP2)  # drop the retained address
    method.u1(RETURN)
    method.max_locals = 2
    method.max_stack = 16


def _emit_main(cls, class_name: str, function_names: set) -> None:
    """A `public static void main(String[])` that calls the module entry."""
    entry = "main" if "main" in function_names else None
    if entry is None:
        for candidate in ("__asmpy_module_init", "__asmpy_main"):
            if candidate in function_names:
                entry = candidate
                break
    method = cls.method("main", "([Ljava/lang/String;)V")
    if entry is not None:
        method.u1(INVOKESTATIC)
        method.u2(method.pool.methodref(class_name, _java_name(entry), "()J"))
        method.u1(POP2)
    method.u1(RETURN)
    method.max_locals = 1
    method.max_stack = 8
