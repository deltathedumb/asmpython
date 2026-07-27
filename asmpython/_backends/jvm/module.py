"""Assemble a whole IRModule into one JVM class."""

from __future__ import annotations

from .classfile import ACC_FINAL, ACC_PUBLIC, CLASS_VERSION_MAJOR, ClassBuilder
from .codegen import (
    DEFAULT_RUNTIME,
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

ALOAD_0 = 0x2A
INVOKESPECIAL = 0xB7


def compile_module(ir_module, class_name: str = DEFAULT_CLASS,
                   class_version: int = CLASS_VERSION_MAJOR,
                   runtime: str = DEFAULT_RUNTIME,
                   annotations: "list[tuple[str, dict]] | None" = None,
                   instantiate: bool = False,
                   runtime_package: str = "asmpython/jvm") -> bytes:
    """Emit one class containing every function in the module.

    Data-section globals become ``static long`` fields holding heap addresses.
    A generated ``<clinit>`` writes each global's bytes into the runtime heap
    at class-initialisation time and stores the address, so ``global_addr``
    lowers to a plain ``getstatic``.

    `annotations` and `instantiate` exist for frameworks that discover and load
    a class rather than being called by it -- a plugin loader that scans for an
    annotation and then constructs what it finds. Both are described entirely
    by the caller: this backend emits what it is told to and knows nothing
    about any particular framework.
    """
    cls = ClassBuilder(class_name, class_version=class_version)
    for descriptor, elements in annotations or []:
        cls.annotate(descriptor, **elements)
    if instantiate:
        # A framework that constructs the class cannot construct a final one
        # with no constructor.
        cls.access &= ~ACC_FINAL
    function_names = {f.name for f in ir_module.funcs}
    FunctionEmitter.cls_functions = {f.name: f for f in ir_module.funcs}
    # try/except landing sites are identified by an id that must be unique
    # across the whole class, not per method.
    FunctionEmitter.next_site_id = 1

    globals_map: dict[str, str] = {}
    for index, global_ in enumerate(getattr(ir_module, "data", []) or []):
        field = f"g{index}_{_java_name(global_.name)}"
        globals_map[global_.name] = field
        cls.add_field(field, "J")

    # Globals the data section never declares -- the exception machinery's
    # `_runtime_exc_msg` and friends -- are declared by the emitters as they
    # meet them, and collected here for <clinit> to initialise.
    runtime_globals: list[str] = []
    for func in ir_module.funcs:
        FunctionEmitter(cls, func, class_name, globals_map, runtime,
                        runtime_globals, runtime_package).emit()

    _emit_clinit(cls, ir_module, class_name, globals_map, runtime_globals, runtime)
    _emit_main(cls, class_name, function_names)
    if instantiate:
        _emit_constructor(cls, class_name, function_names)
    return cls.serialize()


def _entry_name(function_names: set) -> "str | None":
    """The module's entry point, whatever the frontend called it."""
    for candidate in ("main", "__asmpy_module_init", "__asmpy_main"):
        if candidate in function_names:
            return candidate
    return None


def _emit_constructor(cls, class_name: str, function_names: set) -> None:
    """A public no-arg constructor that runs the module entry.

    For frameworks that load a class by instantiating it, where `main` is never
    called. Running the entry from the constructor is what makes "the framework
    made an instance" and "the Python module body ran" the same event.
    """
    method = cls.method("<init>", "()V", access=ACC_PUBLIC)
    method.u1(ALOAD_0)
    method.u1(INVOKESPECIAL)
    method.u2(method.pool.methodref("java/lang/Object", "<init>", "()V"))

    entry = _entry_name(function_names)
    if entry is not None:
        method.u1(INVOKESTATIC)
        method.u2(method.pool.methodref(class_name, _java_name(entry), "()J"))
        method.u1(POP2)
    method.u1(RETURN)
    method.max_locals = 1
    method.max_stack = 4


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


def _emit_clinit(cls, ir_module, class_name: str, globals_map: dict,
                 runtime_globals: "list[str] | None" = None,
                 runtime: str = DEFAULT_RUNTIME) -> None:
    method = cls.method("<clinit>", "()V", access=0x0008)  # ACC_STATIC

    # Runtime-state globals first: a word each, already zero because the bump
    # allocator never reuses memory.
    for name in runtime_globals or []:
        method.u1(LDC2_W)
        method.u2(method.pool.long(8))
        method.u1(INVOKESTATIC)
        method.u2(method.pool.methodref(runtime, "allocate", "(J)J"))
        method.u1(PUTSTATIC)
        method.u2(method.pool.fieldref(class_name, globals_map[name], "J"))

    # The runtime raises, so it has to know where to publish the exception. The
    # cells belong to this class, so the addresses travel this way rather than
    # the runtime owning state the lowering reads as ordinary globals.
    if "_runtime_exc_msg" in globals_map and "_runtime_exc_type" in globals_map:
        for name in ("_runtime_exc_msg", "_runtime_exc_type"):
            method.u1(GETSTATIC)
            method.u2(method.pool.fieldref(class_name, globals_map[name], "J"))
        method.u1(INVOKESTATIC)
        method.u2(method.pool.methodref(runtime, "installExceptionSlots", "(JJ)V"))

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
        method.u2(method.pool.methodref(runtime, "allocate", "(J)J"))
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
            method.u2(method.pool.methodref(runtime, "storeByte", "(JJ)V"))
        method.u1(POP2)  # drop the retained address
    method.u1(RETURN)
    method.max_locals = 2
    method.max_stack = 16


def _emit_main(cls, class_name: str, function_names: set) -> None:
    """A `public static void main(String[])` that calls the module entry."""
    entry = _entry_name(function_names)
    method = cls.method("main", "([Ljava/lang/String;)V")
    if entry is not None:
        method.u1(INVOKESTATIC)
        method.u2(method.pool.methodref(class_name, _java_name(entry), "()J"))
        method.u1(POP2)
    method.u1(RETURN)
    method.max_locals = 1
    method.max_stack = 8
