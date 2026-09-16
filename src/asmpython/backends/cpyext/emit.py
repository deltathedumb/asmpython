"""`cpyext` -- a real CPython extension module: `.so` on Linux, `.pyd` on
Windows, loadable by an ordinary `import` in the host CPython.

WHAT THIS IS NOT. `objects/c/` is asmpython's own dynamic-object runtime --
a tagged union (`apy_obj`), arena-allocated, with no refcounting and no GC
(`docs/INERT-RUNTIME.md`: "there is no garbage collector, and the C runtime
leaks it identically"). It shares nothing with CPython's `PyObject` layout,
and reproducing that layout well enough to hand a real `apy_obj *` to
CPython's own GC-managed heap is not attempted here -- it would mean the
object runtime growing reference counts and a type-object protocol it has
deliberately never needed, for every program, to serve the minority that
wants an extension module.

WHAT THIS IS INSTEAD: the compiled function's BODY still runs entirely
inside asmpython's own arena -- never touching a real `PyObject` -- and
only the BOUNDARY is bridged. Each exported function gets a trampoline that
converts its arguments from real, CPython-refcounted `PyObject*`s into
fresh `apy_value`s (via the ordinary constructors: `apy_from_int`,
`apy_str_copy`, ...), calls the compiled function exactly as it would be
called from other asmpython code, and converts the single returned
`apy_value` back into a fresh, correctly-refcounted `PyObject*` (via
`Py_BuildValue`-equivalent constructors: `PyLong_FromLongLong`,
`PyUnicode_FromStringAndSize`, ...). Nothing crosses the boundary that
either side has to keep alive on the other's terms; every value that
crosses is copied. This is exactly as real an extension module as one
written by hand in C against the same API -- it is not a shim, a wrapper
around another process, or an interpreter embedded in a shared object --
and exactly as limited as that description implies: `int`, `float`, `str`,
`bytes`, `bool` and `None` cross the boundary; a `list`/`dict`/user object
argument or return value does not, yet.

TWO EXPORT SHAPES, drawn straight from the IR's own two calling
conventions (see `frontends/python/lower.py`):

  * a function with no type annotations lowers to the DYNAMIC convention --
    every parameter and the return are `apy_value` (`ptr`), and the first
    parameter is a closure-environment pointer every dynamic function
    except the entry takes (`Lowerer.info.takes_env`), used or not.
    Exported functions never capture anything from an enclosing scope --
    checked below, not assumed -- so `0` is always a safe environment to
    call one with directly, bypassing `apy_call` entirely.
  * a function with a scalar type annotation on every parameter and the
    return (`int`, `float`, `bool`) lowers to the TYPED convention -- plain
    C integers and doubles, no environment, no boxing at all. Cheaper to
    call and cheaper to convert, and offered for exactly that reason: a
    program that annotated its hot function gets a trampoline with no
    `apy_*` calls in it whatsoever.

A function is exported only if it is a genuine TOP-LEVEL `def`, found by
NAME in a re-parse of the recovered source (`source_file_of`, shared with
`pybc`) with a plain positional signature -- no default, `*args`,
`**kwargs`, keyword-only parameter or decorator, all of which change the
calling convention in ways no C signature here models. That re-parse is
also what makes `--library` merely USEFUL rather than required: a module
with no `main` needs it just to compile at all (E0003, "nothing to run"),
but a module that already has one exports its other top-level functions
exactly the same either way -- nested/nested-in-a-class functions never
collide with this because their IR name is a mangled `pyf_...` one
regardless (`frontends/python/lower.py`), never the plain name a re-parse
looks for. Anything that fails the check is left out of the module rather
than guessed at, and named in the refusal if NOTHING ends up exportable.

See the class docstring for the two things this backend leaves to a
toolchain: `-shared -fPIC` and the Python include path (`link/toolchains.py`,
`CPyExtToolchain`) and the `x86_64-linux-cpyext`/`x86_64-windows-cpyext`
targets (`targets/__init__.py`) it needs to be selected at all.
"""
from __future__ import annotations

