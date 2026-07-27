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

from ...stdlib import SUBMODULE_RESOLVER, Func, register_bindings


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
    "jcall_so": _f(("int", "str", "str", "int"), "int", "jcall_so"),
    "jcall_oo": _f(("int", "str", "int", "int"), "int", "jcall_oo"),

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

    # --- implementing a Java interface from Python ------------------------
    # jproxy(interface, callback) -> an object implementing `interface` whose
    # methods call the exported Python function named by `callback`. Callback
    # APIs (listeners, suppliers, consumers) are unreachable without this.
    "jproxy": _f(("str", "str"), "int", "jproxy"),

    # --- misc -------------------------------------------------------------
    "jstr": _f(("int",), "str", "jstr"),
    "jnull": _f((), "int", "jnull"),
}


# --------------------------------------------------------------------------
# `import java.<package> as name`
# --------------------------------------------------------------------------

# The symbol a class attribute lowers to. The class name rides in the symbol
# because the frontend emits `call <name>` and nothing else -- it has no way to
# attach a constant, and no reason to know one is needed. The backend splits it
# back out at codegen (see codegen.emit_call).
NEW_PREFIX = "__jvm_new$"


class JavaPackage(dict):
    """A Java package, resolved a class at a time.

    A plain dict cannot back this: no registry can enumerate the classes in
    `com.google.gson`, and asking the JVM to is neither cheap nor reliable
    (a package is not a closed set). So membership is answered on demand, and
    a name is taken to be a class if it starts with a capital -- the Java
    convention, and the only signal available without loading it.

    Loading eagerly to check would turn a typo into a class-loading error at
    COMPILE time and make an import of an unused class fail a build that does
    not need it.
    """

    def __init__(self, package: str) -> None:
        super().__init__()
        self.package = package

    def _class_name(self, attribute: str) -> str:
        return f"{self.package}.{attribute}" if self.package else attribute

    def __contains__(self, attribute) -> bool:
        return isinstance(attribute, str) and bool(attribute) and attribute[0].isupper()

    def __getitem__(self, attribute):
        if attribute not in self:
            raise KeyError(attribute)
        # Zero-arg construction. A constructor taking arguments still needs
        # java.jnew_s / jnew_i, because a Func declares one fixed arity and
        # this mapping cannot know the class's constructors without loading it.
        return Func(arg_types=(), ret_type="int",
                    c_name=NEW_PREFIX + self._class_name(attribute))

    def get(self, attribute, default=None):
        return self[attribute] if attribute in self else default


def _resolve_submodule(subpath: str):
    """`import java.<subpath>` -> the Java package of that name.

    The subpath is passed through untouched; whether `util.ArrayList` means
    `java.util.ArrayList` is decided at RUNTIME, which can just try both.

    Guessing here does not work. A first-segment test ("is it a JDK package?")
    has to put `util` in the JDK set, and `net` too -- and then
    `java.net.neoforged.neoforge` resolves to `java.net.neoforged.neoforge`,
    which does not exist. The ambiguity is real (`java.net` and `net.neoforged`
    are both genuine), so the only honest resolution is to attempt the load.
    """
    if not subpath:
        return None
    return JavaPackage(subpath)


BINDINGS[SUBMODULE_RESOLVER] = lambda subpath: _resolve_submodule(subpath)


def install() -> None:
    """Make `import java` and `import java.<package>` resolve.

    Called when this backend is loaded. Nothing in the core knows what a Java
    package is; it only knows that the module registered under `java` offered
    to resolve its own subpaths.
    """
    register_bindings("java", BINDINGS, replace=True)
