"""The `java` module: calling ordinary Java from compiled Python.

Lives with the JVM backend rather than in `asmpython/stdlib/`, and that is the
point. Calling Java is meaningful to this backend and meaningless to x86-64, so
the core has no business shipping a module for it -- it offers a registry
(`stdlib.register_bindings`) and knows nothing about who fills it. Compile with
a different backend and `import java` is an unknown module, which is the honest
answer.

Usage:

    import java

    ArrayList = java.jclass("java.util.ArrayList")
    items = java.jnew(ArrayList)
    java.jcall_s(items, "add", "hello")
    print(java.jcalls(items, "toString"))

Why the shapes are in the names
-------------------------------
asmpython's FFI declares a fixed arity and a type per argument, so no single
binding can describe a variadic call. `jcall_si` therefore means "call with a
string then an int", and the `s`-prefixed `jcalls_*` return a string rather
than a word.

That is not a workaround, it is the same thing JNI does with
`CallIntMethod`/`CallObjectMethod`, and for the same reason: a 64-bit word
cannot say whether it is the number 5 or the address of a string, and the
caller is the only one who knows. Sugaring this into `items.add("hello")` would
mean teaching the compiler what a Java object is -- backend knowledge in the
core, which is exactly what this arrangement avoids.
"""

from __future__ import annotations

from ...stdlib import Func, register_bindings


def _f(args: "tuple[str, ...]", ret: str, name: str) -> Func:
    return Func(arg_types=args, ret_type=ret, c_name=name)


BINDINGS: dict = {
    # --- lookup -----------------------------------------------------------
    # A class handle, by binary name. Resolved against the runtime's own
    # loader, so a plugin host's classes are visible and not just the JDK's.
    "jclass": _f(("str",), "int", "jclass"),

    # --- construction -----------------------------------------------------
    "jnew": _f(("int",), "int", "jnew"),
    "jnew_s": _f(("int", "str"), "int", "jnew_s"),
    "jnew_i": _f(("int", "int"), "int", "jnew_i"),
    "jnew_o": _f(("int", "int"), "int", "jnew_o"),

    # --- calls returning a word (int, bool, or another handle) ------------
    # The receiver may be an instance handle or a class handle; a class
    # receiver calls the static method, which is why there is no separate
    # jcall_static.
    "jcall": _f(("int", "str"), "int", "jcall"),
    "jcall_s": _f(("int", "str", "str"), "int", "jcall_s"),
    "jcall_i": _f(("int", "str", "int"), "int", "jcall_i"),
    "jcall_o": _f(("int", "str", "int"), "int", "jcall_o"),
    "jcall_ss": _f(("int", "str", "str", "str"), "int", "jcall_ss"),
    "jcall_si": _f(("int", "str", "str", "int"), "int", "jcall_si"),
    "jcall_ii": _f(("int", "str", "int", "int"), "int", "jcall_ii"),
    "jcall_io": _f(("int", "str", "int", "int"), "int", "jcall_io"),

    # --- calls returning a string ----------------------------------------
    "jcalls": _f(("int", "str"), "str", "jcalls"),
    "jcalls_s": _f(("int", "str", "str"), "str", "jcalls_s"),
    "jcalls_i": _f(("int", "str", "int"), "str", "jcalls_i"),
    "jcalls_o": _f(("int", "str", "int"), "str", "jcalls_o"),

    # --- fields -----------------------------------------------------------
    "jfield": _f(("int", "str"), "int", "jfield"),
    "jfields": _f(("int", "str"), "str", "jfields"),

    # --- arrays -----------------------------------------------------------
    "jarray": _f(("int", "int"), "int", "jvm_array"),
    "jarray_get": _f(("int", "int"), "int", "jvm_array_get"),
    "jarray_len": _f(("int",), "int", "jvm_array_length"),

    # --- misc -------------------------------------------------------------
    "jstr": _f(("int",), "str", "jstr"),
    "jnull": _f((), "int", "jnull"),
}


def install() -> None:
    """Make `import java` resolve. Called when this backend is loaded."""
    register_bindings("java", BINDINGS, replace=True)