import ast
import sys

from ...backend.base import (
    Backend, BackendUnsupported, Option, OptionError, Target, register,
    source_file_of,
)
from ...ir import Function, Module
from ...ir import types as T
from ..c.emit import CBackend, _HOSTSVC_GROUPS

#: IR scalar type -> the C type a TYPED trampoline stores an argument or
#: return value in. `ptr` is deliberately absent: a `ptr`-typed parameter on
#: a function that is not otherwise all-`ptr` (the dynamic shape) is a
#: mixed signature this backend does not have a convention for, and
#: `_classify` refuses it rather than guessing which convention applies.
_SCALAR_C = {
    T.I1: "int8_t", T.I8: "int8_t", T.I16: "int16_t",
    T.I32: "int32_t", T.I64: "int64_t",
    T.U8: "uint8_t", T.U16: "uint16_t", T.U32: "uint32_t", T.U64: "uint64_t",
    T.F32: "float", T.F64: "double",
}

#: Names this backend will never treat as an exportable Python function,
#: whatever their linkage: the object runtime, and the two spellings the
#: entry point can end up with (`main`, or `ir_main` after the C backend's
#: own reserved-word rename -- see `backends/c/emit.py:_RESERVED`).
_RESERVED_NAMES = frozenset({"main", "ir_main"})


def _top_level_defs(source_text: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every top-level `def`, safe or not -- the universe `emit()` reports
    against. Kept separate from `_plain_python_params` so a refusal names
    only genuine top-level Python functions, never an internal symbol
    (`pyf_...`-mangled nested functions, spliced `apy_*` helpers) that
    happens to also be in `module.functions`: this backend discovers what
    to export by walking THIS dict, by name, rather than by walking the
    IR and guessing which names are user code.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return {}
    return {node.name: node for node in tree.body
           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _plain_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg] | None:
    """This function's positional parameters, or `None` if its calling
    convention is not one this backend models.

    Positional parameters only -- `posonlyargs` and `args` are the same
    thing here, since every call this backend generates is positional
    anyway. Refused: `async def` (nothing to await into), any decorator
    (it may wrap the function in something whose C symbol is not simply
    the function's own body), and a default, `*args`, `**kwargs` or
    keyword-only parameter (each means a caller can supply fewer or
    differently-shaped arguments than the compiled C signature takes).
    """
    if isinstance(node, ast.AsyncFunctionDef) or node.decorator_list:
        return None
    a = node.args
    if a.kwonlyargs or a.vararg or a.kwarg or a.defaults:
        return None
    return list(a.posonlyargs) + list(a.args)


def _env_is_unused(fn: Function, env: int) -> bool:
    """Whether register `env` (params[0] of a dynamic function) is truly
    dead code, not merely assumed so. A function whose body never reads it
    can be called with `0` in its place with no loss of meaning; one that
    does read it is reaching an enclosing scope this backend has no value
    to give it, so it is refused rather than handed a null pointer it
    would actually dereference.
    """
    for block in fn.blocks:
        for instr in block.instructions:
            if env in instr.args:
                return False
    return True


def _classify(fn: Function) -> tuple[str, list[int]] | None:
    """Which calling convention `fn` uses, and its REAL (Python-visible)
    parameter registers -- with the synthetic environment parameter of the
    dynamic convention already stripped. `None` if the signature is neither
    shape `cpyext` has a convention for (see `_SCALAR_C`'s docstring).
    """
    types = [fn.registers[r] for r in fn.params]
    if types and all(t is T.PTR for t in types) and fn.ret is T.PTR:
        return ("dynamic", fn.params[1:])
    if all(t in _SCALAR_C for t in types) and (fn.ret in _SCALAR_C or fn.ret is T.VOID):
        return ("typed", list(fn.params))
    return None


class CPyExtBackend(Backend):
    name = "cpyext"
    #: C source too -- the extension module it becomes is the
    #: `cpyext` TOOLCHAIN's artifact, not this one's.
    artifacts = (".c",)
    description = ("a real CPython extension module: .so on Linux, .pyd on "
                   "Windows, loadable with an ordinary `import`")
    #: Emits C -- the object runtime, the module's own functions (both via
    #: `CBackend`, reused rather than duplicated) and this backend's own
    #: glue, ALL IN ONE TRANSLATION UNIT. That last part is not a style
    #: choice: `objects_c(static=True, ...)` makes every `apy_*` an
    #: internal-linkage C symbol, invisible to a glue file compiled
    #: separately and linked in -- so the glue is textually appended to the
    #: same file instead. See `emit()`.
    kind = "language"
    self_contained = True
    #: The host's own shape, absent a cross toolchain -- there is no
    #: "AT ALL x86_64" default the way `--target c` has one, because a
    #: `.pyd` and a `.so` are not the same artifact even though one backend
    #: produces both.
    default_target = ("x86_64-windows-cpyext" if sys.platform == "win32"
                      else "x86_64-linux-cpyext")
    #: Reused from `c`: the emitted C IS the c backend's, so it has exactly
    #: what that backend has (a hosted libc; no `net`, no `text` -- see
    #: `backends/c/emit.py`'s own commentary on the two absences).
    host_services = _HOSTSVC_GROUPS
    options = (
        Option("module-name", metavar="NAME", help=(
            "the Python-visible module name (what PyInit_NAME becomes, "
            "and what `import NAME` reaches); default is the source "
            "file's stem")),
    )

    #: Set by `configure()`; a literal `None` here keeps a directly
    #: constructed instance usable in tests, same as `PycBackend`.
    _module_name: str | None = None

    def configure(self, values: dict[str, str], sink) -> "CPyExtBackend":
        name = values.get("module-name")
        if name is not None and not name.isidentifier():
            raise OptionError(
                f"--module-name wants a Python identifier "
                f"(it becomes PyInit_{name}), got {name!r}")
        be = CPyExtBackend()
        be._module_name = name
        return be

    def check_host_services(self, module: Module) -> None:
        # THE SAME CHECK THE `c` BACKEND WOULD MAKE, because the emitted C
        # IS the `c` backend's -- a program this backend cannot host is
        # refused for the same reason and with the same message `--backend
        # c` would give, not a second, differently-worded one.
        CBackend().check_host_services(module)

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        source = source_file_of(module)
        if source is None or not source.text.strip():
            raise BackendUnsupported(
                "the cpyext backend re-parses the module's original source "
                "to check which functions it can safely export, and could "
                "not recover any: no instruction, function or global in "
                "this module carries a real source span.")

        modname = self._module_name
        if modname is None:
            modname = source.path.stem if source.path is not None else module.name
        if not modname.isidentifier():
            raise BackendUnsupported(
                f"cpyext needs a Python-identifier module name for "
                f"PyInit_{modname} -- {modname!r} is not one; pass "
                f"--module-name")

        # DISCOVERED FROM THE SOURCE, NOT FROM THE IR: walking
        # `module.functions` and guessing which names are user code would
        # also turn up every spliced `apy_*` helper and every mangled
        # `pyf_...` nested function, neither of which belongs in a refusal
        # aimed at a person reading their own module. Walking the top-level
        # `def`s instead means a refusal only ever names one.
        exports: list[tuple[Function, str, list[int]]] = []
        skipped: list[tuple[str, str]] = []
        for name, node in sorted(_top_level_defs(source.text).items()):
            if name in _RESERVED_NAMES:
                continue
            fn = module.function(name)
            if fn is None or fn.external:
                # `--library` REFUSES a module with no `main` and nothing
                # else to compile it for, so a top-level `def` genuinely
                # missing from the IR here is almost always dead code a
                # pass removed -- not a bug in this backend, and not this
                # backend's to explain.
                continue
            node_params = _plain_params(node)
            if node_params is None:
                skipped.append((name, "a default value, *args, **kwargs, "
                                       "a keyword-only parameter, `async def` "
                                       "or a decorator"))
                continue
            classified = _classify(fn)
            if classified is None:
                skipped.append((name, "a parameter or return shape cpyext "
                                       "has no calling convention for (mixed "
                                       "typed/dynamic, or a non-scalar typed "
                                       "value)"))
                continue
            kind, real_params = classified
            if len(node_params) != len(real_params):
                skipped.append((fn.name, "an arity that does not match "
                                          "between source and compiled IR"))
                continue
            if kind == "dynamic" and not _env_is_unused(fn, fn.params[0]):
                skipped.append((fn.name, "a reference to a variable in an "
                                          "enclosing scope"))
                continue
            exports.append((fn, kind, real_params))

        if not exports:
            detail = "; ".join(f"{n}: {why}" for n, why in skipped)
            hint = f" ({detail})" if detail else " (no top-level functions found)"
            raise BackendUnsupported(
                "cpyext found nothing it could export as a CPython-callable "
                f"function{hint}. Compile with --library so every top-level "
                "function is exported rather than only main, and keep "
                "exported functions to plain positional parameters.")

        c_artifacts = CBackend().emit(module, target)
        c_source = "\n\n".join(
            data.decode("utf-8") for _, data in sorted(c_artifacts.items()))
        glue = _glue_source(modname, exports)
        # `Python.h` FIRST, ALWAYS: it sets feature-test macros that later
        # standard-library includes (the c backend's own `<stdint.h>` etc.,
        # already inside `c_source`) must see before anything else does.
        merged = f'#include <Python.h>\n\n{c_source}\n\n{glue}\n'
        return {f"{modname}.c": merged.encode("utf-8")}


#: The boundary itself: every conversion a trampoline needs, written once.
#: `O`/`apy_obj`/the `APY_*_K` kind tags are the object runtime's own
#: internals, not its public `APY_API` -- reachable here only because this
#: text is concatenated into the SAME translation unit as that runtime
#: (see `CPyExtBackend.emit`), which is also why this is plain C using
#: them directly rather than a second accessor layer.
_HELPERS = """\
static uintptr_t cpyext_from_pyobject(PyObject *o) {
    if (o == Py_None) return apy_none();
    if (PyBool_Check(o)) return apy_from_bool(o == Py_True ? 1 : 0);
    if (PyLong_Check(o)) {
        long long v = PyLong_AsLongLong(o);
        if (v == -1 && PyErr_Occurred()) return 0;
        return apy_from_int((int64_t)v);
    }
    if (PyFloat_Check(o)) return apy_from_float(PyFloat_AsDouble(o));
    if (PyUnicode_Check(o)) {
        Py_ssize_t n = 0;
        const char *s = PyUnicode_AsUTF8AndSize(o, &n);
        if (!s) return 0;
        return apy_str_copy(s, (int64_t)n);
    }
    if (PyBytes_Check(o))
        return apy_bytes_copy(PyBytes_AS_STRING(o), (int64_t)PyBytes_GET_SIZE(o));
    PyErr_Format(PyExc_TypeError,
                 "a compiled function received a %.200s, which this "
                 "extension cannot convert (supported: None, bool, int, "
                 "float, str, bytes)", Py_TYPE(o)->tp_name);
    return 0;
}

static PyObject *cpyext_to_pyobject(uintptr_t v) {
    switch (O(v)->kind) {
    case APY_NONE_K: Py_RETURN_NONE;
    case APY_BOOL_K:  return PyBool_FromLong((long)apy_as_int(v));
    case APY_INT_K:   return PyLong_FromLongLong((long long)apy_as_int(v));
    case APY_FLOAT_K: return PyFloat_FromDouble(apy_as_float(v));
    case APY_STR_K:
        return PyUnicode_FromStringAndSize(O(v)->v.s.p, (Py_ssize_t)O(v)->v.s.n);
    case APY_BYTES_K:
        return PyBytes_FromStringAndSize(O(v)->v.s.p, (Py_ssize_t)O(v)->v.s.n);
    default:
        PyErr_Format(PyExc_TypeError,
                     "a compiled function returned a value this extension "
                     "cannot convert back to Python (internal kind %d; "
                     "supported: None, bool, int, float, str, bytes)",
                     (int)O(v)->kind);
        return NULL;
    }
}

/* THE NAMES `apy_error_type()` HANDS BACK are the asmpython runtime's own
   exception class names (`objects/c/_exceptions.py`), which happen to be
   spelled the same as CPython's built-in exceptions for exactly the
   constructs both implement -- this is a lookup table, not a parser, and
   anything it does not recognise becomes a plain RuntimeError rather than
   a guess. */
static PyObject *cpyext_exc_type(const char *name) {
    if (!strcmp(name, "ValueError")) return PyExc_ValueError;
    if (!strcmp(name, "TypeError")) return PyExc_TypeError;
    if (!strcmp(name, "KeyError")) return PyExc_KeyError;
    if (!strcmp(name, "IndexError")) return PyExc_IndexError;
    if (!strcmp(name, "ZeroDivisionError")) return PyExc_ZeroDivisionError;
    if (!strcmp(name, "AttributeError")) return PyExc_AttributeError;
    if (!strcmp(name, "OverflowError")) return PyExc_OverflowError;
    if (!strcmp(name, "StopIteration")) return PyExc_StopIteration;
    if (!strcmp(name, "StopAsyncIteration")) return PyExc_StopAsyncIteration;
    if (!strcmp(name, "NotImplementedError")) return PyExc_NotImplementedError;
    if (!strcmp(name, "NameError")) return PyExc_NameError;
    if (!strcmp(name, "RecursionError")) return PyExc_RecursionError;
    if (!strcmp(name, "AssertionError")) return PyExc_AssertionError;
    if (!strcmp(name, "ArithmeticError")) return PyExc_ArithmeticError;
    if (!strcmp(name, "LookupError")) return PyExc_LookupError;
    if (!strcmp(name, "MemoryError")) return PyExc_MemoryError;
    if (!strcmp(name, "OSError")) return PyExc_OSError;
    return PyExc_RuntimeError;
}

/* THE ASMPYTHON RUNTIME'S ERROR STATE IS STICKY AND GLOBAL (`apy_err_type`
   in `objects/c/_core.py`), not an object CPython's GC could be shown --
   so it is read as two strings (type name, message), turned into a real
   Python exception through the ordinary C-API, and cleared, all before
   returning to the interpreter that called in. */
static PyObject *cpyext_raise_from_apy(void) {
    uintptr_t tval = apy_error_type();
    uintptr_t mval = apy_error_message();
    const char *tname = (O(tval)->kind == APY_STR_K) ? O(tval)->v.s.p : "RuntimeError";
    const char *text = (O(mval)->kind == APY_STR_K) ? O(mval)->v.s.p
                                                     : "asmpython runtime error";
    PyErr_SetString(cpyext_exc_type(tname), text);
    apy_error_clear();
    return NULL;
}
"""


def _dynamic_trampoline(cname: str, pyname: str, real_params: list[int]) -> str:
    n = len(real_params)
    lines = [f"static PyObject *{cname}(PyObject *self, PyObject *args) {{",
            f'    if (!PyTuple_Check(args) || PyTuple_GET_SIZE(args) != {n}) {{',
            f'        PyErr_Format(PyExc_TypeError, '
            f'"{pyname}() takes exactly {n} positional argument(s)");',
            "        return NULL;",
            "    }"]
    arg_names = []
    for i in range(n):
        an = f"a{i}"
        arg_names.append(an)
        lines.append(f"    uintptr_t {an} = "
                     f"cpyext_from_pyobject(PyTuple_GET_ITEM(args, {i}));")
        lines.append("    if (PyErr_Occurred()) return NULL;")
    call_args = ", ".join(["0", *arg_names])  # 0: the unused environment.
    lines.append(f"    uintptr_t cpyext_rv = {pyname}({call_args});")
    lines.append("    if (apy_error_occurred()) return cpyext_raise_from_apy();")
    lines.append("    return cpyext_to_pyobject(cpyext_rv);")
    lines.append("}")
    return "\n".join(lines)


def _typed_trampoline(cname: str, pyname: str, real_params: list[int],
                      registers: dict[int, T.Type], ret: T.Type) -> str:
    n = len(real_params)
    lines = [f"static PyObject *{cname}(PyObject *self, PyObject *args) {{",
            f'    if (!PyTuple_Check(args) || PyTuple_GET_SIZE(args) != {n}) {{',
            f'        PyErr_Format(PyExc_TypeError, '
            f'"{pyname}() takes exactly {n} positional argument(s)");',
            "        return NULL;",
            "    }"]
    arg_names = []
    for i, reg in enumerate(real_params):
        ty = registers[reg]
        ctype = _SCALAR_C[ty]
        an = f"a{i}"
        arg_names.append(an)
        lines.append(f"    {ctype} {an};")
        lines.append(f"    {{ PyObject *o = PyTuple_GET_ITEM(args, {i});")
        if ty.is_float:
            lines.append("      double v = PyFloat_AsDouble(o);")
            lines.append("      if (v == -1.0 && PyErr_Occurred()) return NULL;")
        elif ty is T.I1:
            lines.append("      int v = PyObject_IsTrue(o);")
            lines.append("      if (v < 0) return NULL;")
        elif ty.is_signed:
            lines.append("      long long v = PyLong_AsLongLong(o);")
            lines.append("      if (v == -1 && PyErr_Occurred()) return NULL;")
        else:
            lines.append("      unsigned long long v = PyLong_AsUnsignedLongLong(o);")
            lines.append("      if (v == (unsigned long long)-1 "
                         "&& PyErr_Occurred()) return NULL;")
        lines.append(f"      {an} = ({ctype})v; }}")
    call_args = ", ".join(arg_names)
    if ret is T.VOID:
        lines.append(f"    {pyname}({call_args});")
        lines.append("    Py_RETURN_NONE;")
    else:
        rc = _SCALAR_C[ret]
        lines.append(f"    {rc} cpyext_rv = {pyname}({call_args});")
        if ret is T.I1:
            lines.append("    return PyBool_FromLong((long)cpyext_rv);")
        elif ret.is_float:
            lines.append("    return PyFloat_FromDouble((double)cpyext_rv);")
        elif ret.is_signed:
            lines.append("    return PyLong_FromLongLong((long long)cpyext_rv);")
        else:
            lines.append("    return PyLong_FromUnsignedLongLong"
                         "((unsigned long long)cpyext_rv);")
    lines.append("}")
    return "\n".join(lines)


def _glue_source(modname: str,
                 exports: list[tuple[Function, str, list[int]]]) -> str:
    parts = [_HELPERS]
    entries = []
    for fn, kind, real_params in exports:
        cname = f"cpyext_call_{fn.name}"
        if kind == "dynamic":
            parts.append(_dynamic_trampoline(cname, fn.name, real_params))
        else:
            parts.append(_typed_trampoline(
                cname, fn.name, real_params, fn.registers, fn.ret))
        entries.append(f'    {{"{fn.name}", {cname}, METH_VARARGS, NULL}},')
    methods = "\n".join(entries)
    parts.append(f"""\
static PyMethodDef cpyext_methods[] = {{
{methods}
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef cpyext_module = {{
    PyModuleDef_HEAD_INIT, "{modname}", NULL, -1, cpyext_methods,
    NULL, NULL, NULL, NULL
}};

PyMODINIT_FUNC PyInit_{modname}(void) {{
    return PyModule_Create(&cpyext_module);
}}
""")
    return "\n\n".join(parts)


register(CPyExtBackend())
