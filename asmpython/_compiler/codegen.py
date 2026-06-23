"""x86-64 NASM codegen, parameterized by target OS.

Design notes:
- All values are 64-bit ints. Locals and function args live in stack slots
  reached via RBP-relative addressing.
- Expressions push intermediates onto the runtime stack so that arbitrarily
  nested expressions work without register allocation.
- Comparisons evaluate to 0 / 1.
- String literals are interned in .rodata/.data and emitted with an explicit
  length; print(str) writes that exact length and never relies on a NUL.
- Two builtins exist: print(int) and print(str). Their bodies are emitted
  inline by the target-specific subclass because they differ wildly across
  OSes (Linux uses raw syscalls; Windows calls into msvcrt).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import ast_nodes as A
from .sema import BUILTIN_EXCEPTIONS
from .. import stdlib


# --- Exception-type RTTI -------------------------------------------------------

# Wildcard exception-type id for "untyped" raises (`raise "a string"`, or
# raising a `str` value) -- matches *any* `except` clause, typed or not. This
# preserves the historical "catches anything" behaviour for code that raises
# bare strings while still allowing `except SomeType:` to correctly reject
# exceptions of other concrete types.
EXC_ANY = 0

# Builtin exception types and their CPython parent (for `except LookupError:`
# to also catch a raised `KeyError`, etc.). `None` marks the hierarchy root.
BUILTIN_EXC_PARENTS: dict[str, str | None] = {
    "BaseException": None,
    "Exception": "BaseException",
    "SystemExit": "BaseException",
    "KeyboardInterrupt": "BaseException",
    "ArithmeticError": "Exception",
    "ZeroDivisionError": "ArithmeticError",
    "OverflowError": "ArithmeticError",
    "LookupError": "Exception",
    "IndexError": "LookupError",
    "KeyError": "LookupError",
    "NameError": "Exception",
    "AttributeError": "Exception",
    "TypeError": "Exception",
    "ValueError": "Exception",
    "RuntimeError": "Exception",
    "NotImplementedError": "RuntimeError",
    "AssertionError": "Exception",
    "ImportError": "Exception",
    "OSError": "Exception",
    "FileNotFoundError": "OSError",
    "StopIteration": "Exception",
}
# Fixed ids 1..N in the same order as BUILTIN_EXC_PARENTS (0 reserved for EXC_ANY).
# Written as a literal so _merge_import_bindings can materialize it as a global.
BUILTIN_EXC_IDS: dict[str, int] = {
    "BaseException": 1,
    "Exception": 2,
    "SystemExit": 3,
    "KeyboardInterrupt": 4,
    "ArithmeticError": 5,
    "ZeroDivisionError": 6,
    "OverflowError": 7,
    "LookupError": 8,
    "IndexError": 9,
    "KeyError": 10,
    "NameError": 11,
    "AttributeError": 12,
    "TypeError": 13,
    "ValueError": 14,
    "RuntimeError": 15,
    "NotImplementedError": 16,
    "AssertionError": 17,
    "ImportError": 18,
    "OSError": 19,
    "FileNotFoundError": 20,
    "StopIteration": 21,
    "IOError": 19,  # alias for OSError (same id)
}


# --- Function metadata --------------------------------------------------------


@dataclass
class FuncInfo:
    name: str
    params: list[str]
    locals_: dict[str, int] = field(
        default_factory=dict
    )  # name -> RBP offset (negative)
    local_types: dict[str, str] = field(
        default_factory=dict
    )  # name -> 'int'|'float'|'str'|'list'
    frame_size: int = 0  # bytes to subtract from RSP
    # Default value expressions, one per param (None for required).
    defaults: list = field(default_factory=list)
    # Running RBP offset (negative) used while collecting locals, and whether
    # this is the synthetic module-entry frame. Carried on the FuncInfo so the
    # local-collection helpers can be plain instance methods (no closures /
    # nonlocal) — a self-host requirement.
    offset: int = 0
    is_main: bool = False
    # Names declared `global` in this function: skip frame-slot allocation and
    # access them via the module-global .bss slot instead.
    global_names: set = field(default_factory=set)
    # Nonlocal vars: maps var name -> box-ptr slot name (__nl_box_<name>).
    # Reads/writes to these names go through one pointer indirection.
    nonlocal_boxes: dict = field(default_factory=dict)
    # True when the function's declared return annotation is `-> float`.
    # Lets `return <expr>` promote a non-float result (e.g. an `any`/`int`
    # element read out of an unannotated `list`) to xmm0, since callers of a
    # float-returning function read the result from xmm0.
    ret_is_float: bool = False


# --- Base codegen -------------------------------------------------------------


class Codegen:
    """Common shape; subclasses provide the target-specific prologue,
    print implementations, _exit, and section directives."""

    # Each subclass sets these:
    section_text: str = ""
    section_data: str = ""
    section_rodata: str = ""
    section_bss: str = "section .bss"
    label_main: str = ""  # public entry symbol (e.g. _start on Linux, main on Windows)
    # Human-readable target name, set by each subclass. Used in .asm header
    # comments instead of `self.target_name` (no introspection in the
    # self-host subset).
    target_name: str = "Codegen"

    # GL functions whose real C signature takes/returns GLdouble (64-bit),
    # not the GLfloat (32-bit) every other GL function uses -- see
    # _gen_dynamic_call's `is_gl` float-narrowing for why this distinction
    # matters. Deliberately a short, explicit allowlist of the legacy/
    # fixed-function-era double-precision entry points (core GL 3.3+ added
    # none) rather than trying to infer width from the function name.
    _GL_DOUBLE_FUNCS = frozenset({
        "glDepthRange", "glClearDepth",
        "glVertex2d", "glVertex3d", "glVertex4d",
        "glColor3d", "glColor4d",
        "glNormal3d",
        "glTexCoord1d", "glTexCoord2d", "glTexCoord3d", "glTexCoord4d",
        "glRasterPos2d", "glRasterPos3d", "glRasterPos4d",
        "glRectd",
        "glLoadMatrixd", "glMultMatrixd",
        "glLoadTransposeMatrixd", "glMultTransposeMatrixd",
        "glDepthRangeIndexed",
    })

    def __init__(
        self, mod: A.Module, *, use_runtime_lib: bool = False, entry_path: str | None = None
    ) -> None:
        self.mod = mod
        # If True, skip emitting runtime bodies and assume libasmpython_rt is linked.
        self.use_runtime_lib = use_runtime_lib
        # The compiled program's own source path, for the __file__ dunder.
        # None when compiling a string with no real file (e.g. some test
        # harnesses) -- __file__ then falls back to "".
        self.entry_path = entry_path
        self.lines: list[str] = []
        self.strings: list[tuple[str, str]] = []  # (label, bytes-literal)
        self.floats: list[tuple[str, float]] = []  # (label, value)
        self.label_counter = 0
        self._needs_cwd_buf = False  # set when os.getcwd() is called
        # FFI surface: { asmpython_name: stdlib.Func } across all imports, used
        # for dispatching bare and module-attribute calls. Also any constants
        # imported by `from <mod> import <name>` for direct value substitution.
        self.ffi_funcs: dict = dict(mod.ffi_funcs)
        self.ffi_consts: dict = dict(mod.ffi_consts)
        self.imported_modules: dict = dict(mod.imported_modules)
        # `from module import orig as local` aliases for bundled-source funcs.
        self.func_aliases: dict = dict(getattr(mod, "func_aliases", {}))
        # Set of c_name symbols we'll need `extern` declarations for.
        self.ffi_externs: set[str] = set()
        # Set of c_name symbols that are ACTUALLY CALLED in this program.
        # Used by emit_asmlib_runtime / needs_gui to decide which helpers to
        # emit and whether to link against optional libraries (SDL2, etc.).
        # ffi_externs may be a superset (all imported Func c_names); ffi_called
        # contains only those reached by _gen_ffi_call during codegen.
        self.ffi_called: set[str] = set()
        for fn in self.ffi_funcs.values():
            sym = self._platform_c_name(fn)
            self.ffi_externs.add(sym)
        for mod_bindings in self.imported_modules.values():
            for b in mod_bindings.values():
                if hasattr(b, "c_name"):
                    sym = self._platform_c_name(b)
                    self.ffi_externs.add(sym)
        # gl_import() emits a direct `call SDL_GL_GetProcAddress` via
        # hand-written asm (_emit_get_gl_proc_addr), not through
        # _gen_ffi_call, so it never reaches the auto-registration loops
        # above. Detected here (module body scan, at __init__ time) rather
        # than from inside _gen_gl_import itself: generate()'s extern-line
        # emission runs before entry/function codegen, so adding to
        # ffi_externs from within _gen_gl_import would be too late --
        # nothing would re-scan ffi_externs after that point.
        #
        # The same scan also records which handle variables came from
        # gl_import() specifically (as opposed to import_binary()) into
        # self.gl_import_handles: _gen_dynamic_call narrows float
        # arguments/return values to 32 bits only for these handles, since
        # OpenGL's GLfloat is a 32-bit C float, but asmpython's `float` is
        # always a 64-bit C double -- passing a double's raw bits where a
        # GL entry point expects a float silently corrupts the value (a
        # double 1.0's low 32 bits happen to decode as float 0.0, which is
        # exactly the "glClearColor never visibly does anything" bug this
        # fixes). import_binary()-loaded libraries have no such
        # convention, so they're left as genuine doubles.
        self.gl_import_handles: set[str] = set()
        for s in mod.body:
            if not (isinstance(s, A.Assign) and isinstance(s.value, A.Call)):
                continue
            if s.value.func == "gl_import" and isinstance(s.target, str):
                self.gl_import_handles.add(s.target)
        if self.gl_import_handles:
            self.ffi_externs.add("SDL_GL_GetProcAddress")
            self.ffi_called.add("SDL_GL_GetProcAddress")
        self.funcs: dict[str, FuncInfo] = {}
        # Stack of (continue_label, break_label) pairs for the loop currently
        # being generated. Push on loop entry, pop on exit.
        self.loop_labels: list[tuple[str, str]] = []
        # We need to know which Python functions exist so calls to them can
        # be distinguished from calls to builtins like print().
        for f in mod.funcs:
            self.funcs[f.name] = FuncInfo(
                name=f.name,
                params=list(f.params),
                defaults=list(f.defaults),
                ret_is_float=(
                    f.ret_type is not None and f.ret_type[0] == "float"
                ),
            )
        # Rewrite all Call nodes whose func is an import alias so pre-alloc and
        # emit use the same resolved name (mutates AST func field in place).
        if self.func_aliases:
            self._apply_func_aliases(mod)
        # Module-level variables. A top-level `name = expr` lives in a real
        # .bss slot (a global symbol) instead of the synthetic main frame, so
        # every function can read it — not just module-level code. name -> type.
        # Names also bound (anywhere in module-level code, including nested
        # loops/ifs) as a for-loop variable or tuple-unpack target keep their
        # frame slot, because those codegen paths address frame locals directly.
        bound_in_frame: set[str] = set()
        self._collect_frame_bound(mod.body, bound_in_frame)
        self.global_vars: dict[str, str] = {}
        for s in mod.body:
            if (
                isinstance(s, A.Assign)
                and isinstance(s.target, str)
                and s.target not in bound_in_frame
            ):
                self.global_vars[s.target] = A.expr_type(s.value)
            elif isinstance(s, A.If):
                self._collect_if_globals(s, bound_in_frame, self.global_vars)
        # RTTI: each user class gets a small integer id. Instances are tagged
        # with their id (a hidden `__class__` dict entry) at construction, and
        # isinstance walks the `__class_parents` table to honour inheritance.
        # Built with an explicit loop rather than a dict comprehension over
        # enumerate(...) so this stays self-compilable (asmpython comprehensions
        # don't take enumerate() iterables or tuple targets).
        self.class_ids: dict[str, int] = {}
        cid = 0
        for cls in mod.classes:
            self.class_ids[cls.name] = cid
            cid += 1
        # Lazily-built .rodata table mapping each class id above to a
        # "<class '__main__.Name'>" string, for type(instance) (see
        # _type_name_table_label). None until first requested.
        self.type_name_table: list[str] | None = None
        # Class-level variables that act as static constants: `class C: x = 5`.
        # Each maps "<Class>.<name>" -> (label, default_expr). Emitted as bss
        # globals, initialized at startup, and read/written via ClassName.attr
        # and cls.attr. Only plain class bodies (not @dataclass, whose class
        # vars are per-instance fields) contribute, and only literal/simple
        # defaults that the startup initializer can evaluate.
        self.class_var_labels: dict[str, str] = {}
        self.class_var_defaults: list = []  # (label, default_expr) in emit order
        for cls in mod.classes:
            if getattr(cls, "is_dataclass", False):
                continue
            for cv in getattr(cls, "class_vars", []) or []:
                cvname, _annot, cvdefault = cv
                if cvdefault is None:
                    continue
                label = f"__cv_{cls.name}__{cvname}"
                self.class_var_labels[f"{cls.name}.{cvname}"] = label
                self.class_var_defaults.append((label, cvdefault))
        # import_binary()/.imported dynamic-loading: map each handle variable
        # name to the list of (func_name, FuncDef) decorated `@handle.imported`
        # for it. A handle's import_binary(path) call site resolves every
        # function in its list via GetProcAddress/dlsym immediately, storing
        # each pointer keyed by name on the handle instance — see
        # _gen_constructor-adjacent dynamic-import codegen.
        #
        # Also scans class methods (mod.classes[*].methods), not just
        # top-level mod.funcs: a class can wrap a set of GL bindings behind
        # its own API (e.g. pugtk's GLRenderer3D) instead of forcing every
        # caller to hand-declare the same ~20 top-level @glfns.imported
        # stubs. `handle` (the `@<handle>.imported` decorator's receiver)
        # still has to be a name resolvable where the decorator itself is
        # evaluated -- a module-level `glfns = gl_import()`, since Python
        # evaluates a class's decorators once, at class-definition time,
        # not per-instance. Methods compile with `self` as their first
        # parameter; _gen_dynamic_call's plain_params helper below strips
        # it before marshalling so `self` is never sent to the GL call.
        self.imported_funcs: dict[str, list[tuple[str, A.FuncDef]]] = {}
        for f in mod.funcs:
            for deco in f.decorators:
                if deco.endswith(".imported"):
                    handle_name = deco[: -len(".imported")]
                    self.imported_funcs.setdefault(handle_name, []).append((f.name, f))
        # (class_name, method_name) -> handle_name, the reverse direction:
        # at a `some_instance.glClearColor(...)` call site, `some_instance`
        # has nothing to do with `glfns` (the handle) syntactically -- only
        # `some_instance`'s static class, cross-referenced against this
        # table, says the call should dispatch through `glfns`'s resolved
        # function pointer instead of compiling as an ordinary method call
        # to GLRenderer3D.glClearColor's literal (stub) body.
        self.imported_method_handle: dict[tuple[str, str], str] = {}
        for cls in mod.classes:
            for m in cls.methods:
                for deco in m.decorators:
                    if deco.endswith(".imported"):
                        handle_name = deco[: -len(".imported")]
                        self.imported_funcs.setdefault(handle_name, []).append((m.name, m))
                        self.imported_method_handle[(cls.name, m.name)] = handle_name
        # Exception-type RTTI: builtins get the fixed ids above; user classes
        # deriving (transitively) from a builtin exception get the next ids,
        # assigned in declaration order so output is deterministic.
        self._exc_ids: dict[str, int] = dict(BUILTIN_EXC_IDS)
        self._exc_id_parent: dict[str, int] = {}  # str(child_id) -> parent_id or -1
        for name, parent_name in BUILTIN_EXC_PARENTS.items():
            child_id = BUILTIN_EXC_IDS[name]
            parent_id = BUILTIN_EXC_IDS[parent_name] if parent_name else -1
            self._exc_id_parent[str(child_id)] = parent_id
        self._exc_next_id = max(BUILTIN_EXC_IDS.values()) + 1
        for cls in mod.classes:
            if self._cg_is_exception_class(cls.name):
                self._exc_type_id(cls.name)

    # ---- emit helpers -------------------------------------------------------

    def _user_symbol(self, name: str) -> str:
        """NASM symbol for a user function. A user `def main()` would collide
        with the C entry label, so it's mangled; everything else is verbatim."""
        if name == self.label_main:
            return f"userfn_{name}"
        return name

    def _global_label(self, name: str) -> str:
        return f"__g_{name}"

    def _var_mem(self, name: str, info: "FuncInfo") -> str:
        """NASM memory operand for a user variable: a frame slot when it's a
        local/param, otherwise the module-global .bss slot."""
        if name in info.locals_:
            return f"[rbp{info.locals_[name]:+d}]"
        if name in self.global_vars:
            return f"[rel {self._global_label(name)}]"
        raise NameError(f"undefined variable {name}")

    def _var_type(self, name: str, info: "FuncInfo") -> str:
        if name in info.locals_:
            return info.local_types.get(name, "int")
        if name in self.global_vars:
            return self.global_vars[name]
        return "int"

    def _target_names(self, targets: list) -> list:
        """Flatten a for-loop target list (whose entries may be bare names or
        nested name groups, as in `for i, (a, b) in ...`) into the bare names
        it binds."""
        out: list = []
        for t in targets:
            if isinstance(t, list):
                out.extend(t)
            else:
                out.append(t)
        return out

    def _emit_comp_target_bind(self, targets: list, var: int, info: FuncInfo) -> None:
        """Store the per-iteration element (currently in rax, a list/tuple
        header pointer for unpack targets) into the loop variable(s) of a
        comprehension: a single slot for `var`, or unpacked tuple slots for
        `targets` (mirrors `_gen_for_list`'s unpack branch)."""
        if not targets:
            self.emitf(f"mov [rbp{var:+d}], rax")
            return
        self.emitf(f"mov rdx, [rax+{self.LIST_BUF_OFF}]")  # element buffer
        for j, t in enumerate(targets):
            if isinstance(t, list):
                self.emitf(
                    f"mov r10, [rdx+{j * 8}]",  # nested tuple header
                    f"mov r10, [r10+{self.LIST_BUF_OFF}]",
                )
                for k, nm in enumerate(t):
                    nm_off = info.locals_[nm]
                    self.emitf(f"mov rax, [r10+{k * 8}]", f"mov [rbp{nm_off:+d}], rax")
                continue
            t_off = info.locals_[t]
            self.emitf(f"mov rax, [rdx+{j * 8}]", f"mov [rbp{t_off:+d}], rax")

    def _apply_func_aliases(self, mod: A.Module) -> None:
        """Rewrite all A.Call.func fields that are import aliases to the
        original function name.  Done once before pre-allocation so that slot
        IDs are stable and _cl_walk_expr / _gen_call see the resolved name."""
        aliases: dict = self.func_aliases
        if not aliases:
            return

        def _walk_expr(e) -> None:
            if e is None:
                return
            if isinstance(e, A.Call):
                if e.func in aliases:
                    orig = aliases[e.func]
                    if orig in self.funcs or orig in self.ffi_funcs:
                        e.func = orig
                for a in e.args:
                    _walk_expr(a)
                for _kn, kv in (e.kwargs or []):
                    _walk_expr(kv)
            elif isinstance(e, A.BinOp):
                _walk_expr(e.left)
                _walk_expr(e.right)
            elif isinstance(e, A.Compare):
                for sub in e.operands:
                    _walk_expr(sub)
            elif isinstance(e, A.BoolOp):
                _walk_expr(e.left)
                _walk_expr(e.right)
            elif isinstance(e, A.UnaryOp):
                _walk_expr(e.operand)
            elif isinstance(e, A.IfExp):
                _walk_expr(e.test)
                _walk_expr(e.body)
                _walk_expr(e.orelse)
            elif isinstance(e, (A.ListLit, A.TupleLit, A.SetLit)):
                for el in e.elems:
                    _walk_expr(el)
            elif isinstance(e, A.DictLit):
                for k in e.keys:
                    if k is not None:
                        _walk_expr(k)
                for v in e.values:
                    _walk_expr(v)
            elif isinstance(e, A.Comprehension):
                _walk_expr(e.iter)
                _walk_expr(e.elt)
                if e.cond is not None:
                    _walk_expr(e.cond)
            elif isinstance(e, A.DictComprehension):
                _walk_expr(e.iter)
                _walk_expr(e.key)
                _walk_expr(e.value)
                if e.cond is not None:
                    _walk_expr(e.cond)
            elif isinstance(e, A.MethodCall):
                _walk_expr(e.obj)
                for a in e.args:
                    _walk_expr(a)
            elif isinstance(e, A.Attr):
                _walk_expr(e.obj)
            elif isinstance(e, A.Subscript):
                _walk_expr(e.obj)
                _walk_expr(e.index)

        def _walk_stmts(stmts) -> None:
            for s in stmts:
                if isinstance(s, A.Assign):
                    _walk_expr(s.value)
                elif isinstance(s, A.ExprStmt):
                    _walk_expr(s.expr)
                elif isinstance(s, A.IndexAssign):
                    _walk_expr(s.target)
                    _walk_expr(s.value)
                elif isinstance(s, A.Return):
                    if s.value is not None:
                        _walk_expr(s.value)
                elif isinstance(s, A.If):
                    _walk_expr(s.test)
                    _walk_stmts(s.then)
                    _walk_stmts(s.orelse)
                elif isinstance(s, A.While):
                    _walk_expr(s.test)
                    _walk_stmts(s.body)
                elif isinstance(s, A.For):
                    _walk_expr(s.iter)
                    _walk_stmts(s.body)
                elif isinstance(s, A.Try):
                    _walk_stmts(s.body)
                    _walk_stmts(s.handler)
                    for _types, _bind, hbody in s.extra_handlers:
                        _walk_stmts(hbody)
                    _walk_stmts(s.else_body)
                    _walk_stmts(s.finally_body)
                elif isinstance(s, A.MultiAssign):
                    _walk_expr(s.value)
                elif isinstance(s, A.AugAssign):
                    _walk_expr(s.value)

        _walk_stmts(mod.body)
        for f in mod.funcs:
            _walk_stmts(f.body)

    def _collect_frame_bound(self, stmts: list, acc: set) -> None:
        """Collect names bound by a for-loop variable or tuple-unpack target
        anywhere in `stmts` (recursing into nested blocks). Those codegen paths
        store directly into a frame slot, so such names can't live in .bss."""
        for s in stmts:
            if isinstance(s, A.MultiAssign):
                acc.update(s.targets)
            elif isinstance(s, A.TupleAssign):
                acc.update(t.name for t in s.targets if isinstance(t, A.Name))
            elif isinstance(s, A.For):
                acc.add(s.var)
                acc.update(self._target_names(s.targets))
                self._collect_frame_bound(s.body, acc)
            elif isinstance(s, A.While):
                self._collect_frame_bound(s.body, acc)
            elif isinstance(s, A.If):
                self._collect_frame_bound(s.then, acc)
                self._collect_frame_bound(s.orelse, acc)
            elif isinstance(s, A.Try):
                self._collect_frame_bound(s.body, acc)
                self._collect_frame_bound(s.handler, acc)
                for _types, _bind, hbody in s.extra_handlers:
                    self._collect_frame_bound(hbody, acc)
                self._collect_frame_bound(s.else_body, acc)
                self._collect_frame_bound(s.finally_body, acc)

    def _collect_if_globals(self, stmt: A.If, bound_in_frame: set, out: dict) -> None:
        """Top-level platform-conditional constants (e.g. signal.py's
        `if sys.platform == "win32": SIGABRT: int = 22 else: SIGABRT: int = 6`)
        live in .bss like any other module global, so `signal.SIGABRT` can read
        them via `_gen_attr`'s global_vars lookup. Recurses for elif chains."""
        for branch in (stmt.then, stmt.orelse):
            for s in branch:
                if (
                    isinstance(s, A.Assign)
                    and isinstance(s.target, str)
                    and s.target not in bound_in_frame
                    and s.target not in out
                ):
                    out[s.target] = A.expr_type(s.value)
                elif isinstance(s, A.If):
                    self._collect_if_globals(s, bound_in_frame, out)

    def emit(self, line: str = "") -> None:
        self.lines.append(line)

    def emitf(self, *lines: str) -> None:
        for line in lines:
            self.lines.append("    " + line)

    def label(self, name: str) -> None:
        self.lines.append(name + ":")

    def fresh(self, hint: str) -> str:
        self.label_counter += 1
        return f".L{hint}_{self.label_counter}"

    def intern_float(self, v: float) -> str:
        """Allocate (or reuse) a .rodata label holding a 64-bit double."""
        for label, val in self.floats:
            if val == v and (v != 0 or str(val) == str(v)):  # distinguish -0.0
                return label
        label = f"flt_{len(self.floats)}"
        self.floats.append((label, v))
        return label

    def intern_string(self, s: str) -> tuple[str, int]:
        """Add a string to .data, return (label, byte_length).

        Emits the string as a comma-separated list of byte values for NASM.
        We walk the characters and take `ord(ch)` rather than `s.encode()` so
        this stays self-compilable (asmpython has no bytes type). For the ASCII
        source the compiler emits this is exact; `ord` on a 1-char asmpython str
        already yields its byte value.
        """
        label = f"str_{len(self.strings)}"
        parts: list = []
        for ch in s:
            parts.append(str(ord(ch)))
        body = ",".join(parts) if parts else "0"
        self.strings.append((label, body))
        return label, len(parts)

    def _type_name_table_label(self) -> str:
        """Lazily build a .rodata table mapping each user class's RTTI id
        (see self.class_ids) to a "<class '__main__.Name'>" string, so
        type(instance) can index into it by runtime class id."""
        if self.type_name_table is None:
            table = []
            _tnt_i = 0
            while _tnt_i < len(self.class_ids):
                table.append("")
                _tnt_i += 1
            for name, cid in self.class_ids.items():
                label, _ = self.intern_string(f"<class '__main__.{name}'>")
                table[cid] = label
            self.type_name_table = table
        return "__type_name_table"

    # ---- driver -------------------------------------------------------------

    def generate(self) -> str:
        self.emit(f"; asmpython generated for target = {self.target_name}")
        self.emit("BITS 64")
        self.emit("default rel")
        before = len(self.lines)
        self.emit_externs()
        # Avoid duplicate `extern foo` declarations: the target subclass
        # already emits some. Collect their symbol names (the last token of each
        # `extern ...` line). Built as a list with an explicit loop rather than
        # a set comprehension so this stays self-compilable (asmpython has no
        # set runtime); `already` is only used for membership below.
        already: list = []
        for line in self.lines[before:]:
            stripped = line.strip()
            if stripped.startswith("extern"):
                already.append(stripped.split()[-1])
        # Symbols defined inline by emit_asmlib_runtime must not also be
        # declared `extern` — that would conflict with their label definition.
        inline = self._asmlib_inline_syms()
        for sym in sorted(self.ffi_externs):
            if sym not in already and sym not in inline:
                self.emit(f"extern {sym}")
        self.emit(self.section_text)
        self.emit_entry()
        for f in self.mod.funcs:
            self.emit_function(f)
        # Methods compile as regular functions with mangled names so they
        # don't collide with module-level defs.
        for cls in self.mod.classes:
            for m in cls.methods:
                mangled = A.FuncDef(
                    name=self._method_symbol(cls.name, m.name),
                    params=list(m.params),
                    body=list(m.body),
                    pos=m.pos,
                    defaults=list(m.defaults),
                    param_types=list(m.param_types),
                    asm_body=m.asm_body,
                    # An `@assembly_func` method emits under its mangled symbol
                    # so dispatch (which targets ClassName__method) resolves.
                    asm_symbol=(
                        self._method_symbol(cls.name, m.name)
                        if m.asm_body is not None
                        else None
                    ),
                )
                self.emit_function(mangled)
        self.emit_print_impls()
        self.emit_data_sections()
        self.lines = self._peephole_optimize(self.lines)
        return "\n".join(self.lines) + "\n"

    def _peephole_optimize(self, lines: list[str]) -> list[str]:
        """Drop instructions whose result is immediately overwritten before
        anything reads it: `mov reg, X` followed directly (no label, no
        other instruction) by another `mov reg, Y` to the *same* register
        makes the first write dead — PROVIDED `Y` doesn't itself read `reg`
        (e.g. `mov rdx, [ptr]` / `mov rdx, [rdx+8]` is pointer-chasing, not
        a dead store: the second line's source operand needs the value the
        first line just loaded). Nothing else in straight-line flow can
        observe the first write either way, and a jump targeting the
        second line lands there regardless of whether the first line ran.

        Deliberately conservative: only plain `mov <reg>, <anything>` pairs
        with an exact register-name match, only when truly adjacent (a
        label, comment, or blank line between them is left alone rather
        than reasoned about — this pass runs once near the very end and
        correctness matters far more than squeezing out every dead store).
        """
        GP_REGS = (
            "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
            "eax", "ebx", "ecx", "edx", "esi", "edi",
            "al", "bl", "cl", "dl",
        )

        def mov_dest_and_src(line: str) -> tuple[str, str] | None:
            s = line.strip()
            if not s.startswith("mov "):
                return None
            rest = s[4:]
            comma = rest.find(",")
            if comma == -1:
                return None
            dest = rest[:comma].strip()
            if dest not in GP_REGS:
                return None
            return dest, rest[comma + 1:].strip()

        def src_reads_reg(src: str, reg: str) -> bool:
            # Tokenize on anything that isn't part of a register name so
            # "rdx" inside "[rdx+8]" matches but a coincidental substring
            # inside a longer identifier wouldn't (not a real risk for our
            # fixed register-name set, but cheap to do properly).
            token = ""
            for ch in src:
                if ch.isalnum() or ch == "_":
                    token += ch
                else:
                    if token == reg:
                        return True
                    token = ""
            return token == reg

        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            if i + 1 < n:
                m0 = mov_dest_and_src(lines[i])
                m1 = mov_dest_and_src(lines[i + 1]) if m0 is not None else None
                if (
                    m0 is not None
                    and m1 is not None
                    and m0[0] == m1[0]
                    and not src_reads_reg(m1[1], m0[0])
                ):
                    i += 1  # drop lines[i]; lines[i+1] (now current) overwrites it
                    continue
            out.append(lines[i])
            i += 1
        return out

    def generate_runtime_only(self) -> str:
        """Emit a freestanding `.asm` containing the asmpython runtime helpers and
        nothing else. Used by the `asmpython._runtime.build` step to produce
        `libasmpython_rt_<target>.a`.

        The output declares every `_runtime_*` symbol (plus scratch buffers)
        as `global` so user programs can `extern` them at link time.
        """
        assert not self.use_runtime_lib, "Runtime build must emit bodies, not externs."
        self.emit(f"; asmpython runtime library, target = {self.target_name}")
        self.emit("BITS 64")
        self.emit("default rel")
        # Externs we need from libc (printf, malloc, etc.)
        self.emit_externs()
        # Publish every runtime entry point + the scratch buffers user
        # programs reference.
        # Build the publish list without set ops (self-host: no set runtime).
        publish = list(self.RUNTIME_GLOBALS)
        publish.append("itoa_str_buf")
        publish.append("input_buf")
        publish = sorted(publish)
        for sym in publish:
            self.emit(f"global {sym}")
        self.emit_print_impls()
        return "\n".join(self.lines) + "\n"

    # Symbols the runtime library exposes (functions and globals).
    # Anything emitted by `emit_print_impls`, `emit_dict_runtime`,
    # `emit_string_runtime`, or `emit_exception_runtime` that user programs
    # call or load. A list (not a set) so the self-host subset can iterate it;
    # entries are unique by construction.
    RUNTIME_GLOBALS = [
        # Dict runtime
        "_runtime_zalloc",
        "_runtime_hash_string",
        "_runtime_dict_lookup_slot",
        "_runtime_dict_set",
        "_runtime_dict_get",
        "_runtime_dict_get_default",
        "_runtime_dict_contains",
        "_runtime_dict_grow",
        "_runtime_dict_keys",
        "_runtime_dict_values",
        "_runtime_dict_update",
        "_runtime_dict_items",
        "_runtime_set_subset",
        "_runtime_sort_str",
        "_runtime_sort_int",
        "_runtime_sort_pairs_str",
        "_runtime_sort_pairs_int",
        "_runtime_list_slice",
        "_runtime_list_slice_step",
        "_runtime_list_slice_assign",
        # String runtime
        "_runtime_str_to_int",
        "_runtime_str_concat",
        "_runtime_int_to_base",
        "_runtime_int_to_binary",
        "_runtime_group_digits",
        "_runtime_group_digits_zeropad",
        "_runtime_divmod",
        "_runtime_str_repeat",
        "_runtime_str_eq",
        "_runtime_str_cmp",
        "_runtime_str_char_at",
        "_runtime_str_slice",
        "_runtime_str_slice_step",
        "_runtime_str_contains",
        "_runtime_str_index_of",
        "_runtime_str_index_of_start",
        "_runtime_str_rindex_of",
        "_runtime_str_expandtabs",
        "_runtime_str_count",
        "_runtime_str_starts_with",
        "_runtime_str_ends_with",
        "_runtime_str_removeprefix",
        "_runtime_str_removesuffix",
        "_runtime_str_upper",
        "_runtime_str_lower",
        "_runtime_str_capitalize",
        "_runtime_str_swapcase",
        "_runtime_str_title",
        "_runtime_str_strip",
        "_runtime_str_lstrip",
        "_runtime_str_rstrip",
        "_runtime_str_zfill",
        "_runtime_str_ljust",
        "_runtime_str_rjust",
        "_runtime_str_center",
        "_runtime_str_truncate",
        "_runtime_str_replace",
        "_runtime_str_split",
        "_runtime_str_split_ws",
        "_runtime_str_splitlines",
        "_runtime_str_join",
        "_runtime_str_partition",
        "_runtime_str_rpartition",
        "_runtime_str_rsplit",
        "_runtime_chr",
        "_runtime_str_isdigit",
        "_runtime_str_isalpha",
        "_runtime_str_isalnum",
        "_runtime_str_isspace",
        "_runtime_str_isupper",
        "_runtime_str_islower",
        # Exception runtime + globals
        "_runtime_setjmp",
        "_runtime_longjmp",
        "_runtime_raise",
        "_runtime_handler_top",
        "_runtime_exc_msg",
        "_runtime_exc_type",
        # I/O + collection helpers
        "_runtime_input",
        "_runtime_list_append",
        "_runtime_list_pop",
        "_runtime_list_del",
        "_runtime_list_extend",
        "_runtime_list_repeat",
        "_runtime_list_reverse",
        "_runtime_list_insert",
        "_runtime_dict_clear",
        "_runtime_dict_pop",
    ]

    def emit_externs(self) -> None:
        pass  # Linux has none; Windows overrides.

    def emit_data_sections(self) -> None:
        # rodata: string literals (NUL-terminated) and float constants
        # (raw 8-byte IEEE-754 doubles via NASM's `dq`).
        if self.strings or self.floats:
            self.emit(self.section_rodata)
            for label, body in self.strings:
                self.emit(f"{label}: db {body},0")
            for label, val in self.floats:
                # repr(float) round-trips; special values need hex bit patterns.
                if val != val:  # nan
                    self.emit(f"{label}: dq 0x7ff8000000000000")
                elif val > 0 and val + 1 == val:  # +inf
                    self.emit(f"{label}: dq 0x7ff0000000000000")
                elif val < 0 and val - 1 == val:  # -inf
                    self.emit(f"{label}: dq 0xfff0000000000000")
                else:
                    self.emit(f"{label}: dq {repr(val)}")
            if self.type_name_table:
                self.emit(f"__type_name_table: dq {', '.join(self.type_name_table)}")
        # bss: one zero-initialized 8-byte slot per module-level variable.
        # Module-level code (`main`) writes the real value at startup; every
        # function reads it through the symbol.
        if self.global_vars or self.class_var_labels:
            self.emit("section .bss")
            for name in self.global_vars:
                self.emit(f"{self._global_label(name)}: resq 1")
            for label in self.class_var_labels.values():
                self.emit(f"{label}: resq 1")

    # ---- entry point: top-level statements ----------------------------------

    def emit_entry(self) -> None:
        """Emit the OS-specific entry symbol that runs module-level code."""
        # Build a synthetic "main" FuncInfo for top-level body.
        top = A.FuncDef(name=self.label_main, params=[], body=list(self.mod.body))
        # Reuse the function emitter, but with custom prologue/epilogue.
        info = self._collect_locals(top)
        # Class-variable default expressions are emitted by _emit_init_class_vars
        # using the entry `info`, but _collect_locals only walks mod.body.
        # Walk the defaults here so their scratch slots get allocated.
        for _cv_label, _cv_expr in self.class_var_defaults:
            self._cl_walk_expr(info, _cv_expr)
        # Re-align frame after extra slots.
        _frame2 = -info.offset
        if _frame2 % 16:
            _frame2 += 16 - (_frame2 % 16)
        info.frame_size = _frame2
        self.funcs[self.label_main] = info
        # Scratch globals for sys.argv: emit_entry_prologue stashes the
        # incoming argc/argv here (or zeroes them on targets with no argv),
        # and _emit_build_argv_list turns them into a list[str].
        self.emit(self.section_bss)
        self.emit("_prog_argc: resq 1")
        self.emit("_prog_argv: resq 1")
        self.emit("_sys_argv_list: resq 1")
        self.emit("_argv_hdr: resq 1")
        self.emit("_argv_i: resq 1")
        self.emit("_environ_dict: resq 1")
        self.emit(self.section_text)
        self.label(self.label_main)
        self.emit_entry_prologue(info)
        self._emit_build_argv_list()
        self._emit_init_class_vars(info)
        for stmt in top.body:
            self.gen_stmt(stmt, info)
        self.emit_entry_epilogue(info)

    def _emit_init_class_vars(self, info: FuncInfo) -> None:
        """Initialize class-level variable globals from their default exprs at
        program startup (before module-level code runs). Floats land in xmm0, so
        store the raw bits; everything else is an 8-byte value in rax."""
        for label, default_expr in self.class_var_defaults:
            if A.expr_type(default_expr) == "float":
                self._gen_expr_as_float(default_expr, info, "float")
                self.emitf("movq rax, xmm0", f"mov [rel {label}], rax")
            else:
                self.gen_expr(default_expr, info)
                self.emitf(f"mov [rel {label}], rax")

    def emit_entry_prologue(self, info: FuncInfo) -> None:
        raise NotImplementedError

    def emit_entry_epilogue(self, info: FuncInfo) -> None:
        raise NotImplementedError

    def _emit_build_argv_list(self) -> None:
        """Build sys.argv (a list[str]) from `_prog_argc`/`_prog_argv`, which
        emit_entry_prologue populates from the incoming C `main(argc, argv)`
        registers (or zeroes, on targets with no real argv). Leaves the
        resulting list-header pointer in `_sys_argv_list`."""
        cap_ok = self.fresh("argv_cap_ok")
        copy = self.fresh("argv_copy")
        copy_done = self.fresh("argv_copy_done")

        # cap = max(argc, 4); stash in _argv_hdr until the header is allocated.
        self.emitf(
            "mov rax, [rel _prog_argc]", "cmp rax, 4", f"jge {cap_ok}", "mov rax, 4"
        )
        self.label(cap_ok)
        self.emitf("mov [rel _argv_hdr], rax")

        self._emit_malloc(self.LIST_HEADER)  # rax = header
        self.emitf(
            "mov rbx, [rel _argv_hdr]",
            f"mov qword [rax+{self.LIST_CAP_OFF}], rbx",
            "mov rbx, [rel _prog_argc]",
            f"mov qword [rax+{self.LIST_LEN_OFF}], rbx",
            "mov [rel _argv_hdr], rax",
        )

        # buffer = zalloc(cap * 8)
        self.emitf(
            f"mov rbx, [rax+{self.LIST_CAP_OFF}]",
            "shl rbx, 3",
            "call _runtime_zalloc",
            "mov rcx, [rel _argv_hdr]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov qword [rel _argv_i], 0",
        )

        self.label(copy)
        self.emitf(
            "mov rax, [rel _argv_i]",
            "cmp rax, [rel _prog_argc]",
            f"jge {copy_done}",
            "mov rdx, [rel _prog_argv]",
            "mov rdx, [rdx+rax*8]",
            "mov rcx, [rel _argv_hdr]",
            f"mov rcx, [rcx+{self.LIST_BUF_OFF}]",
            "mov [rcx+rax*8], rdx",
            "inc qword [rel _argv_i]",
            f"jmp {copy}",
        )
        self.label(copy_done)
        self.emitf("mov rax, [rel _argv_hdr]", "mov [rel _sys_argv_list], rax")

    # ---- regular function emission ------------------------------------------

    def emit_function(self, f: A.FuncDef) -> None:
        if f.asm_body is not None:
            self.emit_asm_function(f)
            return
        info = self._collect_locals(f)
        self.funcs[f.name] = info
        self.label(self._user_symbol(f.name))
        self.emit_func_prologue(info)
        self.emit_func_epilogue_label = self.fresh(f"ret_{f.name}")
        for stmt in f.body:
            self.gen_stmt(stmt, info)
        # Fallthrough = implicit return 0.
        self.emitf("xor rax, rax")
        self.label(self.emit_func_epilogue_label)
        self.emit_func_epilogue(info)

    def emit_asm_function(self, f: A.FuncDef) -> None:
        """Emit an `@assembly_func` body verbatim under its symbol label.

        The function's NASM (lifted from its docstring into `asm_body`) is
        responsible for the whole calling convention: arguments arrive in the
        target ABI's integer registers (rdi/rsi/... on System V, rcx/rdx/... on
        Win64 — the same order `_emit_positional_args(start_reg=0)` uses at the
        call site), and the body must `ret` with its result in rax. We provide
        the label and reproduce the instructions exactly; no prologue/epilogue
        is synthesised, so the author has full control.
        """
        symbol = f.asm_symbol or f.name
        self.emit()
        self.emit(f"; ---- assembly_func {f.name} (symbol {symbol}) ----")
        self.label(symbol)
        # Reproduce the body. The docstring is written indented under the def,
        # so we strip each line and re-indent instructions uniformly. A line the
        # author ends with ':' is treated as a label and emitted flush-left so
        # NASM's local-label scoping works as written.
        for raw in (f.asm_body or "").splitlines():
            line = raw.strip()
            if line == "":
                self.emit()
            elif line.endswith(":"):
                self.emit(line)
            else:
                self.emit("    " + line)

    def emit_func_prologue(self, info: FuncInfo) -> None:
        self.emitf("push rbp", "mov rbp, rsp")
        if info.frame_size:
            self.emitf(f"sub rsp, {info.frame_size}")
        self._spill_incoming_args(info)

    def _spill_incoming_args(self, info: FuncInfo) -> None:
        """Move each incoming argument into its frame slot, per
        `_assign_arg_regs`: register-passed args are spilled directly
        (`movsd` for float, `mov` for int/pointer); stack-passed args are
        read off the caller's frame first. Shared by both targets'
        prologues; the stack offset is target-specific."""
        types = [info.local_types.get(p, "int") for p in info.params]
        stack_index = 0
        for p, assign in zip(info.params, self._assign_arg_regs(types)):
            off = info.locals_[p]
            if assign is None:
                # Stack-passed: read from the caller's frame, then store into
                # our local slot. rax is free here (prologue, before any code).
                src = self._incoming_stack_arg_offset(stack_index)
                stack_index += 1
                self.emitf(f"mov rax, [rbp+{src}]", f"mov [rbp{off:+d}], rax")
            else:
                reg, is_xmm = assign
                if is_xmm:
                    self.emitf(f"movsd [rbp{off:+d}], {reg}")
                else:
                    self.emitf(f"mov [rbp{off:+d}], {reg}")

    def emit_func_epilogue(self, info: FuncInfo) -> None:
        self.emitf("mov rsp, rbp", "pop rbp", "ret")

    def _arg_reg(self, i: int) -> Optional[str]:
        raise NotImplementedError

    def _assign_arg_regs(self, types: list) -> list:
        """Assign each parameter/argument position (0-based, in declaration
        or call order) to an ABI register or the stack.

        Returns a list parallel to `types`: each entry is `(reg, is_xmm)`
        for a register-passed slot, or `None` for a stack-passed slot.

        This is the SysV / freestanding scheme: integer/pointer args and
        float args are counted independently, consuming `_int_arg_regs()`
        (rdi, rsi, rdx, rcx, r8, r9) and xmm0-xmm7 respectively. Win64
        overrides this with its positional scheme, where argument position
        N always occupies register slot N (either the Nth integer register
        or xmmN, depending on that argument's type)."""
        int_regs = self._int_arg_regs()
        float_regs = [f"xmm{n}" for n in range(8)]
        result: list = []
        int_idx = 0
        float_idx = 0
        for ty in types:
            if ty == "float":
                if float_idx < len(float_regs):
                    result.append((float_regs[float_idx], True))
                    float_idx += 1
                else:
                    result.append(None)
            else:
                if int_idx < len(int_regs):
                    result.append((int_regs[int_idx], False))
                    int_idx += 1
                else:
                    result.append(None)
        return result

    def _incoming_stack_arg_offset(self, stack_index: int) -> int:
        """Callee-side RBP offset of the `stack_index`-th stack-passed argument
        (0 = the first one beyond the register count).

        After `push rbp; mov rbp, rsp`: saved rbp is at [rbp+0], the return
        address at [rbp+8], and the caller's stack arguments start at [rbp+16].
        On Win64 the 32-byte shadow ("home") space the caller reserves sits
        between the return address and the first stack arg, so they start at
        [rbp+16+32]. SysV has no shadow space. Subclasses override the base.
        """
        return 16 + 8 * stack_index

    def _param_type_from_annot(self, annot: Optional[tuple]) -> Optional[str]:
        """Concrete slot type for a parameter from its annotation descriptor
        (base, el), or None if the annotation doesn't constrain the type.

        Mirrors sema's resolution so the body's loads/stores agree with the
        types sema stamped onto Name nodes.
        """
        if annot is None:
            return None
        base = annot[0]
        if base in ("int", "str", "float", "list", "dict", "tuple"):
            return base
        if base in ("any", "none", "set"):
            return None
        if base in self.mod.classes_sig:
            return f"instance:{base}"
        return None

    def _collect_locals(self, f: A.FuncDef) -> FuncInfo:
        info = FuncInfo(
            name=f.name,
            params=list(f.params),
            defaults=list(f.defaults),
            ret_is_float=(f.ret_type is not None and f.ret_type[0] == "float"),
        )
        # In module-level code (`main`), a write to a global name targets its
        # .bss slot, not a frame slot — so don't give those names a local.
        info.is_main = f.name == self.label_main
        # Nonlocal vars in this lifted inner function: param holds a box ptr;
        # reads/writes go through one pointer indirection.
        #
        # A plain list, not `set(getattr(f, "nonlocal_vars", []))`: a
        # getattr() result is opaque ("any"-typed) to sema, and _gen_set_call
        # treats any "any"-typed set() argument as already dict-shaped
        # (sets are dict-backed) instead of iterating it as a list -- the
        # runtime value here actually IS a LIST_HEADER, so that branch read
        # garbage past the list's allocation as if it were a dict's
        # buf/order_buf fields. Confirmed via gdb on a selfhost rebuild
        # (crashed inside _runtime_dict_lookup_slot on every multi-line
        # function body). nonlocal_vars is already de-duplicated by the
        # pass that produces it, so a list works identically here -- both
        # `for n in nonlocal_list` and `if p in nonlocal_list` are correct
        # without set semantics.
        nonlocal_list: list = list(getattr(f, "nonlocal_vars", []))
        for n in nonlocal_list:
            info.nonlocal_boxes[n] = n  # same slot, just mark for indirection
        # Each local (incl. params) gets an 8-byte slot at a negative RBP offset.
        info.offset = 0
        f_param_types: list = f.param_types
        f_defaults: list = f.defaults
        for i, p in enumerate(f.params):
            info.offset -= 8
            info.locals_[p] = info.offset
            # Param type, in priority order: explicit annotation, then the
            # default's type, then int. This decides the read/write register
            # class (int/pointer via rax vs float via xmm0).
            annot = f_param_types[i] if i < len(f_param_types) else None
            ty = self._param_type_from_annot(annot)
            if ty is None:
                ty = "int"
                if i < len(f_defaults) and f_defaults[i] is not None:
                    ty = A.expr_type(f_defaults[i])  # type: ignore
            # Nonlocal params hold a box pointer, not the actual value — treat
            # as int (pointer) regardless of the annotation.
            if p in nonlocal_list:
                ty = "int"
            info.local_types[p] = ty

        # Walk the body for any name that becomes bound. The walkers below are
        # instance methods (not closures) that thread state through `info` —
        # asmpython can't compile nested functions / nonlocal, and this method
        # is on the self-host path.
        self._cl_walk(info, f.body)
        # Frame size must be 16-byte aligned for ABI compliance before calls.
        frame = -info.offset
        if frame % 16:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        return info

    def _is_closure_factory_call(self, value) -> bool:
        """True if `value` is a Call to a function whose body constructs and
        returns a closure (contains a ClosureBind) -- i.e. calling it
        produces a closure object, not a plain value.

        A.expr_type(value) (sema's static inferred_type) only know about
        the Callable[...] annotation, not this codegen-internal distinction,
        so without this check a variable assigned from such a call never
        gets tagged "closure" -- the call site then takes the plain
        function-pointer-variable path instead of unwrapping the closure's
        [MAGIC, fn_ptr, captured_vars...] list, calling the list's own
        header pointer as if it were code (segfault).
        """
        if not isinstance(value, A.Call) or not isinstance(value.func, str):
            return False
        for ff in self.mod.funcs:
            if ff.name == value.func:
                for fs in ff.body:
                    if isinstance(fs, A.ClosureBind):
                        return True
                return False
        return False

    def _cl_define(self, info: FuncInfo, name: str, ty: str = "int") -> None:
        """Reserve an 8-byte frame slot for `name` (no-op if already present).
        Promotes an int slot to a wider type if a later use reveals it."""
        if info.is_main and name in self.global_vars:
            # Module global: lives in .bss, addressed by symbol -- no frame
            # slot needed. But the type recorded for it in the initial
            # pre-scan (the assigned expression's static return type) can't
            # know about codegen-internal tags like "closure", which only
            # get assigned here, later. Without promoting it too, a closure
            # bound to a module-level global never gets tagged "closure",
            # so the call site falls through to the plain
            # function-pointer-variable call path instead of unwrapping the
            # closure's [MAGIC, fn_ptr, captured_vars...] list -- calling
            # the list's own header pointer as if it were code (segfault).
            if ty != "int" and self.global_vars[name] != ty:
                self.global_vars[name] = ty
            return
        if name in info.global_names:
            return  # declared `global` in this function
        if name not in info.locals_:
            info.offset -= 8
            info.locals_[name] = info.offset
            info.local_types[name] = ty
        elif info.local_types.get(name) == "int" and ty != "int":
            info.local_types[name] = ty

    def _cl_define_bytes(self, info: FuncInfo, name: str, n_bytes: int) -> None:
        """Reserve an arbitrarily-sized slot (e.g. 200-byte jmp_buf).
        Rounds up to a multiple of 8 so subsequent 8-byte locals stay aligned."""
        if name in info.locals_:
            return
        rounded = (n_bytes + 7) & ~7
        info.offset -= rounded
        info.locals_[name] = info.offset
        info.local_types[name] = "buf"

    def _cl_walk_expr(self, info: FuncInfo, expr) -> None:
        # Pre-allocate a scratch slot per ListLit / DictLit so codegen
        # never has to extend the frame at emit-time.
        if isinstance(expr, A.ListLit):
            self._cl_define(info, f"__listlit_{id(expr)}")
            for el in expr.elems:
                self._cl_walk_expr(info, el)
        elif isinstance(expr, A.Comprehension):
            # Result list header, source iterable, cached length, index,
            # and a scratch for each appended value; plus the loop var.
            self._cl_define(info, f"__comp_res_{id(expr)}")
            self._cl_define(info, f"__comp_iter_{id(expr)}")
            self._cl_define(info, f"__comp_stop_{id(expr)}")
            self._cl_define(info, f"__comp_idx_{id(expr)}")
            self._cl_define(info, f"__comp_val_{id(expr)}")
            # enumerate(xs) in comprehension needs an extra counter slot.
            if (
                isinstance(expr.iter, A.Call)
                and expr.iter.func == "enumerate"
                and expr.targets
            ):
                self._cl_define(info, f"__comp_enum_ctr_{id(expr)}", "int")
            # Loop variable inherits the iterable's element kind.
            var_ty = "int"
            if A.expr_type(expr.iter) == "list":
                if isinstance(expr.iter, A.Name):
                    var_ty = expr.iter.list_el_type
                elif isinstance(expr.iter, A.ListLit):
                    var_ty = expr.iter.el_type
                else:
                    var_ty = getattr(expr.iter, "list_el_type", "int")
            if expr.targets:
                # `for a, b in <iter>`: each flattened target binds to a slot
                # of the per-iteration element (opaque kind), mirroring
                # A.For's unpack.
                for nm in self._target_names(expr.targets):
                    self._cl_define(info, nm, "any")
            else:
                # If the loop variable name shadows a module global, _cl_define
                # would skip allocation (globals live in .bss). Force-allocate
                # a mangled slot instead, then temporarily expose it under the
                # original name during body evaluation (done in _gen_comprehension).
                if info.is_main and expr.var in self.global_vars:
                    mangle = f"__compvar_{id(expr)}_{expr.var}"
                    if mangle not in info.locals_:
                        info.offset -= 8
                        info.locals_[mangle] = info.offset
                        info.local_types[mangle] = var_ty
                    expr._comp_var_slot = mangle
                    expr._comp_var_shadows_global = True
                else:
                    self._cl_define(info, expr.var, var_ty)
                    expr._comp_var_slot = expr.var
                    expr._comp_var_shadows_global = False
            self._cl_walk_expr(info, expr.iter)
            # Instance-iterable comprehension: needs setjmp buffer + saved
            # exception slots, mirroring the For instance-iter path.
            if A.expr_type(expr.iter).startswith("instance:"):
                self._cl_define_bytes(info, f"__comp_inst_buf_{id(expr)}", 200)
                self._cl_define(info, f"__comp_inst_parent_{id(expr)}", "int")
                self._cl_define(info, f"__comp_inst_prev_exc_{id(expr)}", "int")
                self._cl_define(info, f"__comp_inst_prev_exc_type_{id(expr)}", "int")
            # Direct field access, not getattr(expr, "...", []): expr is
            # already confirmed isinstance(expr, A.Comprehension) here, and
            # all four extra_for_* fields are always-present `list`-typed
            # fields on it. A getattr() result is opaque ("any"-typed) to
            # sema regardless of the real field's type, which makes len()
            # compile as strlen() (the only fallback codegen's len() has
            # for an "any"-typed argument) instead of a real list-length
            # read -- same bug class fixed elsewhere in this function for
            # target_types/free_vars/nonlocal_vars.
            ef_vars = expr.extra_for_vars
            ef_targets_l = expr.extra_for_targets
            ef_iters = expr.extra_for_iters
            ef_conds_l = expr.extra_for_conds
            for ef_idx in range(len(ef_iters)):
                ef_evar = ef_vars[ef_idx] if ef_idx < len(ef_vars) else ""
                ef_emulti = ef_targets_l[ef_idx] if ef_idx < len(ef_targets_l) else []
                ef_iter = ef_iters[ef_idx]
                ef_cond = ef_conds_l[ef_idx] if ef_idx < len(ef_conds_l) else None
                self._cl_define(info, f"__comp_ef_it_{id(expr)}_{ef_idx}")
                self._cl_define(info, f"__comp_ef_stop_{id(expr)}_{ef_idx}")
                self._cl_define(info, f"__comp_ef_idx_{id(expr)}_{ef_idx}")
                if ef_emulti:
                    for nm in self._target_names(ef_emulti):
                        if nm not in info.locals_:
                            self._cl_define(info, nm, "any")
                elif ef_evar and ef_evar not in info.locals_:
                    self._cl_define(info, ef_evar)
                self._cl_walk_expr(info, ef_iter)
                if ef_cond is not None:
                    self._cl_walk_expr(info, ef_cond)
            self._cl_walk_expr(info, expr.elt)
            if expr.cond is not None:
                self._cl_walk_expr(info, expr.cond)
        elif isinstance(expr, A.DictComprehension):
            # Result dict header, source iterable, cached length, index,
            # plus key/value scratch and the loop var.
            self._cl_define(info, f"__dcomp_res_{id(expr)}")
            self._cl_define(info, f"__dcomp_iter_{id(expr)}")
            self._cl_define(info, f"__dcomp_stop_{id(expr)}")
            self._cl_define(info, f"__dcomp_idx_{id(expr)}")
            self._cl_define(info, f"__dcomp_key_{id(expr)}")
            # enumerate(xs) in dict comprehension needs extra counter slot.
            if (
                isinstance(expr.iter, A.Call)
                and getattr(expr.iter, "func", None) == "enumerate"
            ):
                self._cl_define(info, f"__dcomp_enum_ctr_{id(expr)}")
            # zip(A, B, ...) in dict comprehension: per-iterable pointer + stop + idx.
            if (
                isinstance(expr.iter, A.Call)
                and getattr(expr.iter, "func", None) == "zip"
            ):
                nz = len(expr.iter.args)
                self._cl_define(info, f"__dcomp_zip_stop_{id(expr)}")
                self._cl_define(info, f"__dcomp_zip_i_{id(expr)}")
                for _k in range(nz):
                    self._cl_define(info, f"__dcomp_zip_{_k}_{id(expr)}")
            var_ty = "int"
            if A.expr_type(expr.iter) == "list":
                if isinstance(expr.iter, A.Name):
                    var_ty = expr.iter.list_el_type
                elif isinstance(expr.iter, A.ListLit):
                    var_ty = expr.iter.el_type
                else:
                    var_ty = getattr(expr.iter, "list_el_type", "int")
            if expr.targets:
                for nm in self._target_names(expr.targets):
                    self._cl_define(info, nm, "any")
            else:
                self._cl_define(info, expr.var, var_ty)
            self._cl_walk_expr(info, expr.iter)
            self._cl_walk_expr(info, expr.key)
            self._cl_walk_expr(info, expr.value)
            if expr.cond is not None:
                self._cl_walk_expr(info, expr.cond)
        elif isinstance(expr, A.TupleLit):
            # One scratch slot to park the header pointer across the two
            # mallocs (mirrors ListLit). Tuples reuse the list layout.
            self._cl_define(info, f"__tuplelit_{id(expr)}")
            for el in expr.elems:
                self._cl_walk_expr(info, el)
        elif isinstance(expr, A.DictLit):
            self._cl_define(info, f"__dictlit_{id(expr)}")
            self._cl_define(info, f"__dictlit_key_{id(expr)}")
            for k in expr.keys:
                if k is not None:
                    self._cl_walk_expr(info, k)
            for v in expr.values:
                self._cl_walk_expr(info, v)
        elif isinstance(expr, A.SetLit):
            # A set is a dict keyed by its members (dummy value 1), so it
            # needs the same header + per-element key scratch slots a dict
            # literal uses.
            self._cl_define(info, f"__setlit_{id(expr)}")
            self._cl_define(info, f"__setlit_key_{id(expr)}")
            for el in expr.elems:
                self._cl_walk_expr(info, el)
        elif isinstance(expr, A.BinOp):
            # String concat / repeat needs a scratch slot to park the left
            # operand across the right-side evaluation (which may call).
            lt, rt = A.expr_type(expr.left), A.expr_type(expr.right)
            if "str" in (lt, rt):
                self._cl_define(info, f"__binstr_{id(expr)}")
            # List concat (list + list) needs a slot to park the copy while
            # the right operand is evaluated (right eval may contain calls).
            if expr.op == "+" and "list" in (lt, rt):
                self._cl_define(info, f"__listcat_{id(expr)}")
            # List repeat (list * int) needs a slot for the list operand.
            if expr.op == "*" and "list" in (lt, rt):
                self._cl_define(info, f"__listrep_{id(expr)}")
            # An instance operand may overload the operator via a dunder
            # method (`Path / "sub"` -> __truediv__): park `self` across the
            # other operand's evaluation (which may itself call).
            if lt.startswith("instance:") or rt.startswith("instance:"):
                self._cl_define(info, f"__binop_lhs_{id(expr)}")
            # Float arithmetic (a - b, a * b, ...) needs a scratch slot to
            # park the left operand (in xmm0) across the right operand's
            # evaluation -- which may itself be or contain a call (e.g.
            # `f0 + (1.0 - f0) * math.pow(x, 5.0)`). A raw `sub rsp, 8` /
            # `[rsp]` push (rather than this rbp-relative slot) is NOT safe
            # here: an FFI call evaluated for the right operand adjusts rsp
            # itself (shadow space / stack-passed args), so by the time
            # control returns, `[rsp]` no longer points at the spilled left
            # operand -- silently reading garbage instead (confirmed bug:
            # `f0 + (1.0 - f0) * math.pow(1.0 - ct, 5.0)` produced ~75000
            # instead of ~0.04).
            if ("str" not in (lt, rt) and not lt.startswith("instance:")
                    and not rt.startswith("instance:")
                    and ("float" in (lt, rt) or expr.op == "/")):
                self._cl_define(info, f"__binfloat_{id(expr)}", "float")
            # Set algebra (`|`, `&`, `-`) builds a fresh set, mirroring
            # set.union/intersection/difference's scratch slots. Dict union
            # (`d1 | d2`, PEP 584) reuses the same "union" scratch slots.
            if (lt == "set" and rt == "set" and expr.op in ("|", "&", "-")) or (
                lt == "dict" and rt == "dict" and expr.op == "|"
            ):
                self._cl_define(info, f"__sm_other_{id(expr)}")
                self._cl_define(info, f"__sm_new_{id(expr)}")
                self._cl_define(info, f"__sm_keys_{id(expr)}")
                self._cl_define(info, f"__sm_idx_{id(expr)}")
                self._cl_define(info, f"__sm_key_{id(expr)}")
            self._cl_walk_expr(info, expr.left)
            self._cl_walk_expr(info, expr.right)
        elif isinstance(expr, A.Compare):
            # Two str operands with ==/!= or in/not in -> runtime call
            # needs a scratch slot to park the lhs across the rhs eval.
            if len(expr.ops) == 1 and len(expr.operands) == 2:
                op = expr.ops[0]
                lt, rt = (
                    A.expr_type(expr.operands[0]),
                    A.expr_type(expr.operands[1]),
                )
                if (
                    op in ("==", "!=", "<", "<=", ">", ">=")
                    and lt in ("str", "any")
                    and rt in ("str", "any")
                    and "str" in (lt, rt)
                ) or (
                    op in ("==", "!=")
                    and getattr(expr, "_map_val_str_cmp", False)
                ):
                    self._cl_define(info, f"__strcmp_{id(expr)}")
                elif (
                    op in ("==", "!=")
                    and getattr(expr, "dunder_owner", None) is not None
                ):
                    # `instance == instance` dispatched to a user `__eq__`:
                    # park lhs across rhs evaluation, like a binop dunder.
                    self._cl_define(info, f"__cmpeq_lhs_{id(expr)}")
                elif (
                    op in ("<", "<=", ">", ">=")
                    and getattr(expr, "dunder_owner", None) is not None
                ):
                    # `a < b` etc. dispatched to user __lt__/__le__/__gt__/__ge__
                    self._cl_define(info, f"__cmpord_lhs_{id(expr)}")
                elif op in ("<=", ">=", "<", ">") and lt == "set" and rt == "set":
                    # Set subset/superset comparisons (_gen_compare's
                    # _runtime_set_subset call).
                    self._cl_define(info, f"__setcmp_{id(expr)}")
                    if op in ("<", ">"):
                        self._cl_define(info, f"__setcmp_eq_{id(expr)}")
                elif (
                    op in ("in", "not in")
                    and lt in ("str", "any")
                    and rt == "str"
                ):
                    self._cl_define(info, f"__strin_{id(expr)}")
                elif op in ("in", "not in") and rt in ("list", "tuple"):
                    # Element type drives slot kind (float needs xmm-sized
                    # spill, but our locals are 8 bytes which matches).
                    # Tuples reuse the list layout, so the scan is identical.
                    self._cl_define(info, f"__listin_{id(expr)}")
                elif op in ("in", "not in") and rt == "dict":
                    self._cl_define(info, f"__dictin_{id(expr)}")
                elif op in ("in", "not in") and rt == "set":
                    # Sets are dicts under the hood, so membership reuses the
                    # dict-membership scratch slot + helper.
                    self._cl_define(info, f"__dictin_{id(expr)}")
                elif (
                    op in ("in", "not in")
                    and getattr(expr, "dunder_contains_owner", None) is not None
                ):
                    # `x in obj` dispatched to __contains__: park needle
                    # across container eval.
                    self._cl_define(info, f"__contains_needle_{id(expr)}")
                elif (
                    op in ("in", "not in")
                    and (rt in ("any", "int") or rt.startswith("instance:"))
                    and lt in ("str", "any", "int")
                ):
                    # Membership against an opaque (dict-backed) value.
                    self._cl_define(info, f"__dictin_{id(expr)}")
                elif op in ("in", "not in"):
                    # Catch-all: allocate a dict-in slot for any remaining
                    # in/not-in (e.g. list-in-list, unusual LHS types). The
                    # codegen fallback routes through _gen_dict_in.
                    self._cl_define(info, f"__dictin_{id(expr)}")
            # Float comparison (a < b, a == math.pow(...), chained a < b < c,
            # ...) needs a scratch slot per spilled LHS, same reasoning as
            # __binfloat_ above: a raw `sub rsp, 8` / `[rsp]` push isn't
            # safe across evaluating an operand that is or contains an FFI
            # call. One slot per comparison position (_gen_compare spills
            # once per chained `op`, not just once for the whole chain).
            if not any(op in ("is", "is not") for op in expr.ops):
                if any(A.expr_type(o) == "float" for o in expr.operands):
                    for ci in range(len(expr.ops)):
                        self._cl_define(info, f"__cmpfloat_{id(expr)}_{ci}", "float")
            for o in expr.operands:
                self._cl_walk_expr(info, o)
        elif isinstance(expr, A.BoolOp):
            self._cl_define(info, f"__boolop_{id(expr)}")
            self._cl_walk_expr(info, expr.left)
            self._cl_walk_expr(info, expr.right)
        elif isinstance(expr, A.IfExp):
            self._cl_walk_expr(info, expr.test)
            self._cl_walk_expr(info, expr.body)
            self._cl_walk_expr(info, expr.orelse)
        elif isinstance(expr, A.UnaryOp):
            self._cl_walk_expr(info, expr.operand)
        elif isinstance(expr, A.Call):
            # FFI call needs one scratch slot per arg.
            if expr.func in self.ffi_funcs:
                fn = self.ffi_funcs[expr.func]
                for k in range(len(expr.args)):
                    self._cl_define(
                        info,
                        f"__ffi_arg_{id(fn)}_{k}",
                        "float" if fn.arg_types[k] == "float" else "int",
                    )
            # Constructor needs a slot to park the freshly-allocated
            # instance ptr across the __init__ call.
            if expr.func in self.mod.classes_sig:
                self._cl_define(info, f"__ctor_inst_{id(expr)}")
                self._cl_define(info, f"__ctor_tmp_{id(expr)}")
            # getattr(obj, "name", default) parks the default across the
            # object's evaluation (which may itself call).
            if expr.func == "getattr" and len(expr.args) == 3:
                self._cl_define(info, f"__getattr_def_{id(expr)}")
            # getattr/hasattr/setattr park the (possibly dynamic) name across
            # the object's (and for setattr, the value's) evaluation.
            if expr.func in ("getattr", "hasattr"):
                self._cl_define(info, f"__{expr.func}_name_{id(expr)}")
            if expr.func == "setattr":
                self._cl_define(info, f"__setattr_name_{id(expr)}")
            if expr.func == "gl_resolve":
                self._cl_define(info, f"__glresolve_dict_{id(expr)}")
                self._cl_define(info, f"__glresolve_ptr_{id(expr)}")
                self._cl_define(info, f"__setattr_val_{id(expr)}")
            # int(s, base) parks the base across the string's evaluation.
            if expr.func == "int" and len(expr.args) == 2:
                self._cl_define(info, f"__int_base_{id(expr)}")
            # range(...) as a value parks start/stop across arg evaluation.
            if expr.func == "range":
                self._cl_define(info, f"__range_a_{id(expr)}")
                self._cl_define(info, f"__range_b_{id(expr)}")
            # sorted(xs, key=..., reverse=...): a `key=` callable builds a
            # parallel "keys" list (one slot set), `reverse=` parks the
            # (sorted) header across evaluating the reverse condition.
            if expr.func == "sorted" and getattr(expr, "sort_key", None) is not None:
                self._cl_define(info, f"__sortkey_elems_{id(expr)}")
                self._cl_define(info, f"__sortkey_fn_{id(expr)}")
                self._cl_define(info, f"__sortkey_keys_{id(expr)}")
                self._cl_define(info, f"__sortkey_n_{id(expr)}")
                self._cl_define(info, f"__sortkey_i_{id(expr)}")
            if expr.func == "sorted" and getattr(expr, "sort_reverse", None) is not None:
                self._cl_define(info, f"__sortrev_hdr_{id(expr)}")
            # min(xs, key=...) / max(xs, key=...) (or plain min/max over a
            # str list, which also needs the general scan): best-(elem, key)
            # tracking slots.
            if expr.func in ("min", "max") and len(expr.args) >= 3:
                self._cl_define(info, f"__mmvar_best_{id(expr)}")
            if expr.func in ("min", "max") and len(expr.args) == 1:
                arg0 = expr.args[0]
                if isinstance(arg0, A.Name):
                    el_kind = arg0.list_el_type
                elif isinstance(arg0, A.ListLit):
                    el_kind = arg0.el_type
                else:
                    el_kind = "int"
                if getattr(expr, "sort_key", None) is not None or el_kind == "str":
                    self._cl_define(info, f"__mmkey_fn_{id(expr)}")
                    self._cl_define(info, f"__mmkey_n_{id(expr)}")
                    self._cl_define(info, f"__mmkey_i_{id(expr)}")
                    self._cl_define(info, f"__mmkey_buf_{id(expr)}")
                    self._cl_define(info, f"__mmkey_best_elem_{id(expr)}")
                    self._cl_define(info, f"__mmkey_best_key_{id(expr)}")
                    self._cl_define(info, f"__mmkey_cur_elem_{id(expr)}")
                    self._cl_define(info, f"__mmkey_cur_key_{id(expr)}")
            # `key=`/`reverse=` expressions may themselves need scratch slots
            # (e.g. a name-bound key function, or a comparison reverse=).
            if getattr(expr, "sort_key", None) is not None:
                self._cl_walk_expr(info, expr.sort_key)
            if getattr(expr, "sort_reverse", None) is not None:
                self._cl_walk_expr(info, expr.sort_reverse)
            # round(x, ndigits): needs a slot to spill x across pow() call.
            if expr.func == "round" and len(expr.args) >= 2:
                self._cl_define(info, f"__round_nd_{id(expr)}")
            # bytes()/bytearray(): always builds a list[int].
            # str-arg case: needs a list-header slot, a str-save slot, and an
            # index slot.  int-arg case: list-header + count slot.
            if expr.func in ("bytes", "bytearray"):
                if not expr.args:
                    pass  # empty list, no scratch needed
                else:
                    at = A.expr_type(expr.args[0]) if expr.args else None
                    if at == "str":
                        self._cl_define(info, f"__bytes_list_{id(expr)}")
                        self._cl_define(info, f"__bytes_str_{id(expr)}")
                        self._cl_define(info, f"__bytes_idx_{id(expr)}")
                    elif at != "list":
                        # int n
                        self._cl_define(info, f"__bytes_list_{id(expr)}")
                        self._cl_define(info, f"__bytes_cnt_{id(expr)}")
            # dict() / dict(other): an empty dict (same layout as a set's),
            # plus a slot to park the source across the allocation when copying.
            if expr.func == "dict":
                self._cl_define(info, f"__dictcall_res_{id(expr)}")
                if expr.args:
                    self._cl_define(info, f"__dictcall_src_{id(expr)}")
                if getattr(expr, "dict_from_pairs", False):
                    self._cl_define(info, f"__dictpairs_it_{id(expr)}")
                    self._cl_define(info, f"__dictpairs_stop_{id(expr)}")
                    self._cl_define(info, f"__dictpairs_idx_{id(expr)}")
                    self._cl_define(info, f"__dictpairs_key_{id(expr)}")
            # list(filter(...)): truthy-filter loop needs 5 scratch slots.
            # list(map(lambda, xs)): map loop needs the same 5 scratch slots.
            if expr.func in ("list", "tuple") and expr.args:
                a0 = expr.args[0]
                if isinstance(a0, A.Call) and a0.func in ("filter", "map") and len(a0.args) == 2:
                    self._cl_define(info, f"__listcall_res_{id(expr)}")
                    self._cl_define(info, f"__listcall_it_{id(expr)}")
                    self._cl_define(info, f"__listcall_stop_{id(expr)}")
                    self._cl_define(info, f"__listcall_idx_{id(expr)}")
                    self._cl_define(info, f"__listcall_val_{id(expr)}")
                    if isinstance(a0, A.Call) and a0.func in ("filter", "map") and isinstance(a0.args[0], A.Lambda):
                        lam = a0.args[0]
                        for p in lam.params:
                            self._cl_define(info, p)
                # list(zip(A, B, ...)): result list + per-iterable pointer + stop + idx.
                if isinstance(a0, A.Call) and a0.func == "zip":
                    n = len(a0.args)
                    self._cl_define(info, f"__lzip_res_{id(expr)}")
                    self._cl_define(info, f"__lzip_stop_{id(expr)}")
                    self._cl_define(info, f"__lzip_idx_{id(expr)}")
                    self._cl_define(info, f"__lzip_tup_{id(expr)}")
                    for k in range(n):
                        self._cl_define(info, f"__lzip_it{k}_{id(expr)}")
            # set()/frozenset(): an empty set needs a header slot; building
            # from a list/tuple additionally needs iteration-cursor slots.
            if expr.func in ("set", "frozenset"):
                at = A.expr_type(expr.args[0]) if expr.args else None
                if not expr.args:
                    self._cl_define(info, f"__setcall_res_{id(expr)}")
                elif at in ("list", "tuple"):
                    self._cl_define(info, f"__setcall_res_{id(expr)}")
                    self._cl_define(info, f"__setcall_it_{id(expr)}")
                    self._cl_define(info, f"__setcall_stop_{id(expr)}")
                    self._cl_define(info, f"__setcall_idx_{id(expr)}")
                    self._cl_define(info, f"__setcall_key_{id(expr)}")
            # sum(xs, start): park the start value across the list eval.
            if expr.func == "sum" and len(expr.args) == 2:
                self._cl_define(info, f"__sum_start_{id(expr)}")
            # User function / constructor calls pass arguments through
            # frame slots (alignment-safe). One slot per positional arg.
            # Indirect calls (`f(...)` where f is a local function pointer, e.g.
            # a bound lambda) use the same slot mechanism.
            if (
                expr.func in self.funcs
                or expr.func in self.mod.classes_sig
                or expr.func in info.locals_
                or expr.func in info.params
                or expr.func in self.global_vars
            ):
                for k in range(len(expr.args)):
                    self._cl_define(info, f"__callarg_{id(expr)}_{k}")
            # Direct-by-name call to a lifted closure body (self-recursion, or
            # a call from within the function that originally enclosed the
            # nested `def`): reserve scratch slots for the free vars sema
            # prepends as its leading params, so _gen_call can evaluate them
            # through the same register/stack-spill machinery as ordinary
            # args instead of assuming they all fit in registers.
            for ff in self.mod.funcs:
                if ff.name == expr.func and getattr(ff, "is_lifted", False):
                    # Direct field access, not getattr(ff, "free_vars", [])
                    # or []: a getattr() result is always opaque ("any"-typed)
                    # to sema, which made len() below compile as strlen()
                    # (codegen's len() has no other fallback for "any") --
                    # same bug class as the target_types fix above, just one
                    # more occurrence of the pattern. ff is a real FuncDef
                    # here (from self.mod.funcs) and free_vars: list is
                    # always present on it.
                    for k in range(len(ff.free_vars)):
                        self._cl_define(info, f"__fvarg_{id(expr)}_{k}")
                    break
            # Closure call: allocate per-arg scratch slots to park evaluated args
            # while we load the closure buf pointer (which may clobber registers).
            if (
                isinstance(expr.func, str)
                and self._var_type(expr.func, info) == "closure"
            ):
                for k in range(len(expr.args)):
                    self._cl_define(info, f"__closure_arg_{id(expr)}_{k}")
            for a in expr.args:
                self._cl_walk_expr(info, a)
            # Keyword args (kept un-normalized for @dataclass-style ctors) and
            # the class-var default expressions the synthesized init may emit
            # both contain expressions needing slot reservations.
            kwlist = getattr(expr, "kwargs", [])

            kwlist = kwlist or []

            for _kn, kv in kwlist:
                self._cl_walk_expr(info, kv)
            if expr.func in self.mod.classes_sig:
                # Mirrors _gen_constructor's class-var seeding, which walks
                # the full inheritance chain (not just expr.func's own
                # class_vars) so an inherited-but-not-overridden class var
                # is seeded into the instance too. Every class-var default
                # expression _gen_constructor will emit needs its slot
                # reserved here first.
                for cname in self._resolve_class_chain(expr.func):
                    for c in self.mod.classes:
                        if c.name == cname:
                            for cv in getattr(c, "class_vars", []) or []:
                                _fn, _fa, fdefault = cv
                                if fdefault is not None and not (
                                    isinstance(fdefault, A.Call)
                                    and fdefault.func == "field"
                                ):
                                    self._cl_walk_expr(info, fdefault)
        elif isinstance(expr, A.MethodCall):
            # os.environ.get(key[, default]) — lowers to getenv(key); reserve
            # its one FFI scratch slot (see the matching codegen in
            # _gen_method_call).
            if (
                isinstance(expr.obj, A.Attr)
                and isinstance(expr.obj.obj, A.Name)
                and expr.obj.obj.name == "os"
                and expr.obj.name == "environ"
                and expr.method == "get"
            ):
                fn = stdlib.os.BINDINGS["getenv"]
                self._cl_define(info, f"__ffi_arg_{id(fn)}_0", "int")
            # os.getcwd() / os.listdir(path) — scratch slots for inline helpers.
            if isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_modules:
                if expr.obj.name == "os" and expr.method == "listdir":
                    path_arg = expr.args[0] if expr.args else None
                    self._cl_define(info, f"__listdir_pipe_{id(path_arg)}")
                    self._cl_define(info, f"__listdir_acc_{id(path_arg)}")
                    self._cl_define(info, f"__listdir_line_{id(path_arg)}")
                    self._cl_define(info, f"__listdir_char_{id(path_arg)}")
            # math.sqrt(x) — same FFI scratch reservation.
            if isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_modules:
                bindings = self.imported_modules[expr.obj.name]
                b = bindings.get(expr.method)
                if b is not None and hasattr(b, "arg_types"):
                    for k in range(len(expr.args)):
                        self._cl_define(
                            info,
                            f"__ffi_arg_{id(b)}_{k}",
                            "float" if b.arg_types[k] == "float" else "int",
                        )
            # handle.func(args) where handle = import_binary(path) and func
            # is @handle.imported: scratch slots for each marshaled arg plus
            # the resolved function pointer (see _gen_dynamic_call).
            if isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_funcs:
                _funcdef = None
                for _fname, _fdef in self.imported_funcs[expr.obj.name]:
                    if _fname == expr.method:
                        _funcdef = _fdef
                        break
                if _funcdef is not None:
                    for k in range(len(expr.args)):
                        annot = (
                            _funcdef.param_types[k]
                            if k < len(_funcdef.param_types)
                            else None
                        )
                        base = annot[0] if annot else "int"
                        self._cl_define(
                            info,
                            f"__dyncall_arg_{id(expr)}_{k}",
                            "float" if base == "float" else "int",
                        )
                    self._cl_define(info, f"__dyncall_ptr_{id(expr)}")
                    # Holds the handle dict pointer across a GL call's
                    # lazy-resolve _runtime_dict_set (see _gen_dynamic_call's
                    # is_gl branch). Reserved unconditionally here (cheap,
                    # one frame slot) rather than threading "is this
                    # actually a gl_import() handle" through this scan too.
                    self._cl_define(info, f"__dyncall_gldict_{id(expr)}")
            # `instance.glClearColor(args)` where glClearColor is a
            # `@glfns.imported` *method* (see imported_method_handle /
            # _gen_method_call) -- same scratch-slot shape as the direct
            # `glfns.glClearColor(...)` case just above, just matched via
            # the receiver's static class instead of expr.obj being the
            # handle itself, and offset past `self` in param_types.
            _recv_ty = A.expr_type(expr.obj)
            if _recv_ty.startswith("instance:"):
                _cls_name = _recv_ty[len("instance:"):]
                _handle = self.imported_method_handle.get((_cls_name, expr.method))
                if _handle is not None:
                    _funcdef = None
                    for _fname, _fdef in self.imported_funcs.get(_handle, []):
                        if _fname == expr.method:
                            _funcdef = _fdef
                            break
                    if _funcdef is not None:
                        for k in range(len(expr.args)):
                            pidx = k + 1  # skip `self`
                            annot = (
                                _funcdef.param_types[pidx]
                                if pidx < len(_funcdef.param_types)
                                else None
                            )
                            base = annot[0] if annot else "int"
                            self._cl_define(
                                info,
                                f"__dyncall_arg_{id(expr)}_{k}",
                                "float" if base == "float" else "int",
                            )
                        self._cl_define(info, f"__dyncall_ptr_{id(expr)}")
                        self._cl_define(info, f"__dyncall_gldict_{id(expr)}")
            if not (isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_modules):
                # String methods spill the object pointer (and one extra
                # for replace's 2-arg signature) across argument eval. An
                # opaque receiver calling a known str method is dispatched to
                # the str runtime too, so it needs the same slots.
                if A.expr_type(expr.obj) == "str" or (
                    A.expr_type(expr.obj) == "any"
                    and expr.method in self.STR_METHOD_RUNTIME
                ):
                    self._cl_define(info, f"__strm_obj_{id(expr)}")
                    if len(expr.args) >= 2:
                        self._cl_define(info, f"__strm_a1_{id(expr)}")
                # str.format() on a literal builds a concat accumulator.
                if expr.method == "format" and isinstance(expr.obj, A.StrLit):
                    self._cl_define(info, f"__fmt_acc_{id(expr)}")
                # User-class (and super) method calls pass args through
                # frame slots; instance calls also park the receiver.
                obj_t = A.expr_type(expr.obj)
                if obj_t == "set" and expr.method == "add":
                    # set.add(x): park the key across the receiver's eval.
                    self._cl_define(info, f"__setadd_key_{id(expr)}")
                if obj_t == "set" and expr.method in ("discard", "remove"):
                    # set.discard(x)/remove(x): park the key across the
                    # receiver's (repeated) eval.
                    self._cl_define(info, f"__setrm_key_{id(expr)}")
                if expr.method == "update" and obj_t in ("dict", "set", "any"):
                    # dict/set.update(src): park src across the receiver's eval.
                    self._cl_define(info, f"__dictupd_{id(expr)}")
                    if obj_t == "set" and expr.args and A.expr_type(expr.args[0]) in ("list", "tuple"):
                        # set.update(some_list): per-element add loop needs its
                        # own index and set-header scratch slots too.
                        self._cl_define(info, f"__dictupd_idx_{id(expr)}")
                        self._cl_define(info, f"__dictupd_set_{id(expr)}")
                if expr.method == "index" and obj_t in ("list", "tuple", "any"):
                    # list.index(v): park the needle across the receiver eval.
                    self._cl_define(info, f"__listidx_{id(expr)}")
                if obj_t == "list" and expr.method in ("count", "remove"):
                    self._cl_define(info, f"__lm_val_{id(expr)}")
                    self._cl_define(info, f"__lm_hdr_{id(expr)}")
                if obj_t in ("list", "any", "int") and expr.method == "insert":
                    self._cl_define(info, f"__lm_idx_{id(expr)}")
                    self._cl_define(info, f"__lm_val_{id(expr)}")
                if expr.method == "sort" and getattr(expr, "sort_key", None) is not None:
                    self._cl_define(info, f"__sortkey_elems_{id(expr)}")
                    self._cl_define(info, f"__sortkey_fn_{id(expr)}")
                    self._cl_define(info, f"__sortkey_keys_{id(expr)}")
                    self._cl_define(info, f"__sortkey_n_{id(expr)}")
                    self._cl_define(info, f"__sortkey_i_{id(expr)}")
                if expr.method == "sort" and getattr(expr, "sort_reverse", None) is not None:
                    self._cl_define(info, f"__sortrev_hdr_{id(expr)}")
                if expr.method == "sort":
                    if getattr(expr, "sort_key", None) is not None:
                        self._cl_walk_expr(info, expr.sort_key)
                    if getattr(expr, "sort_reverse", None) is not None:
                        self._cl_walk_expr(info, expr.sort_reverse)
                if obj_t in ("dict", "set", "any") and expr.method in ("pop", "setdefault"):
                    self._cl_define(info, f"__dm_arg_{id(expr)}")
                    # expr (A.MethodCall) always has a real `args: list`
                    # field -- direct access, not getattr(expr, "args", []),
                    # which would make len() compile as strlen() on an
                    # "any"-typed opaque value (same bug class fixed above
                    # for target_types/free_vars).
                    if len(expr.args) >= 2:
                        self._cl_define(info, f"__dm_arg2_{id(expr)}")
                if expr.method == "copy" and obj_t not in ("list", "tuple"):
                    self._cl_define(info, f"__dm_src_{id(expr)}")
                    self._cl_define(info, f"__dm_new_{id(expr)}")
                if obj_t == "set" and expr.method in ("union", "intersection", "difference"):
                    self._cl_define(info, f"__sm_other_{id(expr)}")
                    self._cl_define(info, f"__sm_new_{id(expr)}")
                    self._cl_define(info, f"__sm_keys_{id(expr)}")
                    self._cl_define(info, f"__sm_idx_{id(expr)}")
                    self._cl_define(info, f"__sm_key_{id(expr)}")
                if obj_t.startswith("instance:"):
                    self._cl_define(info, f"__callself_{id(expr)}")
                # Module-qualified call to a merged project function
                # (`A.expr_type(x)`) lowers to a plain call, so it needs the
                # same per-arg slots as an instance/super call.
                is_module_fn = obj_t == "module" and expr.method in self.funcs
                # `ClassName.staticmethod(args)` / `.classmethod(args)`: a plain
                # call to the method symbol, so it needs per-arg slots too.
                is_class_static = (
                    isinstance(expr.obj, A.Name)
                    and expr.obj.name in self.class_ids
                )
                if (
                    obj_t.startswith("instance:")
                    or obj_t.startswith("super:")
                    or is_module_fn
                    or is_class_static
                ):
                    for k in range(len(expr.args)):
                        self._cl_define(info, f"__callarg_{id(expr)}_{k}")
                self._cl_walk_expr(info, expr.obj)
            for a in expr.args:
                self._cl_walk_expr(info, a)
            for _kn, kv in getattr(expr, "kwargs", []) or []:
                self._cl_walk_expr(info, kv)
        elif isinstance(expr, A.Subscript):
            if isinstance(expr.index, A.Slice):
                # slice needs scratch slots for obj/start (always) plus
                # stop/step when the step-aware path is used.
                self._cl_define(info, f"__strsl_obj_{id(expr)}")
                self._cl_define(info, f"__strsl_start_{id(expr)}")
                if expr.index.step is not None:
                    self._cl_define(info, f"__strsl_stop_{id(expr)}")
                    self._cl_define(info, f"__strsl_step_{id(expr)}")
                # List slicing dispatches through a different helper; the
                # codegen path uses its own pair of slots.
                if A.expr_type(expr.obj) == "list":
                    self._cl_define(info, f"__lstsl_obj_{id(expr)}")
                    self._cl_define(info, f"__lstsl_start_{id(expr)}")
                    if expr.index.step is not None:
                        self._cl_define(info, f"__lstsl_step_{id(expr)}")
                self._cl_walk_expr(info, expr.obj)
                if expr.index.start is not None:
                    self._cl_walk_expr(info, expr.index.start)
                if expr.index.stop is not None:
                    self._cl_walk_expr(info, expr.index.stop)
                if expr.index.step is not None:
                    self._cl_walk_expr(info, expr.index.step)
            elif A.expr_type(expr.obj) == "str":
                self._cl_define(info, f"__stridx_{id(expr)}")
                self._cl_walk_expr(info, expr.obj)
                self._cl_walk_expr(info, expr.index)
            elif getattr(expr, "_getitem_class", None) is not None:
                # Instance __getitem__: synthesized method call needs receiver
                # slot and one arg slot (the index). We park them under
                # __gi_self_<id> / __gi_arg_<id> so they don't collide with
                # normal subscript or callarg slots.
                self._cl_define(info, f"__gi_self_{id(expr)}")
                self._cl_define(info, f"__gi_arg_{id(expr)}")
                self._cl_walk_expr(info, expr.obj)
                self._cl_walk_expr(info, expr.index)
            else:
                # Dict/list subscripts park the evaluated index across the
                # object's evaluation. A frame slot, not push/pop: if the
                # object expression calls anything (a dict literal's mallocs,
                # an attr read), the callee's Win64 shadow-space stores at
                # [rsp..rsp+31] clobber a pushed value.
                self._cl_define(info, f"__subidx_{id(expr)}")
                self._cl_walk_expr(info, expr.obj)
                self._cl_walk_expr(info, expr.index)
        elif isinstance(expr, A.Attr):
            # Module attr access doesn't need to evaluate the obj.
            if isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_modules:
                pass
            else:
                self._cl_walk_expr(info, expr.obj)
        elif isinstance(expr, A.FString):
            self._cl_define(info, f"__fstr_acc_{id(expr)}")
            for s in expr.segments:
                self._cl_walk_expr(info, s)
        elif isinstance(expr, A.NamedExpr):
            self._cl_define(info, expr.target, A.expr_type(expr.value))
            self._cl_walk_expr(info, expr.value)

    def _cl_walk(self, info: FuncInfo, stmts: list) -> None:
        for s in stmts:
            if isinstance(s, A.Global):
                info.global_names.update(s.names)
                continue
            if isinstance(s, A.MultiAssign):
                ty_ma = A.expr_type(s.value)
                self._cl_walk_expr(info, s.value)
                for nm in s.targets:
                    self._cl_define(info, nm, ty_ma)
            elif isinstance(s, A.Assign):
                ty_wa = A.expr_type(s.value)
                if self._is_closure_factory_call(s.value):
                    ty_wa = "closure"

                self._cl_define(info, s.target, ty_wa)
                self._cl_walk_expr(info, s.value)
                if (
                    isinstance(s.target, str)
                    and isinstance(s.value, A.Call)
                    and s.value.func == "import_binary"
                ):
                    # Scratch slot to hold the loaded handle across the
                    # sequence of GetProcAddress/dlsym calls that resolve
                    # each @<target>.imported function (each clobbers rax).
                    self._cl_define(info, f"__importbin_handle_{id(s)}", "int")
                if (
                    isinstance(s.target, str)
                    and isinstance(s.value, A.Call)
                    and s.value.func == "gl_import"
                ):
                    # Scratch slot to hold the function-pointer-table dict
                    # across _emit_dict_alloc_order_buf's setup (mirrors
                    # import_binary's handle slot, but there's no library
                    # handle here -- just the dict itself needs to survive
                    # across that helper call).
                    self._cl_define(info, f"__glimport_dictslot_{id(s)}", "int")
            elif isinstance(s, A.AugAssign):
                self._cl_define(info, s.target, A.expr_type(s.value))
                self._cl_walk_expr(info, s.value)
            elif isinstance(s, A.TupleAssign) and any(
                isinstance(t, A.StarTarget) for t in s.targets
            ):
                # `a, *rest = xs` (xs: list[T]). `rest` is a fresh list[T];
                # plain targets get T directly. Sema guarantees a single
                # list-typed RHS and at most one StarTarget.
                self._cl_define(info, f"__tupunpack_{id(s)}", "int")
                self._cl_walk_expr(info, s.values[0])
                el_t = self._list_expr_el_kind(s.values[0])
                for t in s.targets:
                    if isinstance(t, A.StarTarget):
                        self._cl_define(info, t.name, "list")
                    else:
                        self._cl_define(info, t.name, el_t)
            elif isinstance(s, A.TupleAssign):
                if len(s.values) == 1 and (
                    A.expr_type(s.values[0]) in ("tuple", "any")
                    or len(s.targets) > 1
                ):
                    # Unpack form: park the tuple ptr in one slot, then
                    # type each target from the tuple's element kinds (an
                    # opaque "any" tuple has unknown slots -> int/lenient).
                    # Sema guarantees plain-name targets in this form.
                    self._cl_define(info, f"__tupunpack_{id(s)}", "int")
                    self._cl_walk_expr(info, s.values[0])
                    ets = A.tuple_element_types(s.values[0])
                    for i, t in enumerate(s.targets):
                        self._cl_define(info, t.name, ets[i] if i < len(ets) else "int")
                else:
                    for i, v in enumerate(s.values):
                        self._cl_walk_expr(info, v)
                        self._cl_define(info, f"__tup_tmp_{id(s)}_{i}", A.expr_type(v))
                    for t, v in zip(s.targets, s.values):
                        if isinstance(t, A.Name):
                            self._cl_define(info, t.name, A.expr_type(v))
                        elif isinstance(t, A.Subscript):
                            self._cl_walk_expr(info, t.obj)
                            self._cl_walk_expr(info, t.index)
                        elif isinstance(t, A.Attr):
                            self._cl_walk_expr(info, t.obj)
            elif isinstance(s, A.For) and self._for_zip_spec(s) is not None:
                # for a, b[, c...] in zip(A, B[, C...]) / enumerate(zip(...)): N-way lockstep.
                zidx, znames, zexprs = self._for_zip_spec(s)  # type: ignore
                if zidx is not None:
                    self._cl_define(info, zidx, "int")
                for i, (zn, ze) in enumerate(zip(znames, zexprs)):
                    self._cl_define(info, zn, self._list_expr_el_kind(ze))
                    self._cl_define(info, f"__zip_{i}_{id(s)}", "int")
                self._cl_define(info, f"__zip_stop_{id(s)}", "int")
                self._cl_define(info, f"__zip_i_{id(s)}", "int")
                for ze in zexprs:
                    self._cl_walk_expr(info, ze)
                self._cl_walk(info, s.body)
                self._cl_walk(info, s.orelse)
            elif (
                isinstance(s, A.For)
                and s.iter is not None
                and isinstance(s.iter, A.Call)
                and s.iter.func == "enumerate"
            ):
                # for i, x in enumerate(inner): index var, element var, plus
                # the list-iteration machinery.
                inner = s.iter.args[0]
                el_ty = "int"
                if A.expr_type(inner) == "list":
                    if isinstance(inner, A.Name):
                        el_ty = inner.list_el_type
                    elif isinstance(inner, A.ListLit):
                        el_ty = inner.el_type
                    else:
                        el_ty = getattr(inner, "list_el_type", "int")
                self._cl_define(info, s.targets[0], "int")
                self._cl_define(info, s.targets[1], el_ty)
                self._cl_define(info, f"__for_stop_{id(s)}", "int")
                self._cl_define(info, f"__for_step_{id(s)}", "int")
                self._cl_define(info, f"__for_iter_{id(s)}", "int")
                _enum_start_expr = None
                if len(s.iter.args) == 2:
                    _enum_start_expr = s.iter.args[1]
                else:
                    for _kn, _kv in s.iter.kwargs:
                        if _kn == "start":
                            _enum_start_expr = _kv
                            break
                if _enum_start_expr is not None:
                    self._cl_define(info, f"__for_enum_start_{id(s)}", "int")
                    self._cl_walk_expr(info, _enum_start_expr)
                self._cl_walk_expr(info, inner)
                self._cl_walk(info, s.body)
                self._cl_walk(info, s.orelse)
            elif isinstance(s, A.For):
                # Loop var inherits the iterable's element type so the
                # later Name load picks the right register class.
                var_ty = "int"
                if s.iter is not None and A.expr_type(s.iter) == "list":
                    if isinstance(s.iter, A.Name):
                        var_ty = s.iter.list_el_type
                    elif isinstance(s.iter, A.ListLit):
                        var_ty = s.iter.el_type
                elif s.iter is not None and A.expr_type(s.iter) == "tuple":
                    # Iterable tuples are homogeneous (sema enforces it),
                    # so the loop var takes the shared element kind.
                    ets = A.tuple_element_types(s.iter)
                    var_ty = ets[0] if ets else "int"
                elif s.iter is not None and A.expr_type(s.iter) == "dict":
                    var_ty = "str"
                elif s.iter is not None and A.expr_type(s.iter) == "set":
                    # Set members are str-typed (v1: str-keyed sets).
                    var_ty = "str"
                elif s.iter is not None and A.expr_type(s.iter) == "str":
                    var_ty = "str"
                if s.targets:
                    # `for a, b in pairs`: tuple-unpacking targets. sema stamps
                    # per-target kinds (list[tuple] slots) onto target_types;
                    # fall back to opaque "any" when shapes are untracked.
                    #
                    # `s` is already known to be an A.For here (this whole
                    # branch is `elif isinstance(s, A.For)`), and every A.For
                    # has a real `target_types: list` field -- access it
                    # directly rather than through getattr(s, "target_types",
                    # []). A getattr() call's result is always opaque
                    # ("any"-typed) to sema regardless of the real attribute's
                    # type, which made `len(ttypes)` below compile as
                    # strlen() (codegen's len() has no other fallback for an
                    # "any"-typed argument) instead of a real list-length
                    # read. strlen() on a list header's raw bytes returns a
                    # essentially-random length, so `i < len(ttypes)` passed
                    # or failed unpredictably and the following `ttypes[i]`
                    # (correctly compiled as a real list read, since
                    # `ttypes`'s own static type from direct field access is
                    # "list") indexed out of the real list's actual bounds.
                    # Confirmed via gdb on a selfhost rebuild: crashed
                    # compiling tests/cases/03_fib.py with a "list index out
                    # of range" compile-time error (not a segfault, since
                    # the OOB check itself is correct -- only the LENGTH fed
                    # into it was wrong).
                    ttypes = s.target_types
                    names = self._target_names(s.targets)
                    i = 0
                    for t in names:
                        ty = ttypes[i] if i < len(ttypes) else "any"
                        self._cl_define(info, t, ty)
                        i += 1
                elif s.var:
                    self._cl_define(info, s.var, var_ty)
                self._cl_define(info, f"__for_stop_{id(s)}", "int")
                self._cl_define(info, f"__for_step_{id(s)}", "int")
                if s.iter is not None:
                    self._cl_define(
                        info, f"__for_iter_{id(s)}", "int"
                    )  # ptr; treat as int slot
                    self._cl_walk_expr(info, s.iter)
                    # Instance-iterable: allocate setjmp buf + saved exception
                    # slots for the internal StopIteration catch in _gen_for_iter.
                    if getattr(s, "iter_is_instance", None) is not None:
                        self._cl_define_bytes(info, f"__for_iter_buf_{id(s)}", 200)
                        self._cl_define(info, f"__for_iter_parent_{id(s)}", "int")
                        self._cl_define(info, f"__for_iter_prev_exc_{id(s)}", "int")
                        self._cl_define(info, f"__for_iter_prev_exc_type_{id(s)}", "int")
                else:
                    for a in s.range_args:
                        self._cl_walk_expr(info, a)
                self._cl_walk(info, s.body)
                self._cl_walk(info, s.orelse)
            elif isinstance(s, A.If):
                self._cl_walk_expr(info, s.test)
                self._cl_walk(info, s.then)
                self._cl_walk(info, s.orelse)
            elif isinstance(s, A.While):
                self._cl_walk_expr(info, s.test)
                self._cl_walk(info, s.body)
            elif isinstance(s, A.Return):
                if s.value is not None:
                    self._cl_walk_expr(info, s.value)
            elif isinstance(s, A.ExprStmt):
                self._cl_walk_expr(info, s.expr)
            elif isinstance(s, A.IndexAssign):
                self._cl_walk_expr(info, s.target.obj)
                self._cl_walk_expr(info, s.target.index)
                self._cl_walk_expr(info, s.value)
                if A.expr_type(s.target.obj) == "dict":
                    self._cl_define(info, f"__dictset_key_{id(s)}")
                elif isinstance(s.target.index, A.Slice):
                    # Slice assign: park dst and src pointers across evals.
                    self._cl_define(info, f"__slcasgn_dst_{id(s)}")
                    self._cl_define(info, f"__slcasgn_src_{id(s)}")
            elif isinstance(s, A.AttrAssign):
                self._cl_walk_expr(info, s.obj)
                self._cl_walk_expr(info, s.value)
            elif isinstance(s, A.Try):
                # jmp_buf on x86-64 libc is typically <= 200 bytes.
                self._cl_define_bytes(info, f"__try_buf_{id(s)}", 200)
                # Saved previous handler ptr to restore on normal exit.
                self._cl_define(info, f"__try_parent_{id(s)}", "int")
                # Saved _runtime_exc_msg / _runtime_exc_type from before this
                # try, restored once a handler here finishes so a later bare
                # `raise` outside any handler correctly reports "no active
                # exception" (and re-raises propagate the right type).
                self._cl_define(info, f"__try_prev_exc_{id(s)}", "int")
                self._cl_define(info, f"__try_prev_exc_type_{id(s)}", "int")
                if s.bind_name is not None:
                    self._cl_define(info, s.bind_name, "str")
                self._cl_walk(info, s.body)
                self._cl_walk(info, s.handler)
                for _types, bind_name, hbody in s.extra_handlers:
                    if bind_name is not None:
                        self._cl_define(info, bind_name, "str")
                    self._cl_walk(info, hbody)
                self._cl_walk(info, s.else_body)
                self._cl_walk(info, s.finally_body)
            elif isinstance(s, A.Raise):
                if s.value is not None:
                    self._cl_walk_expr(info, s.value)
            elif isinstance(s, A.With):
                self._cl_walk_expr(info, s.expr)
                if s.name is not None:
                    self._cl_define(info, s.name, A.expr_type(s.expr))
                self._cl_walk(info, s.body)
            elif isinstance(s, A.Del):
                tgt = s.target
                if isinstance(tgt, A.Subscript):
                    self._cl_define(info, f"__del_key_{id(s)}")
                    self._cl_walk_expr(info, tgt.obj)
                    self._cl_walk_expr(info, tgt.index)
            elif isinstance(s, A.ClosureBind):
                # The closure object is stored under the function name.
                self._cl_define(info, s.func_name, "closure")

    # ---- statement codegen --------------------------------------------------

    def gen_stmt(self, stmt, info: FuncInfo) -> None:
        if isinstance(stmt, A.Pass):
            return
        if isinstance(stmt, A.ClosureBind):
            # Allocate a closure list: [CLOSURE_MAGIC, fn_ptr, fv1, fv2, ...]
            # where CLOSURE_MAGIC=0xC105E distinguishes closures from plain lists.
            CLOSURE_MAGIC = 0xC105E
            n_items = 2 + len(stmt.free_vars)
            cap = max(4, n_items)
            self._emit_malloc(self.LIST_HEADER)
            self.emitf(
                f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
                f"mov qword [rax+{self.LIST_LEN_OFF}], {n_items}",
            )
            mem = self._var_mem(stmt.func_name, info)
            self.emitf(f"mov {mem}, rax")
            self._emit_malloc(cap * 8)
            self.emitf(
                f"mov rbx, {mem}",
                f"mov [rbx+{self.LIST_BUF_OFF}], rax",
            )
            # [0] = CLOSURE_MAGIC
            self.emitf(
                f"mov rbx, {mem}",
                f"mov rcx, [rbx+{self.LIST_BUF_OFF}]",
                f"mov qword [rcx], {CLOSURE_MAGIC}",
            )
            # [1] = function pointer (the address of the lifted function label)
            fn_label = stmt.func_name
            self.emitf(
                f"lea rax, [{fn_label}]",
                f"mov [rcx+8], rax",
            )
            # [2..] = captured variable values (or box ptrs for nonlocal vars)
            #
            # A plain list, not set(getattr(stmt, "nonlocal_vars", [])):
            # same bug class as _collect_locals' nonlocal_set fix -- a
            # getattr() result is opaque ("any"-typed) to sema, and
            # _gen_set_call's "any"-argument branch hands an opaque value
            # straight back as if it were already dict-shaped instead of
            # iterating it as the list it actually is here. stmt
            # (A.ClosureBind) always has a real `nonlocal_vars: list`
            # field, so access it directly.
            nl_list = list(stmt.nonlocal_vars)
            for i, fv in enumerate(stmt.free_vars):
                fv_mem = self._var_mem(fv, info)
                if fv in nl_list:
                    # Nonlocal: allocate an 8-byte box, write current value,
                    # store the box ptr in the closure slot.
                    box_slot = f"__nl_box_{fv}_{id(stmt)}"
                    self._cl_define(info, box_slot)
                    box_mem = self._var_mem(box_slot, info)
                    # malloc(8) for the box
                    self._emit_malloc(8)
                    self.emitf(f"mov {box_mem}, rax")
                    # write current value into box
                    self.emitf(
                        f"mov rbx, {fv_mem}",
                        f"mov rax, {box_mem}",
                        "mov [rax], rbx",
                    )
                    # store box ptr in closure slot
                    self.emitf(
                        f"mov rax, {box_mem}",
                        f"mov rbx, {mem}",
                        f"mov rdx, [rbx+{self.LIST_BUF_OFF}]",
                        f"mov [rdx+{(i + 2) * 8}], rax",
                    )
                else:
                    self.emitf(
                        f"mov rax, {fv_mem}",
                        f"mov rbx, {mem}",
                        f"mov rdx, [rbx+{self.LIST_BUF_OFF}]",
                        f"mov [rdx+{(i + 2) * 8}], rax",
                    )
            return
        if isinstance(stmt, A.Global):
            return  # handled at _cl_walk time
        if isinstance(stmt, (A.Import, A.FromImport)):
            # Imports are resolved statically by sema; nothing runtime-side.
            return
        if isinstance(stmt, A.MultiAssign):
            self.gen_expr(stmt.value, info)
            for nm in stmt.targets:
                mem = self._var_mem(nm, info)
                self.emitf(f"mov {mem}, rax")
            return
        if isinstance(stmt, A.Assign):
            ty = self._var_type(stmt.target, info)
            value_t = A.expr_type(stmt.value)
            mem = self._var_mem(stmt.target, info)
            if (
                isinstance(stmt.target, str)
                and isinstance(stmt.value, A.Call)
                and stmt.value.func == "import_binary"
            ):
                self._gen_import_binary(stmt, info, mem)
                return
            if (
                isinstance(stmt.target, str)
                and isinstance(stmt.value, A.Call)
                and stmt.value.func == "gl_import"
            ):
                self._gen_gl_import(stmt, info, mem)
                return
            if isinstance(stmt.target, str) and stmt.target in info.nonlocal_boxes:
                # Nonlocal: param slot holds box ptr; store value through it.
                self.gen_expr(stmt.value, info)
                self.emitf(f"mov rbx, {mem}", "mov [rbx], rax")
            elif ty == "float":
                # Slot expects a float; promote int RHS to float.
                self._gen_expr_as_float(stmt.value, info, value_t)
                self.emitf(f"movsd {mem}, xmm0")
            else:
                self.gen_expr(stmt.value, info)
                self.emitf(f"mov {mem}, rax")
            return
        if isinstance(stmt, A.TupleAssign) and any(
            isinstance(t, A.StarTarget) for t in stmt.targets
        ):
            # `a, *rest = xs` / `*init, last = xs` / `a, *mid, b = xs`
            # (xs: list[T]). Evaluate xs once; plain targets before the star
            # read xs[0..n_before-1], plain targets after the star read from
            # the end (xs[len-n_after..len-1]), and the star target becomes
            # _runtime_list_slice(xs, n_before, len - n_after).
            star_i = -1
            for _si, _st in enumerate(stmt.targets):
                if isinstance(_st, A.StarTarget):
                    star_i = _si
                    break
            n_before = star_i
            n_after = len(stmt.targets) - star_i - 1
            ptr_slot = info.locals_[f"__tupunpack_{id(stmt)}"]
            el_t = self._list_expr_el_kind(stmt.values[0])
            self.gen_expr(stmt.values[0], info)  # rax = list header ptr
            self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
            for i in range(n_before):
                off = info.locals_[stmt.targets[i].name]
                self.emitf(
                    f"mov rbx, [rbp{ptr_slot:+d}]",
                    f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                )
                if el_t == "float":
                    self.emitf(
                        f"movsd xmm0, [rbx+{i * 8}]", f"movsd [rbp{off:+d}], xmm0"
                    )
                else:
                    self.emitf(f"mov rax, [rbx+{i * 8}]", f"mov [rbp{off:+d}], rax")
            for j in range(n_after):
                off = info.locals_[stmt.targets[star_i + 1 + j].name]
                self.emitf(
                    f"mov rcx, [rbp{ptr_slot:+d}]",
                    f"mov rax, [rcx+{self.LIST_LEN_OFF}]",
                    f"sub rax, {n_after - j}",
                    f"mov rbx, [rcx+{self.LIST_BUF_OFF}]",
                )
                if el_t == "float":
                    self.emitf(
                        "movsd xmm0, [rbx+rax*8]", f"movsd [rbp{off:+d}], xmm0"
                    )
                else:
                    self.emitf("mov rax, [rbx+rax*8]", f"mov [rbp{off:+d}], rax")
            rest_off = info.locals_[stmt.targets[star_i].name]
            self.emitf(
                f"mov rax, [rbp{ptr_slot:+d}]",  # src header
                f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
                f"sub rcx, {n_after}",  # stop = len - n_after
                f"mov rbx, {n_before}",  # start = n_before
                "call _runtime_list_slice",
                f"mov [rbp{rest_off:+d}], rax",
            )
            return
        if isinstance(stmt, A.TupleAssign):
            # Unpack form: `a, b = <tuple>`. Evaluate the tuple once, park its
            # header ptr, then copy each slot out into the matching target.
            # An "any"-typed single RHS is an opaque tuple pointer (sema bound
            # the targets leniently) — unpack it the same way, reading each slot
            # as a plain 8-byte move.
            if len(stmt.values) == 1 and (
                A.expr_type(stmt.values[0]) in ("tuple", "any")
                or len(stmt.targets) > 1
            ):
                # Sema guarantees plain-name targets in this form.
                ptr_slot = info.locals_[f"__tupunpack_{id(stmt)}"]
                rhs_t = A.expr_type(stmt.values[0])
                # String unpack: `a, b, c = "abc"` — extract each char as a
                # one-char heap string using _runtime_str_getitem.
                if rhs_t == "str":
                    self.gen_expr(stmt.values[0], info)
                    self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
                    for i, target in enumerate(stmt.targets):
                        off = info.locals_[target.name]
                        self.emitf(
                            f"mov rax, [rbp{ptr_slot:+d}]",
                            f"mov rbx, {i}",
                            "call _runtime_str_char_at",
                            f"mov [rbp{off:+d}], rax",
                        )
                    return
                ets = A.tuple_element_types(stmt.values[0])
                self.gen_expr(stmt.values[0], info)  # rax = tuple header ptr
                self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
                for i, target in enumerate(stmt.targets):
                    off = info.locals_[target.name]
                    el_t = ets[i] if i < len(ets) else "int"
                    self.emitf(
                        f"mov rbx, [rbp{ptr_slot:+d}]",
                        f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                    )
                    if el_t == "float":
                        self.emitf(
                            f"movsd xmm0, [rbx+{i * 8}]", f"movsd [rbp{off:+d}], xmm0"
                        )
                    else:
                        self.emitf(f"mov rax, [rbx+{i * 8}]", f"mov [rbp{off:+d}], rax")
                return
            # Parallel form: evaluate every RHS into a pre-reserved scratch
            # slot, then commit each store. Two-pass model means swap works.
            for i, v in enumerate(stmt.values):
                tmp = info.locals_[f"__tup_tmp_{id(stmt)}_{i}"]
                self.gen_expr(v, info)
                self.emitf(f"mov [rbp{tmp:+d}], rax")
            for i, target in enumerate(stmt.targets):
                tmp = info.locals_[f"__tup_tmp_{id(stmt)}_{i}"]
                if isinstance(target, A.Name):
                    off = info.locals_[target.name]
                    self.emitf(f"mov rax, [rbp{tmp:+d}]", f"mov [rbp{off:+d}], rax")
                elif isinstance(target, A.Subscript):
                    # `xs[i] = <tmp>` / `d[k] = <tmp>`, mirroring IndexAssign
                    # codegen but reading the value out of the scratch slot
                    # instead of re-evaluating an RHS expression.
                    obj_t = A.expr_type(target.obj)
                    if obj_t == "dict":
                        self.gen_expr(target.index, info)
                        self.emitf("push rax")
                        self.emitf(f"mov rax, [rbp{tmp:+d}]", "push rax")
                        self.gen_expr(target.obj, info)  # rax = header
                        self.emitf(
                            "pop rcx",  # rcx = value
                            "pop rbx",  # rbx = key
                            "call _runtime_dict_set",
                        )
                    else:
                        self.gen_expr(target.index, info)
                        self.emitf("push rax")
                        self.emitf(f"mov rax, [rbp{tmp:+d}]", "push rax")
                        self.gen_expr(target.obj, info)  # rax = header
                        pos_lbl = self.fresh("tupidxw_pos")
                        self.emitf(
                            "pop rbx",  # rbx = value
                            "pop rcx",  # rcx = index
                            "test rcx, rcx",
                            f"jns {pos_lbl}",
                            f"add rcx, [rax+{self.LIST_LEN_OFF}]",
                        )
                        self.label(pos_lbl)
                        self.emitf(
                            f"mov rax, [rax+{self.LIST_BUF_OFF}]",
                            "mov [rax+rcx*8], rbx",
                        )
                else:
                    # A.Attr: `self.x = <tmp>` (or `ClassName.x = <tmp>` for a
                    # class variable), mirroring AttrAssign codegen.
                    if isinstance(target.obj, A.Name):
                        cv = self.class_var_labels.get(f"{target.obj.name}.{target.name}")
                        if cv is not None:
                            self.emitf(
                                f"mov rax, [rbp{tmp:+d}]", f"mov [rel {cv}], rax"
                            )
                            continue
                    key_label, _ = self.intern_string(target.name)
                    self.emitf(f"mov rax, [rbp{tmp:+d}]", "push rax")
                    self.gen_expr(target.obj, info)  # rax = instance dict
                    self.emitf(
                        "pop rcx",  # rcx = value
                        f"lea rbx, [{key_label}]",  # rbx = key
                        "call _runtime_dict_set",
                    )
            return
        if isinstance(stmt, A.AugAssign):
            if stmt.target in info.nonlocal_boxes:
                # Nonlocal: target is a box ptr; read → op → write through ptr.
                box_mem = self._var_mem(stmt.target, info)
                # Read current value from box.
                self.emitf(f"mov rax, {box_mem}", "mov rax, [rax]", "push rax")
                self.gen_expr(stmt.value, info)
                self.emitf("mov rbx, rax", "pop rax")
                self._emit_binop_inline(stmt.op)
                # Write result back through box ptr.
                self.emitf(f"mov rbx, {box_mem}", "mov [rbx], rax")
                return
            mem = self._var_mem(stmt.target, info)
            ty = self._var_type(stmt.target, info)
            if ty in ("dict", "set") and stmt.op == "|":
                # `d |= other` (PEP 584) / `s |= other` (set in-place union):
                # merge other's entries into d/s in place; the header
                # pointer itself doesn't change. Sets are dict-backed
                # (dummy value 1 per key, see _gen_set_call), so
                # _runtime_dict_update's "call _runtime_dict_set(dst, key,
                # value) per src entry" is exactly set union too — no
                # separate runtime helper needed. Previously this only
                # checked ty == "dict": a set target fell through to the
                # generic integer-arithmetic fallback further down, which
                # did `or rax, rbx` on the two raw 40-byte dict/set header
                # pointers and stored the resulting garbage address back
                # into the target's slot, corrupting it for any later use.
                self.gen_expr(stmt.value, info)
                self.emitf("mov rbx, rax", f"mov rax, {mem}", "call _runtime_dict_update")
                return
            if ty == "list" and stmt.op == "+":
                # `xs += other` → extend xs in-place.
                self.gen_expr(stmt.value, info)
                self.emitf("mov rbx, rax", f"mov rax, {mem}", "call _runtime_list_extend")
                return
            if ty == "str" and stmt.op == "+":
                # `s += other` → concat and rebind.
                self.gen_expr(stmt.value, info)
                self.emitf("mov rbx, rax", f"mov rax, {mem}", "call _runtime_str_concat")
                self.emitf(f"mov {mem}, rax")
                return
            if ty.startswith("instance:"):
                cls_name = ty.split(":", 1)[1]
                _INPLACE_DUNDERS: dict = {
                    "+": ("__iadd__", "__add__"),
                    "-": ("__isub__", "__sub__"),
                    "*": ("__imul__", "__mul__"),
                    "/": ("__itruediv__", "__truediv__"),
                    "//": ("__ifloordiv__", "__floordiv__"),
                    "%": ("__imod__", "__mod__"),
                    "**": ("__ipow__", "__pow__"),
                    "&": ("__iand__", "__and__"),
                    "|": ("__ior__", "__or__"),
                    "^": ("__ixor__", "__xor__"),
                    "<<": ("__ilshift__", "__lshift__"),
                    ">>": ("__irshift__", "__rshift__"),
                    "@": ("__imatmul__", "__matmul__"),
                }
                inplace_m, fallback_m = _INPLACE_DUNDERS.get(stmt.op, (None, None))
                owner = None
                method = None
                if inplace_m is not None:
                    owner = self._resolve_method_owner(cls_name, inplace_m)
                    method = inplace_m
                if owner is None and fallback_m is not None:
                    owner = self._resolve_method_owner(cls_name, fallback_m)
                    method = fallback_m
                if owner is not None:
                    self.gen_expr(stmt.value, info)
                    self.emitf(
                        f"mov {self._arg_reg(1)}, rax",
                        f"mov {self._arg_reg(0)}, {mem}",
                    )
                    self.emit_call(self._method_symbol(owner, method))
                    self.emitf(f"mov {mem}, rax")
                    return
                # No dunder found: fall through to integer arithmetic.
            if ty == "float":
                self._gen_expr_as_float(stmt.value, info, A.expr_type(stmt.value))
                self.emitf("movsd xmm1, xmm0", f"movsd xmm0, {mem}")
                # Apply float op in place.
                self._emit_binop_inline_float(stmt.op)
                self.emitf(f"movsd {mem}, xmm0")
            else:
                self.emitf(f"mov rax, {mem}", "push rax")
                self.gen_expr(stmt.value, info)
                self.emitf("mov rbx, rax", "pop rax")
                self._emit_binop_inline(stmt.op)
                self.emitf(f"mov {mem}, rax")
            return
        if isinstance(stmt, A.Return):
            if stmt.value is not None:
                if info.ret_is_float:
                    # Caller reads the result from xmm0. A non-"float"-typed
                    # return expression (e.g. an "any" element read out of an
                    # unannotated `list`, which holds a plain int at runtime)
                    # must be promoted via cvtsi2sd.
                    self._gen_expr_as_float(
                        stmt.value, info, A.expr_type(stmt.value)
                    )
                else:
                    self.gen_expr(stmt.value, info)
            else:
                self.emitf("xor rax, rax")
            self.emitf(f"jmp {self.emit_func_epilogue_label}")
            return
        if isinstance(stmt, A.ExprStmt):
            self.gen_expr(stmt.expr, info)
            return
        if isinstance(stmt, A.If):
            else_lbl = self.fresh("else")
            end_lbl = self.fresh("endif")
            self._gen_truthy_test(stmt.test, info, else_lbl)
            for s in stmt.then:
                self.gen_stmt(s, info)
            self.emitf(f"jmp {end_lbl}")
            self.label(else_lbl)
            for s in stmt.orelse:
                self.gen_stmt(s, info)
            self.label(end_lbl)
            return
        if isinstance(stmt, A.While):
            top = self.fresh("while")
            end = self.fresh("endwhile")
            orelse_w = getattr(stmt, "orelse", [])
            if orelse_w:
                # condition-false → orelse; break → end (past orelse)
                nat = self.fresh("while_else")
                self.loop_labels.append((top, end))
                self.label(top)
                self._gen_truthy_test(stmt.test, info, nat)
                for s in stmt.body:
                    self.gen_stmt(s, info)
                self.emitf(f"jmp {top}")
                self.label(nat)
                for s in orelse_w:
                    self.gen_stmt(s, info)
            else:
                self.loop_labels.append((top, end))
                self.label(top)
                self._gen_truthy_test(stmt.test, info, end)
                for s in stmt.body:
                    self.gen_stmt(s, info)
                self.emitf(f"jmp {top}")
            self.label(end)
            self.loop_labels.pop()
            return
        if isinstance(stmt, A.For):
            self._gen_for(stmt, info)
            return
        if isinstance(stmt, A.Break):
            if not self.loop_labels:
                raise RuntimeError("break outside loop reached codegen")
            self.emitf(f"jmp {self.loop_labels[-1][1]}")
            return
        if isinstance(stmt, A.Continue):
            if not self.loop_labels:
                raise RuntimeError("continue outside loop reached codegen")
            self.emitf(f"jmp {self.loop_labels[-1][0]}")
            return
        if isinstance(stmt, A.IndexAssign):
            # Slice assignment: dst[start:stop] = src_list (in-place, same size).
            if isinstance(stmt.target.index, A.Slice):
                sl: A.Slice = stmt.target.index
                dst_slot = info.locals_[f"__slcasgn_dst_{id(stmt)}"]
                src_slot = info.locals_[f"__slcasgn_src_{id(stmt)}"]
                INT64_MIN = "0x8000000000000000"
                INT64_MAX = "0x7fffffffffffffff"
                # evaluate dst
                self.gen_expr(stmt.target.obj, info)
                self.emitf(f"mov [rbp{dst_slot:+d}], rax")
                # evaluate src
                self.gen_expr(stmt.value, info)
                self.emitf(f"mov [rbp{src_slot:+d}], rax")
                # start
                if sl.start is not None:
                    self.gen_expr(sl.start, info)
                else:
                    self.emitf(f"mov rax, {INT64_MIN}")
                self.emitf("mov rcx, rax")  # rcx = start
                # stop
                if sl.stop is not None:
                    self.gen_expr(sl.stop, info)
                else:
                    self.emitf(f"mov rax, {INT64_MAX}")
                self.emitf("mov rdx, rax")  # rdx = stop
                # call
                self.emitf(
                    f"mov rax, [rbp{dst_slot:+d}]",
                    f"mov rbx, [rbp{src_slot:+d}]",
                    "call _runtime_list_slice_assign",
                )
                return
            obj_t = A.expr_type(stmt.target.obj)
            if getattr(stmt.target, "_setitem_class", None) is not None:
                cls_name = stmt.target._setitem_class  # type: ignore[attr-defined]
                owner = self._resolve_method_owner(cls_name, "__setitem__")
                if owner is not None:
                    # __setitem__(self, key, value) -> 3 args
                    self.gen_expr(stmt.target.obj, info)
                    self.emitf("push rax")  # push self
                    self.gen_expr(stmt.target.index, info)
                    self.emitf("push rax")  # push key
                    self.gen_expr(stmt.value, info)
                    if A.expr_type(stmt.value) == "float":
                        self.emitf("movq rax, xmm0")
                    self.emitf(
                        f"mov {self._arg_reg(2)}, rax",  # arg2 = value
                        "pop rax",
                        f"mov {self._arg_reg(1)}, rax",  # arg1 = key
                        "pop rax",
                        f"mov {self._arg_reg(0)}, rax",  # arg0 = self
                    )
                    self.emit_call(self._method_symbol(owner, "__setitem__"))
                return
            if obj_t == "dict":
                # Eval key, save to frame slot; eval value, save to frame slot;
                # eval header, then call. Frame slots avoid stack misalignment
                # when value is a DictLit or other complex expression that calls.
                key_slot = info.locals_[f"__dictset_key_{id(stmt)}"]
                self.gen_expr(stmt.target.index, info)
                self.emitf(f"mov [rbp{key_slot:+d}], rax")
                self.gen_expr(stmt.value, info)
                if A.expr_type(stmt.value) == "float":
                    self.emitf("movq rax, xmm0")
                self.emitf("push rax")  # value on stack (after key is safe)
                self.gen_expr(stmt.target.obj, info)  # rax = header
                self.emitf(
                    "pop rcx",                         # rcx = value
                    f"mov rbx, [rbp{key_slot:+d}]",   # rbx = key
                    "call _runtime_dict_set",
                )
                return
            self.gen_expr(stmt.target.index, info)
            self.emitf("push rax")
            self.gen_expr(stmt.value, info)
            if A.expr_type(stmt.value) == "float":
                self.emitf("movq rax, xmm0")  # store the raw bit pattern
            self.emitf("push rax")
            self.gen_expr(stmt.target.obj, info)  # rax = header
            pos = self.fresh("idxw_pos")
            self.emitf(
                "pop rbx",  # rbx = value
                "pop rcx",  # rcx = index
                "test rcx, rcx",
                f"jns {pos}",
                f"add rcx, [rax+{self.LIST_LEN_OFF}]",
            )
            self.label(pos)
            self.emitf(
                f"mov rax, [rax+{self.LIST_BUF_OFF}]",
                "mov [rax+rcx*8], rbx",
            )
            return
        if isinstance(stmt, A.AttrAssign):
            # Class-level variable write: `ClassName.x = value` -> store global.
            if isinstance(stmt.obj, A.Name):
                cv = self.class_var_labels.get(f"{stmt.obj.name}.{stmt.name}")
                if cv is not None:
                    if A.expr_type(stmt.value) == "float":
                        self._gen_expr_as_float(stmt.value, info, "float")
                        self.emitf("movq rax, xmm0", f"mov [rel {cv}], rax")
                    else:
                        self.gen_expr(stmt.value, info)
                        self.emitf(f"mov [rel {cv}], rax")
                    return
            # obj.name = value  ->  dict_set(obj, "name", value)
            key_label, _ = self.intern_string(stmt.name)
            self.gen_expr(stmt.value, info)
            if A.expr_type(stmt.value) == "float":
                self.emitf("movq rax, xmm0")  # store the raw bit pattern
            self.emitf("push rax")
            self.gen_expr(stmt.obj, info)  # rax = instance dict
            self.emitf(
                "pop rcx",  # rcx = value
                f"lea rbx, [{key_label}]",  # rbx = key
                "call _runtime_dict_set",
            )
            return
        if isinstance(stmt, A.Try):
            self._gen_try(stmt, info)
            return
        if isinstance(stmt, A.Raise):
            if stmt.value is None:
                # Bare `raise`: re-raise the currently-active exception.
                # _runtime_exc_msg is zero until the first raise, so a bare
                # `raise` outside any handler reports the same error CPython
                # does (RuntimeError: No active exception to re-raise).
                no_exc_lbl, _ = self.intern_string(
                    "No active exception to reraise"
                )
                has_exc = self.fresh("reraise_has_exc")
                after_lbl = self.fresh("reraise_type")
                self.emitf(
                    "mov rax, [rel _runtime_exc_msg]",
                    "test rax, rax",
                    f"jnz {has_exc}",
                    f"lea rax, [{no_exc_lbl}]",
                    f"mov rbx, {self._exc_type_id('RuntimeError')}",
                    f"jmp {after_lbl}",
                )
                self.label(has_exc)
                self.emitf("mov rbx, [rel _runtime_exc_type]")
                self.label(after_lbl)
                self.emitf("call _runtime_raise")
                return
            # `raise SystemExit(code)` is process exit, not an exception: the
            # idiom `raise SystemExit(main())` must end the process with the
            # code, and nothing catches SystemExit in compiled programs.
            if (
                isinstance(stmt.value, A.Call)
                and stmt.value.func == "SystemExit"
            ):
                if stmt.value.args:
                    self.gen_expr(stmt.value.args[0], info)
                else:
                    self.emitf("xor rax, rax")
                self.emitf(f"mov {self._arg_reg(0)}, rax", "call exit")
                return
            # Evaluate message into rax, then call _runtime_raise.
            exc_type_id = self._exc_raise_type_id(stmt.value)
            # For `raise UserExcClass(msg)`: the constructor returns an instance
            # dict (not a string), but _runtime_raise expects a string in rax.
            # Extract the message from the first argument directly.
            if (
                isinstance(stmt.value, A.Call)
                and self._cg_is_exception_class(stmt.value.func)
                and stmt.value.func not in BUILTIN_EXC_IDS
            ):
                # User exception class: extract message from first arg rather
                # than constructing an instance (which would put a dict pointer
                # into rax, but _runtime_raise expects a string).
                if stmt.value.args:
                    self.gen_expr(stmt.value.args[0], info)
                    arg_t = A.expr_type(stmt.value.args[0])
                    if arg_t == "int":
                        self._emit_int_to_str()
                    elif arg_t == "float":
                        self._emit_float_to_str()
                    elif arg_t in ("list", "tuple", "dict", "set"):
                        # `raise MultiSemaError(self._collected_errors)`: the
                        # first arg is a LIST of error messages (or similar
                        # container), not a scalar message -- using its raw
                        # list/dict header pointer as if it were a string
                        # pointer corrupted the printed message into garbage
                        # (the header's first field, e.g. a small capacity
                        # int, read back as a 1-2 byte "string"). Fall back to
                        # a generic placeholder message instead of the
                        # unusable raw container pointer; this loses the
                        # per-error detail under self-compilation (the same
                        # accepted degradation as the rest of this exception
                        # model -- structured data doesn't survive `raise`),
                        # but is no longer corrupted garbage.
                        ph_lbl, _ = self.intern_string(
                            "(multiple errors; detail unavailable)"
                        )
                        self.emitf(f"lea rax, [rel {ph_lbl}]")
                else:
                    cls_lbl, _ = self.intern_string(stmt.value.func)
                    self.emitf(f"lea rax, [{cls_lbl}]")
            elif (
                isinstance(stmt.value, A.Name)
                and self._cg_is_exception_class(stmt.value.name)
                and stmt.value.name not in BUILTIN_EXC_IDS
            ):
                # `raise MyError` (bare class, no args): use class name as msg.
                cls_lbl, _ = self.intern_string(stmt.value.name)
                self.emitf(f"lea rax, [{cls_lbl}]")
            else:
                self.gen_expr(stmt.value, info)
            self.emitf(f"mov rbx, {exc_type_id}", "call _runtime_raise")
            return
        if isinstance(stmt, A.With):
            # Simplified: evaluate the context expr, bind to name if given.
            # No __enter__/__exit__ dispatch — the body runs directly.
            self.gen_expr(stmt.expr, info)
            if stmt.name is not None and stmt.name in info.locals_:
                self.emitf(f"mov [rbp{info.locals_[stmt.name]:+d}], rax")
            for s in stmt.body:
                self.gen_stmt(s, info)
            return
        if isinstance(stmt, A.Nonlocal):
            return  # closures not supported; accept as no-op
        if isinstance(stmt, A.Del):
            # `del x` — zero the slot so the variable can't be accidentally read.
            # `del d[k]` — call dict pop (discard result).
            tgt = stmt.target
            if isinstance(tgt, A.Name) and tgt.name in info.locals_:
                slot = info.locals_[tgt.name]
                self.emitf(f"mov qword [rbp{slot:+d}], 0")
            elif isinstance(tgt, A.Subscript):
                # del xs[i] -> _runtime_list_del(xs, i); del d[key] ->
                # _runtime_dict_pop(d, key), discarding the result either way.
                key_slot = info.locals_.get(f"__del_key_{id(stmt)}")
                fn = (
                    "_runtime_list_del"
                    if A.expr_type(tgt.obj) == "list"
                    else "_runtime_dict_pop"
                )
                if key_slot is not None:
                    self.gen_expr(tgt.index, info)
                    self.emitf(f"mov [rbp{key_slot:+d}], rax")
                    self.gen_expr(tgt.obj, info)
                    self.emitf(f"mov rbx, [rbp{key_slot:+d}]", f"call {fn}")
                else:
                    # Fallback: evaluate for side effects only.
                    self.gen_expr(tgt.obj, info)
            return
        raise NotImplementedError(f"stmt {stmt}")

    # ---- for / range -------------------------------------------------------
    #
    # Lowering:  for v in range(start, stop, step):  body
    #     v = start
    #     stop_slot = stop; step_slot = step
    #   .top:
    #     if step_slot > 0:  if v >= stop_slot: goto end
    #     else:              if v <= stop_slot: goto end
    #   .body:  body
    #   .cont:  v += step_slot; goto .top
    #   .end:
    #
    # For simplicity we evaluate `start`, `stop`, `step` once into the loop
    # variable and the synthetic locals reserved by `_collect_locals`.

    def _list_expr_el_kind(self, expr: A.Expr) -> str:
        """Element kind of a list-typed expression, defaulting to int when the
        expression doesn't carry one (so a slot still gets a sane register
        class). Used to type the targets of a zip loop."""
        if A.expr_type(expr) != "list":
            return "int"
        if isinstance(expr, A.Name):
            return expr.list_el_type
        if isinstance(expr, A.ListLit):
            return expr.el_type
        return getattr(expr, "list_el_type", "int")

    def _for_zip_spec(self, s: A.For):
        """Recognize `for a, b[, c...] in zip(A, B[, C...])` and
        `for i, (a, b[, c...]) in enumerate(zip(A, B[, C...]))`.

        Returns (idx_name_or_None, names_list, exprs_list) or None.
        Mirrors the analyzer's detection so codegen and sema agree on the loop shape."""
        it = s.iter
        if it is None or not isinstance(it, A.Call):
            return None
        if it.func == "zip":
            n = len(it.args)
            if (
                n >= 2
                and len(s.targets) == n
                and all(isinstance(t, str) for t in s.targets)
            ):
                return (None, list(s.targets), list(it.args))
            return None
        if (
            it.func == "enumerate"
            and len(it.args) == 1
            and isinstance(it.args[0], A.Call)
            and it.args[0].func == "zip"
        ):
            z = it.args[0]
            n = len(z.args)
            if (
                n >= 2
                and len(s.targets) == 2
                and isinstance(s.targets[0], str)
                and isinstance(s.targets[1], list)
                and len(s.targets[1]) == n
            ):
                return (
                    s.targets[0],
                    list(s.targets[1]),
                    list(z.args),
                )
            return None
        return None

    def _gen_for(self, stmt: A.For, info: FuncInfo) -> None:
        # Direct field access: stmt is A.For (the function's own parameter
        # type) and orelse: list is always present on it -- getattr() would
        # make its result opaque ("any"-typed) to sema for no reason here.
        orelse_stmts = stmt.orelse
        zspec = self._for_zip_spec(stmt)
        if zspec is not None:
            self._gen_for_zip(stmt, info, zspec)
            for s in orelse_stmts:
                self.gen_stmt(s, info)
            return
        if (
            stmt.iter is not None
            and isinstance(stmt.iter, A.Call)
            and stmt.iter.func == "enumerate"
        ):
            self._gen_for_enumerate(stmt, info)
            for s in orelse_stmts:
                self.gen_stmt(s, info)
            return
        if stmt.iter is not None:
            iter_t = A.expr_type(stmt.iter)
            if getattr(stmt, "iter_is_instance", None) is not None:
                self._gen_for_iter(stmt, info)
                for s in orelse_stmts:
                    self.gen_stmt(s, info)
                return
            if iter_t in ("dict", "set"):
                # Sets are dict-backed (members live as keys); a plain sweep
                # over the slot buffer yields the members, same as a dict's
                # keys.
                self._gen_for_dict(stmt, info)
                for s in orelse_stmts:
                    self.gen_stmt(s, info)
                return
            if iter_t == "str":
                self._gen_for_str(stmt, info)
                for s in orelse_stmts:
                    self.gen_stmt(s, info)
                return
            self._gen_for_list(stmt, info)
            for s in orelse_stmts:
                self.gen_stmt(s, info)
            return
        args = stmt.range_args
        if len(args) == 1:
            start_expr = A.IntLit(0)
            stop_expr = args[0]
            step_expr = A.IntLit(1)
        elif len(args) == 2:
            start_expr, stop_expr = args
            step_expr = A.IntLit(1)
        else:
            start_expr, stop_expr, step_expr = args

        var_off = info.locals_[stmt.var]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]

        # Initialize var, stop, step.
        self.gen_expr(start_expr, info)
        self.emitf(f"mov [rbp{var_off:+d}], rax")
        self.gen_expr(stop_expr, info)
        self.emitf(f"mov [rbp{stop_off:+d}], rax")
        self.gen_expr(step_expr, info)
        self.emitf(f"mov [rbp{step_off:+d}], rax")

        top = self.fresh("for")
        cont = self.fresh("for_cont")
        end = self.fresh("endfor")
        pos_branch = self.fresh("for_step_pos")
        body_lbl = self.fresh("for_body")
        orelse_f = getattr(stmt, "orelse", [])
        # cond_end: where condition-false jumps (start of orelse, or end)
        # break_end: where break jumps (past orelse)
        cond_end = self.fresh("for_else") if orelse_f else end
        break_end = end

        self.loop_labels.append((cont, break_end))
        self.label(top)
        # Choose comparison based on sign of step (computed at runtime so the
        # step can be dynamic without needing constant folding).
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]",
            "test rax, rax",
            f"jg {pos_branch}",
            # step <= 0:  if var <= stop:  goto cond_end
            f"mov rax, [rbp{var_off:+d}]",
            f"mov rbx, [rbp{stop_off:+d}]",
            "cmp rax, rbx",
            f"jle {cond_end}",
            f"jmp {body_lbl}",
        )
        self.label(pos_branch)
        # step > 0:  if var >= stop:  goto cond_end
        self.emitf(
            f"mov rax, [rbp{var_off:+d}]",
            f"mov rbx, [rbp{stop_off:+d}]",
            "cmp rax, rbx",
            f"jge {cond_end}",
        )
        self.label(body_lbl)
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(
            f"mov rax, [rbp{var_off:+d}]",
            f"add rax, [rbp{step_off:+d}]",
            f"mov [rbp{var_off:+d}], rax",
            f"jmp {top}",
        )
        if orelse_f:
            self.label(cond_end)
            for s in orelse_f:
                self.gen_stmt(s, info)
        self.label(end)
        self.loop_labels.pop()

    def _gen_try(self, stmt: A.Try, info: FuncInfo) -> None:
        """Compile  try: body [except ...]+ [else:] [finally:]  via setjmp/longjmp.

        We push a fresh jmp_buf onto the global handler chain, setjmp into
        it, and run the body (+ `else`, on normal completion). A `raise`
        deep in any callee invokes longjmp, which transfers control to the
        post-setjmp return path with eax != 0. We then check the raised
        exception's runtime type id (`_runtime_exc_type`) against each
        `except` clause's declared type(s), in source order, and run the
        first one that matches. If none match, `finally` runs and the
        exception propagates (re-raised) to the enclosing handler.
        """
        orig_id = id(stmt)
        buf_off = info.locals_[f"__try_buf_{orig_id}"]
        parent_off = info.locals_[f"__try_parent_{orig_id}"]
        prev_exc_off = info.locals_[f"__try_prev_exc_{orig_id}"]
        prev_exc_type_off = info.locals_[f"__try_prev_exc_type_{orig_id}"]
        handler_lbl = self.fresh("try_handler")
        end_lbl = self.fresh("try_end")
        # The finally block is emitted on every exit path (normal +
        # exceptional, per handler, and on no-handler-matched propagation);
        # no nested helper — closures are outside the self-host subset.
        fin_body = stmt.finally_body or []
        # `else` runs only on normal (no-exception) completion of the body.
        body = list(stmt.body) + list(stmt.else_body or [])

        handlers: list = []
        if stmt.handler:
            handlers.append((stmt.handler_types, stmt.bind_name, stmt.handler))
        handlers.extend(stmt.extra_handlers)

        # Remember whatever exception (if any) was active when this try
        # started, so a handled exception here doesn't leak as "active" once
        # the handler completes.
        self.emitf(
            "mov rax, [rel _runtime_exc_msg]",
            f"mov [rbp{prev_exc_off:+d}], rax",
            "mov rax, [rel _runtime_exc_type]",
            f"mov [rbp{prev_exc_type_off:+d}], rax",
        )
        # parent_handler = _runtime_handler_top
        self.emitf(
            "mov rax, [rel _runtime_handler_top]", f"mov [rbp{parent_off:+d}], rax"
        )
        # _runtime_handler_top = &buf
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "mov [rel _runtime_handler_top], rax")
        # setjmp(buf). Returns 0 on direct call, nonzero after longjmp.
        self._emit_call_setjmp(buf_off)
        self.emitf("test eax, eax", f"jnz {handler_lbl}")

        # ---- body (+ else) ----
        for s in body:
            self.gen_stmt(s, info)
        # Normal completion: restore parent handler, run finally, skip handlers.
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]",
            "mov [rel _runtime_handler_top], rax",
        )
        for fs in fin_body:
            self.gen_stmt(fs, info)
        self.emitf(f"jmp {end_lbl}")

        # ---- exceptional path ----
        self.label(handler_lbl)
        # Restore parent handler too (we caught it).
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]", "mov [rel _runtime_handler_top], rax"
        )

        # One "check" label per handler, plus a final "no handler matched"
        # label. A typed handler that doesn't match jumps to the next check;
        # a bare `except:` (empty types) always matches and falls straight
        # into its body.
        check_lbls = [self.fresh("try_check") for _ in range(len(handlers) + 1)]
        _hi = 0
        for types, bind_name, hbody in handlers:
            i = _hi
            _hi = _hi + 1
            self.label(check_lbls[i])
            if types:
                run_lbl = self.fresh("try_run")
                self.emitf("mov rax, [rel _runtime_exc_type]")
                for mid in sorted(self._exc_matching_ids(types)):
                    self.emitf(f"cmp rax, {mid}", f"je {run_lbl}")
                self.emitf(f"jmp {check_lbls[i + 1]}")
                self.label(run_lbl)
            if bind_name is not None:
                exc_off = info.locals_[bind_name]
                self.emitf(
                    "mov rax, [rel _runtime_exc_msg]", f"mov [rbp{exc_off:+d}], rax"
                )
            for s in hbody:
                self.gen_stmt(s, info)
            # finally runs after the handler completes. (Caveat: a raise from
            # inside the handler/finally longjmps to the outer handler without
            # re-running this finally — CPython would; acceptable for now.)
            for fs in fin_body:
                self.gen_stmt(fs, info)
            # The exception is now handled: restore whatever was "active"
            # before this try, so a bare `raise` after this point (outside
            # any handler) reports correctly.
            self.emitf(
                f"mov rax, [rbp{prev_exc_off:+d}]",
                "mov [rel _runtime_exc_msg], rax",
                f"mov rax, [rbp{prev_exc_type_off:+d}]",
                "mov [rel _runtime_exc_type], rax",
            )
            self.emitf(f"jmp {end_lbl}")

        # No `except` clause matched (or there were none at all, i.e. a bare
        # `try/finally`): run `finally`, then re-raise so the exception keeps
        # propagating to the enclosing handler.
        self.label(check_lbls[-1])
        for fs in fin_body:
            self.gen_stmt(fs, info)
        self.emitf(
            "mov rax, [rel _runtime_exc_msg]",
            "mov rbx, [rel _runtime_exc_type]",
            "call _runtime_raise",
        )
        self.label(end_lbl)

    def _gen_for_dict(self, stmt: A.For, info: FuncInfo) -> None:
        # Walk order_buf[0..len) directly (insertion order, CPython 3.7+
        # ordering) -- no skip logic needed since order_buf has no gaps.
        var_off = info.locals_[stmt.var]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]

        self.gen_expr(stmt.iter, info)  # rax = header
        self.emitf(
            f"mov [rbp{iter_off:+d}], rax",
            f"mov rbx, [rax+{self.DICT_LEN_OFF}]",
            f"mov [rbp{stop_off:+d}], rbx",
            f"mov qword [rbp{step_off:+d}], 0",
        )  # i = 0

        top = self.fresh("for_dict")
        cont = self.fresh("for_dict_cont")
        end = self.fresh("endfor_dict")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # var = order_buf[i]
        self.emitf(
            f"mov rbx, [rbp{iter_off:+d}]",
            f"mov rbx, [rbx+{self.DICT_ORDER_OFF}]",
            f"mov rcx, [rbp{step_off:+d}]",
            "mov rax, [rbx+rcx*8]",
            f"mov [rbp{var_off:+d}], rax",
        )
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{step_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    def _gen_for_enumerate(self, stmt: A.For, info: FuncInfo) -> None:
        # for i, x in enumerate(inner):  i = index, x = inner[i].
        inner = stmt.iter.args[0]  # type: ignore
        inner_t = A.expr_type(inner)
        is_str = inner_t == "str"
        if inner_t not in ("list", "tuple", "str", "any"):
            raise NotImplementedError(
                f"enumerate() over {inner_t} is not supported yet"
            )
        idx_off = info.locals_[stmt.targets[0]]
        el_off = info.locals_[stmt.targets[1]]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]  # index counter
        start_off = None
        _start_expr = None
        if len(stmt.iter.args) == 2:  # type: ignore
            _start_expr = stmt.iter.args[1]  # type: ignore
        else:
            for _kn, _kv in getattr(stmt.iter, "kwargs", []):
                if _kn == "start":
                    _start_expr = _kv
                    break
        if _start_expr is not None:
            start_off = info.locals_[f"__for_enum_start_{id(stmt)}"]
            self.gen_expr(_start_expr, info)
            self.emitf(f"mov [rbp{start_off:+d}], rax")

        # Cache the iterable pointer and its length. Tuples reuse the list
        # [cap,len,buf] layout, and an opaque (`any`) iterable here is a
        # list/tuple at runtime, so they share the LIST_LEN_OFF/buffer path;
        # only a string differs (length via strlen, element via char_at).
        self.gen_expr(inner, info)
        self.emitf(f"mov [rbp{iter_off:+d}], rax")
        if is_str:
            self._emit_libc_strlen()  # rax = strlen
            self.emitf(f"mov [rbp{stop_off:+d}], rax")
        else:
            self.emitf(
                f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
                f"mov [rbp{stop_off:+d}], rbx",
            )
        self.emitf(f"mov qword [rbp{step_off:+d}], 0")

        top = self.fresh("for_enum")
        cont = self.fresh("for_enum_cont")
        end = self.fresh("endfor_enum")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # index var = counter (+ start, if enumerate() was given one);
        # element var = inner[counter].
        self.emitf(f"mov rax, [rbp{step_off:+d}]")
        if start_off is not None:
            self.emitf(f"add rax, [rbp{start_off:+d}]")
        self.emitf(f"mov [rbp{idx_off:+d}], rax")
        if is_str:
            # char_at(s, i): rax=s, rbx=i -> fresh 1-char str.
            self.emitf(
                f"mov rax, [rbp{iter_off:+d}]",
                f"mov rbx, [rbp{step_off:+d}]",
                "call _runtime_str_char_at",
                f"mov [rbp{el_off:+d}], rax",
            )
        else:
            self.emitf(
                f"mov rbx, [rbp{iter_off:+d}]",
                f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                f"mov rcx, [rbp{step_off:+d}]",
                "mov rax, [rbx+rcx*8]",
                f"mov [rbp{el_off:+d}], rax",
            )
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{step_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    def _gen_for_zip(self, stmt: A.For, info: FuncInfo, zspec) -> None:
        """`for a, b[, c...] in zip(A, B[, C...])` / enumerate(zip(...)).

        Walks N list buffers in lockstep, stopping at the shortest (Python's
        zip semantics). Values are copied as raw 8-byte slots."""
        idx_name, znames, zexprs = zspec
        n = len(znames)
        stop_off = info.locals_[f"__zip_stop_{id(stmt)}"]
        i_off = info.locals_[f"__zip_i_{id(stmt)}"]
        iter_offs = [info.locals_[f"__zip_{k}_{id(stmt)}"] for k in range(n)]
        var_offs = [info.locals_[znames[k]] for k in range(n)]

        # Evaluate all N iterables, cache pointers; compute min length.
        for k, (ze, it_off) in enumerate(zip(zexprs, iter_offs)):
            self.gen_expr(ze, info)
            self.emitf(f"mov [rbp{it_off:+d}], rax")

        # Initialise stop = len(first), then fold min over the rest.
        self.emitf(
            f"mov rax, [rbp{iter_offs[0]:+d}]",
            f"mov rax, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop_off:+d}], rax",
        )
        for k in range(1, n):
            self.emitf(
                f"mov rax, [rbp{stop_off:+d}]",
                f"mov rbx, [rbp{iter_offs[k]:+d}]",
                f"mov rbx, [rbx+{self.LIST_LEN_OFF}]",
                "cmp rax, rbx",
                "cmovg rax, rbx",  # rax = min(cur_stop, len_k)
                f"mov [rbp{stop_off:+d}], rax",
            )
        self.emitf(f"mov qword [rbp{i_off:+d}], 0")

        top = self.fresh("for_zip")
        cont = self.fresh("for_zip_cont")
        end = self.fresh("endfor_zip")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{i_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # Bind each var: var_k = iters[k].buf[i]  (reload each step for realloc safety).
        for k, (it_off, v_off) in enumerate(zip(iter_offs, var_offs)):
            self.emitf(
                f"mov rbx, [rbp{it_off:+d}]",
                f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                f"mov rcx, [rbp{i_off:+d}]",
                "mov rax, [rbx+rcx*8]",
                f"mov [rbp{v_off:+d}], rax",
            )
        if idx_name is not None:
            idx_off = info.locals_[idx_name]
            self.emitf(f"mov rax, [rbp{i_off:+d}]", f"mov [rbp{idx_off:+d}], rax")
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{i_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    def _gen_for_list(self, stmt: A.For, info: FuncInfo) -> None:
        # Lower as:  i = 0; iter = <list>; while i < iter.length: var = iter[i]; body; i += 1
        # We reuse stop_off (length cache) and iter_off (list pointer).
        # `for a, b in pairs` (tuple-unpacking targets) binds each target to a
        # slot of the per-iteration element (itself a list/tuple) instead of a
        # single var.
        unpack = bool(stmt.targets)
        var_off = info.locals_[stmt.var] if not unpack else 0
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]  # repurposed as index

        # Evaluate iterable (header ptr); store header + cached length.
        self.gen_expr(stmt.iter, info)
        self.emitf(
            f"mov [rbp{iter_off:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop_off:+d}], rbx",
            f"mov qword [rbp{step_off:+d}], 0",
        )

        top = self.fresh("for_list")
        cont = self.fresh("for_list_cont")
        end = self.fresh("endfor_list")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # iter is header; reload buffer each iteration (it may have been
        # reallocated by append calls inside the loop). rax = element.
        self.emitf(
            f"mov rbx, [rbp{iter_off:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{step_off:+d}]",
            "mov rax, [rbx+rcx*8]",
        )
        if unpack:
            # Each element is itself a list/tuple (shared layout); unpack its
            # buffer slots into the targets: target[j] = element.buf[j]. A
            # nested group (`for k, (a, b) in ...`) means slot j is itself a
            # tuple — unpack its slots one level deeper.
            self.emitf(f"mov rdx, [rax+{self.LIST_BUF_OFF}]")  # element buffer
            for j, t in enumerate(stmt.targets):
                if isinstance(t, list):
                    self.emitf(
                        f"mov r10, [rdx+{j * 8}]",  # nested tuple header
                        f"mov r10, [r10+{self.LIST_BUF_OFF}]",
                    )
                    for k, nm in enumerate(t):
                        nm_off = info.locals_[nm]
                        self.emitf(
                            f"mov rax, [r10+{k * 8}]", f"mov [rbp{nm_off:+d}], rax"
                        )
                    continue
                t_off = info.locals_[t]
                self.emitf(f"mov rax, [rdx+{j * 8}]", f"mov [rbp{t_off:+d}], rax")
        else:
            self.emitf(f"mov [rbp{var_off:+d}], rax")
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{step_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    def _gen_for_iter(self, stmt: A.For, info: FuncInfo) -> None:
        """`for x in obj` where obj is a user class with __iter__/__next__.

        Lowers as:
            iterator = obj.__iter__()           ; call __iter__, store result
            loop:
                setjmp(buf) — returns 0 normally, 1 if exception raised
                if exception: if StopIteration -> goto end; else re-raise
                result = iterator.__next__()    ; result in rax
                var = result
                body
                jmp loop
            end:
        """
        cls_name = stmt.iter_is_instance
        var_off = info.locals_[stmt.var]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        buf_off = info.locals_[f"__for_iter_buf_{id(stmt)}"]
        parent_off = info.locals_[f"__for_iter_parent_{id(stmt)}"]
        prev_exc_off = info.locals_[f"__for_iter_prev_exc_{id(stmt)}"]
        prev_exc_type_off = info.locals_[f"__for_iter_prev_exc_type_{id(stmt)}"]

        # 1. Evaluate the iterable object -> rax (pointer to instance dict).
        self.gen_expr(stmt.iter, info)
        self.emitf(f"mov [rbp{iter_off:+d}], rax")

        # 2. Call __iter__(obj) -> iterator (may return self, or a new object).
        self.emitf(f"mov {self._arg_reg(0)}, [rbp{iter_off:+d}]")
        self.emit_call(self._method_symbol(cls_name, "__iter__"))
        # rax now holds the iterator object; store it back (may differ from obj).
        self.emitf(f"mov [rbp{iter_off:+d}], rax")

        top = self.fresh("for_iter")
        end = self.fresh("endfor_iter")
        cont = self.fresh("for_iter_cont")
        self.loop_labels.append((cont, end))
        self.label(top)

        # 3. Save previous exception state and install a fresh handler.
        self.emitf(
            "mov rax, [rel _runtime_exc_msg]",
            f"mov [rbp{prev_exc_off:+d}], rax",
            "mov rax, [rel _runtime_exc_type]",
            f"mov [rbp{prev_exc_type_off:+d}], rax",
            "mov rax, [rel _runtime_handler_top]",
            f"mov [rbp{parent_off:+d}], rax",
        )
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "mov [rel _runtime_handler_top], rax")
        self._emit_call_setjmp(buf_off)
        handler_lbl = self.fresh("for_iter_handler")
        self.emitf("test eax, eax", f"jnz {handler_lbl}")

        # Normal path: call __next__(iterator).
        self.emitf(f"mov {self._arg_reg(0)}, [rbp{iter_off:+d}]")
        self.emit_call(self._method_symbol(cls_name, "__next__"))
        # rax = value returned by __next__; store in var.
        self.emitf(f"mov [rbp{var_off:+d}], rax")

        # Restore handler chain and saved exception state (normal path).
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]",
            "mov [rel _runtime_handler_top], rax",
            f"mov rax, [rbp{prev_exc_off:+d}]",
            "mov [rel _runtime_exc_msg], rax",
            f"mov rax, [rbp{prev_exc_type_off:+d}]",
            "mov [rel _runtime_exc_type], rax",
        )

        # Run the loop body.
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"jmp {top}")

        # Handler: restore handler chain, check for StopIteration.
        self.label(handler_lbl)
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]",
            "mov [rel _runtime_handler_top], rax",
        )
        # _runtime_exc_type == 21 means StopIteration (see EXCEPTION_TYPES).
        self.emitf(
            "mov rax, [rel _runtime_exc_type]",
            "cmp rax, 21",
            f"je {end}",
        )
        # Not StopIteration: re-raise so outer try handlers catch it.
        self.emitf(
            "mov rax, [rel _runtime_exc_msg]",
            "mov rbx, [rel _runtime_exc_type]",
            "call _runtime_raise",
        )

        self.label(end)
        self.loop_labels.pop()

    def _gen_for_str(self, stmt: A.For, info: FuncInfo) -> None:
        """`for ch in s:` lowering.

        Walks the string by byte index 0..strlen(s). Each iteration allocates
        a fresh 1-char str via _runtime_str_char_at and binds it to `var`.
        The source pointer is cached in iter_off; length in stop_off; the
        running index in step_off (same slot triplet as the list/dict
        variants, so _collect_locals doesn't need to learn a fourth shape).
        """
        var_off = info.locals_[stmt.var]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]

        # Evaluate the string once; cache pointer and length.
        self.gen_expr(stmt.iter, info)
        self.emitf(f"mov [rbp{iter_off:+d}], rax")
        self._emit_libc_strlen()
        self.emitf(
            f"mov [rbp{stop_off:+d}], rax",
            f"mov qword [rbp{step_off:+d}], 0",
        )

        top = self.fresh("for_str")
        cont = self.fresh("for_str_cont")
        end = self.fresh("endfor_str")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]",
            f"cmp rax, [rbp{stop_off:+d}]",
            f"jge {end}",
        )
        # char_at(s, i): rax=s, rbx=i -> rax = newly-allocated 1-char str.
        self.emitf(
            f"mov rax, [rbp{iter_off:+d}]",
            f"mov rbx, [rbp{step_off:+d}]",
            "call _runtime_str_char_at",
            f"mov [rbp{var_off:+d}], rax",
        )
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{step_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    # ---- expression codegen -------------------------------------------------
    # Convention: result in RAX. Intermediate stashed on the runtime stack.

    def gen_expr(self, expr, info: FuncInfo) -> None:
        if isinstance(expr, A.IntLit):
            self.emitf(f"mov rax, {expr.value}")
            return
        if isinstance(expr, A.FloatLit):
            label = self.intern_float(expr.value)
            expr.label = label
            self.emitf(f"movsd xmm0, [{label}]")
            return
        if isinstance(expr, A.StrLit):
            label, _ = self.intern_string(expr.value)
            expr.label = label
            # The "value" of a string expression is its label address.
            self.emitf(f"lea rax, [{label}]")
            return
        if isinstance(expr, A.Name):
            # Bare imported constant (`from math import pi; print(pi)`).
            if expr.name in self.ffi_consts:
                self._gen_const_load(self.ffi_consts[expr.name])
                return
            # Module dunders the runtime provides as string constants. A
            # compiled program is its own entry point, so __name__ is
            # "__main__"; __file__ is the entry source file's resolved path
            # (threaded in from the driver), or "" if compiling from a
            # string with no real file.
            if expr.name == "__name__":
                label, _ = self.intern_string("__main__")
                self.emitf(f"lea rax, [{label}]")
                return
            if expr.name == "__file__":
                label, _ = self.intern_string(self.entry_path or "")
                self.emitf(f"lea rax, [{label}]")
                return
            # A bare class name used as a value (`Stmt = Assign | AugAssign`,
            # `isinstance(x, Token)`, storing a class object). asmpython has no
            # first-class type objects; we load the class's RTTI id so the value
            # is stable and unique per class. (These appear in type-alias
            # expressions the compiled program never actually inspects.)
            if expr.name in self.class_ids:
                self.emitf(f"mov rax, {self.class_ids[expr.name]}")
                return
            if expr.name in BUILTIN_EXCEPTIONS:
                # A bare builtin-exception name as a value (`raise X` without
                # parens, storing the class): exceptions are message strings,
                # so the class-as-value is its interned name.
                lbl, _ = self.intern_string(expr.name)
                self.emitf(f"lea rax, [{lbl}]")
                return
            if expr.name not in info.locals_ and expr.name not in self.global_vars:
                # A bare reference to a top-level function (not a call): used
                # as a callback value, e.g. `atexit.register(my_handler, ...)`
                # or `signal.signal(SIGINT, handler)`. Evaluate to the
                # function's address so it can be stored in an int slot and
                # called indirectly later.
                if any(f.name == expr.name for f in self.mod.funcs):
                    self.emitf(f"lea rax, [rel {self._user_symbol(expr.name)}]")
                    return
                # A module name (from `import X`) isn't a real heap variable —
                # represent it as null (0). Attribute access on it falls through
                # to the lenient module/any path in _gen_attr.
                if expr.inferred_type in ("module", "any") or expr.name in self.mod.imported_modules:
                    self.emitf("xor rax, rax")
                    return
                raise NameError(f"undefined variable {expr.name}")
            mem = self._var_mem(expr.name, info)
            ty = self._var_type(expr.name, info)
            if expr.name in info.nonlocal_boxes:
                # Nonlocal: param slot holds a box ptr; deref to get the value.
                self.emitf(f"mov rax, {mem}", "mov rax, [rax]")
            elif ty == "float":
                self.emitf(f"movsd xmm0, {mem}")
            else:
                self.emitf(f"mov rax, {mem}")
            return
        if isinstance(expr, A.UnaryOp):
            operand_t = A.expr_type(expr.operand)
            if operand_t == "float" and expr.op == "-":
                self.gen_expr(expr.operand, info)
                # Flip the sign bit directly so -0.0 stays -0.0 (0.0 - 0.0
                # would give +0.0 under IEEE-754 round-to-nearest).
                self.emitf(
                    "movq rax, xmm0",
                    "mov rbx, 0x8000000000000000",
                    "xor rax, rbx",
                    "movq xmm0, rax",
                )
                return
            if operand_t == "float" and expr.op == "not":
                # not <float> -> int 0/1
                self.gen_expr(expr.operand, info)
                zero_lbl = self.intern_float(0.0)
                self.emitf(
                    f"movsd xmm1, [{zero_lbl}]",
                    "ucomisd xmm0, xmm1",
                    "sete al",  # equal & no NaN -> true
                    "movzx rax, al",
                )
                return
            self.gen_expr(expr.operand, info)
            if expr.op in ("-", "~", "+") and getattr(expr, "dunder_owner", None) is not None:
                owner = expr.dunder_owner  # type: ignore[attr-defined]
                method = expr.dunder_method  # type: ignore[attr-defined]
                self.emitf(f"mov {self._arg_reg(0)}, rax")
                self.emit_call(self._method_symbol(owner, method))
            elif expr.op == "-":
                self.emitf("neg rax")
            elif expr.op == "~":
                self.emitf("not rax")
            elif expr.op == "not":
                # Python truthiness mirrors _gen_truthy_test: containers test
                # their length (an empty list is a non-NULL pointer), strings
                # their first byte; scalars/pointers the raw value.
                if operand_t.startswith("instance:"):
                    cls_name = operand_t.split(":", 1)[1]
                    for mname in ("__bool__", "__len__"):
                        owner = self._resolve_method_owner(cls_name, mname)
                        if owner is not None:
                            reg0 = self._arg_reg(0)
                            self.emitf(f"mov {reg0}, rax")
                            self.emit_call(self._method_symbol(owner, mname))
                            break
                    else:
                        # No __bool__/__len__: live instance is always truthy.
                        self.emitf("mov rax, 0")
                        return
                elif operand_t in ("list", "tuple", "dict", "set"):
                    # An Optional container can be a NULL pointer; None is
                    # falsy too, so skip the length-read when rax is 0.
                    skip_lbl = self.fresh("not_container_null")
                    self.emitf(
                        "test rax, rax", f"jz {skip_lbl}",
                        "mov rax, [rax+8]",
                    )
                    self.label(skip_lbl)
                elif operand_t == "str":
                    # A `str | None` value can be a NULL pointer; None is
                    # falsy too, so skip the byte-read when rax is already 0.
                    skip_lbl = self.fresh("not_str_null")
                    self.emitf(
                        "test rax, rax", f"jz {skip_lbl}",
                        "movzx rax, byte [rax]",
                    )
                    self.label(skip_lbl)
                # rax = (rax == 0) ? 1 : 0
                self.emitf("test rax, rax", "sete al", "movzx rax, al")
            return
        if isinstance(expr, A.BinOp):
            self._gen_binop(expr, info)
            return
        if isinstance(expr, A.Compare):
            self._gen_compare(expr, info)
            return
        if isinstance(expr, A.BoolOp):
            self._gen_boolop(expr, info)
            return
        if isinstance(expr, A.IfExp):
            self._gen_ifexp(expr, info)
            return
        if isinstance(expr, A.Call):
            self._gen_call(expr, info)
            return
        if isinstance(expr, A.ListLit):
            self._gen_list_lit(expr, info)
            return
        if isinstance(expr, A.Comprehension):
            self._gen_comprehension(expr, info)
            return
        if isinstance(expr, A.DictComprehension):
            self._gen_dict_comprehension(expr, info)
            return
        if isinstance(expr, A.TupleLit):
            self._gen_tuple_lit(expr, info)
            return
        if isinstance(expr, A.Subscript):
            self._gen_subscript(expr, info)
            return
        if isinstance(expr, A.MethodCall):
            self._gen_method_call(expr, info)
            return
        if isinstance(expr, A.Attr):
            self._gen_attr(expr, info)
            return
        if isinstance(expr, A.DictLit):
            self._gen_dict_lit(expr, info)
            return
        if isinstance(expr, A.SetLit):
            self._gen_set_lit(expr, info)
            return
        if isinstance(expr, A.FString):
            self._gen_fstring(expr, info)
            return
        if isinstance(expr, A.Lambda):
            self._gen_lambda(expr, info)
            return
        if isinstance(expr, A.NamedExpr):
            self._gen_named_expr(expr, info)
            return
        raise NotImplementedError(f"expr {expr}")

    def _gen_named_expr(self, e: "A.NamedExpr", info: FuncInfo) -> None:
        """`target := value` — evaluate `value`, store it into `target`'s
        slot (like an `Assign`), and leave the result in rax/xmm0 so the
        enclosing expression can use it."""
        ty = self._var_type(e.target, info)
        value_t = A.expr_type(e.value)
        mem = self._var_mem(e.target, info)
        if ty == "float":
            self._gen_expr_as_float(e.value, info, value_t)
            self.emitf(f"movsd {mem}, xmm0")
        else:
            self.gen_expr(e.value, info)
            self.emitf(f"mov {mem}, rax")

    def _gen_lambda(self, e: A.Lambda, info: FuncInfo) -> None:
        """Register a hidden top-level function for the lambda and load its address."""
        lname = getattr(e, "func_name", None) or f"_lambda_{id(e):x}"
        # Only register once — gen_expr may be called multiple times for the
        # same Lambda node if it appears in multiple contexts.
        if not any(f.name == lname for f in self.mod.funcs):
            body_ret = A.Return(value=e.body, pos=e.pos)
            fdef = A.FuncDef(
                name=lname,
                params=list(e.params),
                body=[body_ret],
                pos=e.pos,
            )
            self.mod.funcs.append(fdef)
        self.emitf(f"lea rax, [rel {self._user_symbol(lname)}]")

    def _gen_fstring(self, e: A.FString, info: FuncInfo) -> None:
        """Lower an f-string used as a value: convert each segment to str
        (int/float go through str()) and chain through _runtime_str_concat.

        StrLit segments don't need conversion. Empty f-strings produce "".
        """
        if not e.segments:
            label, _ = self.intern_string("")
            self.emitf(f"lea rax, [{label}]")
            return
        acc_slot = info.locals_[f"__fstr_acc_{id(e)}"]

        # First segment seeds the accumulator.
        self._gen_fstring_segment(e.segments[0], info)
        self.emitf(f"mov [rbp{acc_slot:+d}], rax")
        # Each subsequent segment: convert -> concat with accumulator.
        for seg in e.segments[1:]:
            self._gen_fstring_segment(seg, info)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{acc_slot:+d}]",
                "call _runtime_str_concat",
                f"mov [rbp{acc_slot:+d}], rax",
            )
        self.emitf(f"mov rax, [rbp{acc_slot:+d}]")

    def _cfmt_for_spec(self, spec: str, t: str) -> str | None:
        """Translate a Python format spec (the part after `:`) into a C printf
        format for a numeric value, or None if unsupported (caller falls back to
        the default conversion). Handles the common cases:
          float:  .Nf .Ne .Eg  ->  %.Nf etc.
          int:    d  x  X  o  b(unsupported)  Nd(width)  0Nd(zero-pad)
        """
        if not spec:
            return None
        if t == "float":
            # `.2f`, `.0f`, `.3e`, `.4g`; also bare `f`/`e`/`g`.
            if spec and spec[-1] in "feEgG":
                return "%" + spec
            return None
        if t == "int":
            # `d`, `x`, `X`, `o`, width/zero-pad like `05d`, `3d`.
            if spec and spec[-1] in "dxXo":
                # printf needs the length modifier for 64-bit: %lld, %llx, ...
                conv = spec[-1]
                flags = spec[:-1]
                return "%" + flags + "ll" + conv
            if spec.isdigit() or (spec.startswith("0") and spec[1:].isdigit()):
                return "%" + spec + "lld"
            return None
        return None

    def _parse_binary_spec(self, body: str) -> tuple[int, bool] | None:
        """If `body` (an int format-spec, after any `[[fill]align]` prefix
        has been removed) requests binary (`b`) formatting, return
        `(min_total_width, prefix_flag)` -- `prefix_flag` is True for `#b`
        (adds a `0b` prefix, counted in `min_total_width`). Otherwise None.
        A leading sign flag (`+`/`-`/` `) is accepted but ignored (negative
        values always get a `-` sign; positive values never do, matching the
        default `-` sign behavior -- explicit `+`/` ` sign-forcing on binary
        specs isn't supported)."""
        if not body.endswith("b"):
            return None
        rest = body[:-1]
        if rest and rest[0] in "+- ":
            rest = rest[1:]
        prefix_flag = rest.startswith("#")
        if prefix_flag:
            rest = rest[1:]
        if rest and not rest.isdigit():
            return None
        return (int(rest) if rest else 0), prefix_flag

    def _emit_int_to_binary_str(self, width: int, prefix_flag: bool) -> None:
        """In: rax = int. Out: rax = binary-format string (fresh allocation),
        via `_runtime_int_to_binary`. `width` is the total zero-padded width
        (0 = none); `prefix_flag` adds a `0b` prefix."""
        self.emitf(
            f"mov rbx, {width}",
            f"mov rcx, {1 if prefix_flag else 0}",
            "call _runtime_int_to_binary",
        )

    def _strip_grouping_option(self, spec: str) -> tuple[str | None, str]:
        """If `spec` contains a `,` or `_` grouping-option char (PEP
        378/515 thousands separators for int/float format specs), remove it
        and return `(sep_char, spec_without_it)`. Otherwise `(None, spec)`.

        Grouping is applied as a post-processing step on the formatted
        digit string by `_emit_group_digits`, since C's printf has no
        equivalent. If the remaining spec still requests a zero-padded width
        (e.g. `"015"` from `"015,"`), the `0`-flag + width are left in `rest`
        for the caller to detect via `_split_fmt_width` and handle via
        `_emit_group_digits_zeropad` instead of `_emit_group_digits`."""
        if "," in spec:
            _idx = spec.find(",")
            sep, rest = ",", spec[:_idx] + spec[_idx + 1:]
        elif "_" in spec:
            _idx2 = spec.find("_")
            sep, rest = "_", spec[:_idx2] + spec[_idx2 + 1:]
        else:
            return None, spec
        return sep, rest

    def _emit_group_digits(self, sep: str) -> None:
        """In/out: rax = numeric string ptr. Inserts `sep` every 3 digits in
        the integer part (after any leading '-', before any '.'), via
        `_runtime_group_digits` (fresh allocation)."""
        self.emitf(f"mov rbx, {ord(sep)}", "call _runtime_group_digits")

    def _emit_group_digits_zeropad(self, sep: str, width: int) -> None:
        """In/out: rax = numeric string ptr (already formatted, e.g.
        "-1234567.89"). Zero-pads the integer part so the grouped result
        reaches at least `width` chars total (CPython's zero-pad+grouping
        combo, e.g. `f"{n:015,}"` -> `"000,001,234,567"`), then groups via
        `_runtime_group_digits_zeropad`."""
        self.emitf(f"mov rbx, {width}", f"mov rcx, {ord(sep)}", "call _runtime_group_digits_zeropad")

    def _gen_int_value_str(self, seg, info: FuncInfo, rest: str) -> None:
        """Evaluate int-typed `seg`, leaving its formatted-string form (per
        the numeric format-spec `rest`, before any alignment/width padding)
        in rax as a freshly-allocated or shared-buffer string. `rest` may be
        empty (decimal via `_emit_int_to_str`), a printf-style spec for
        `_cfmt_for_spec` (`d`/`x`/`X`/`o`, with width/zero-pad), or a binary
        spec (`b`/`#b`, optionally zero-padded -- handled by
        `_runtime_int_to_binary`, which returns a fresh allocation). A `,`/`_`
        grouping option (e.g. `",d"`, `",}"`) is applied afterward via
        `_emit_group_digits`."""
        sep, rest = self._strip_grouping_option(rest)
        binspec = self._parse_binary_spec(rest) if rest else None
        if binspec is not None:
            width, prefix_flag = binspec
            self.gen_expr(seg, info)
            self._emit_int_to_binary_str(width, prefix_flag)
            return
        cfmt = self._cfmt_for_spec(rest, "int") if rest else None
        self.gen_expr(seg, info)
        if cfmt is not None:
            label, _ = self.intern_string(cfmt)
            self._emit_int_fmt(label)
        else:
            self._emit_int_to_str()
        if sep is not None:
            self._emit_group_digits(sep)

    def _split_fmt_align(self, spec: str) -> tuple[str, str | None, str]:
        """Split an optional `[[fill]align]` prefix off a format spec.
        Returns (fill_char, align_char_or_None, rest)."""
        if len(spec) >= 2 and spec[1] in "<>^=":
            return spec[0], spec[1], spec[2:]
        if len(spec) >= 1 and spec[0] in "<>^=":
            return " ", spec[0], spec[1:]
        return " ", None, spec

    def _split_fmt_width(self, body: str, t: str) -> tuple[int | None, str]:
        """Split a leading width off `body` (after any `[[fill]align]`
        prefix has been removed). For `str`, `body` must be just digits
        optionally followed by `s`. For `int`/`float`, an optional
        sign/`#`/zero-pad-flag prefix is preserved in `rest` (the zero-pad
        flag itself is dropped, since explicit alignment handles fill).
        Returns (width_or_None, rest), where `rest` is suitable for
        `_cfmt_for_spec` (numeric types) or unused (`str`)."""
        if t == "str":
            i = 0
            while i < len(body) and body[i].isdigit():
                i += 1
            if i > 0 and body[i:] in ("", "s"):
                return int(body[:i]), ""
            return None, body
        i = 0
        while i < len(body) and body[i] in "+- #":
            i += 1
        prefix = body[:i]
        j = i
        if j < len(body) and body[j] == "0":
            j += 1  # zero-pad flag: irrelevant once we pad ourselves
        k = j
        while k < len(body) and body[k].isdigit():
            k += 1
        if k == j:
            return None, body
        return int(body[j:k]), prefix + body[k:]

    def _split_str_width_precision(self, body: str) -> tuple[int | None, int | None]:
        """Parse a `str` format-spec `body` (after any `[[fill]align]`
        prefix has been removed) of the form `[width][.precision][s]`.
        Returns `(width_or_None, precision_or_None)`, or `(None, None)` if
        `body` doesn't fully match that grammar (caller leaves the value
        unmodified, matching the pre-existing fallback for unsupported
        specs)."""
        i = 0
        while i < len(body) and body[i].isdigit():
            i += 1
        width = int(body[:i]) if i > 0 else None
        j = i
        precision = None
        if j < len(body) and body[j] == ".":
            k = j + 1
            while k < len(body) and body[k].isdigit():
                k += 1
            if k > j + 1:
                precision = int(body[j + 1 : k])
                j = k
        if j < len(body) and body[j] == "s":
            j += 1
        if j != len(body):
            return None, None
        return width, precision

    def _gen_fstring_aligned(
        self,
        seg,
        info: FuncInfo,
        t: str,
        conv: str,
        width: int | None,
        fill: str,
        align: str,
        rest: str,
        precision: int | None = None,
    ) -> None:
        """Evaluate `seg` to its (unpadded) string form, then pad/justify it
        to `width` with `fill` via the shared `_runtime_str_ljust`/`rjust`/
        `center` helpers, which also safely dup any shared sprintf buffer
        even when `width` doesn't exceed the value's length."""
        if t == "str":
            self.gen_expr(seg, info)
            if conv in ("r", "a"):
                self.emitf("mov rbx, 1", "call _runtime_fmt_elem")
            if precision is not None:
                self.emitf(f"mov rbx, {precision}", "call _runtime_str_truncate")
        elif t == "float":
            sep, rest = self._strip_grouping_option(rest) if rest else (None, rest)
            cfmt = self._cfmt_for_spec(rest, "float") if rest else None
            self._gen_expr_as_float(seg, info, "float")
            if cfmt is not None:
                label, _ = self.intern_string(cfmt)
                self._emit_float_fmt(label)
            else:
                self._emit_float_to_str()
            if sep is not None:
                self._emit_group_digits(sep)
        else:
            # A non-empty format spec on a bool/None formats the underlying
            # int value (0/1), not "True"/"False"/"None" -- matches CPython's
            # int.__format__ (bool has no __format__ override, and a spec'd
            # None is formatted as its 0 stand-in).
            self._gen_int_value_str(seg, info, rest)
        helper = {
            "<": "_runtime_str_ljust",
            ">": "_runtime_str_rjust",
            "^": "_runtime_str_center",
        }[align]
        fill_byte = ord(fill[0]) if fill else 0x20
        w = width if width is not None else 0
        self.emitf(f"mov rbx, {w}", f"mov rcx, {fill_byte}", f"call {helper}")

    def _gen_fstring_segment(self, seg, info: FuncInfo) -> None:
        """Evaluate one f-string segment and leave a str pointer in rax
        (int/float go through str(); str segments stay as-is). A plain method
        rather than a closure inside _gen_fstring, so codegen self-compiles."""
        t = A.expr_type(seg)
        spec = getattr(seg, "fmt_spec", "")
        conv = getattr(seg, "conv_flag", "")
        if spec:
            fill, align, body = self._split_fmt_align(spec)
            width, rest, precision = None, body, None
            if t == "str":
                width, precision = self._split_str_width_precision(body)
                rest = ""
                if align is None and width is not None:
                    fill, align = " ", "<"
            elif align is not None:
                width, rest = self._split_fmt_width(body, t)
            if align in ("<", ">", "^") and t in ("str", "int", "float"):
                self._gen_fstring_aligned(seg, info, t, conv, width, fill, align, rest, precision)
                return
            if t == "str" and precision is not None:
                self.gen_expr(seg, info)
                if conv in ("r", "a"):
                    self.emitf("mov rbx, 1", "call _runtime_fmt_elem")
                self.emitf(f"mov rbx, {precision}", "call _runtime_str_truncate")
                return
            if t in ("int", "float"):
                sep, body2 = self._strip_grouping_option(body)
                if t == "int":
                    binspec = self._parse_binary_spec(body2)
                    if binspec is not None:
                        width, prefix_flag = binspec
                        self.gen_expr(seg, info)
                        self._emit_int_to_binary_str(width, prefix_flag)
                        return
                # Zero-pad width + grouping (`f"{n:015,}"`): CPython zero-pads
                # the integer part so the *grouped* result reaches `zwidth`
                # chars (separator-aware), e.g. -> "000,001,234,567". Strip
                # the zero-pad width here so `cfmt` doesn't also zero-pad
                # (that would double-count), and apply both via
                # `_emit_group_digits_zeropad` below.
                zwidth = None
                if (
                    sep is not None
                    and len(body2) >= 2
                    and body2[0] == "0"
                    and body2[1].isdigit()
                ):
                    zwidth, body2 = self._split_fmt_width(body2, t)
                cfmt = self._cfmt_for_spec(body2, t)
                if cfmt is not None or sep is not None:
                    label = self.intern_string(cfmt)[0] if cfmt is not None else None
                    if t == "float":
                        self._gen_expr_as_float(seg, info, t)
                        if cfmt is not None:
                            self._emit_float_fmt(label)
                        else:
                            self._emit_float_to_str()
                    else:
                        self.gen_expr(seg, info)
                        if cfmt is not None:
                            self._emit_int_fmt(label)
                        else:
                            self._emit_int_to_str()
                    if sep is not None:
                        if zwidth is not None:
                            self._emit_group_digits_zeropad(sep, zwidth)
                        else:
                            self._emit_group_digits(sep)
                    elif cfmt is not None:
                        self.emitf("call _runtime_str_concat_dup")
                    return
        self.gen_expr(seg, info)
        if t == "int":
            if A.is_bool_expr(seg):
                self._emit_bool_to_str()
            elif A.is_none_expr(seg):
                self.emitf("lea rax, [_runtime_none_str]")
            else:
                self._emit_int_to_str()
        elif t == "float":
            self._emit_float_to_str()
        elif t.startswith("instance:"):
            # `!r`/`!a` prefer __repr__ over __str__ (the reverse of the
            # default order), matching repr()'s lookup priority.
            class_name = t.split(":", 1)[1]
            if conv in ("r", "a"):
                resolved = self._resolve_repr_dunder(class_name)
            else:
                resolved = self._resolve_str_dunder(class_name)
            if resolved is not None:
                # A statically `instance:T`-typed expression can still be a
                # runtime NULL pointer when its declared type is actually
                # `Optional[T]` (`T | None`) - is_none_expr only catches a
                # STATICALLY-always-None expression (a bare `None` literal,
                # or a Name sema tracked as currently bound to None), not
                # "this Optional value happens to be None at this call
                # site" (e.g. `f"{maybe_path}"` where maybe_path: Path |
                # None = None and could be either at runtime). Calling the
                # dunder unconditionally then dereferences `self` as NULL
                # inside the method (e.g. Path.__str__ reading self.p) -
                # a real crash found via gdb on the selfhosted binary
                # (driver.py's `_resolve_tool`'s `f"--{name} {override}"`
                # with `override: Path | None = None`). Guarded the same
                # way codegen.py's own _gen_truthy_test already guards
                # str/container NULL checks: test the pointer before
                # dereferencing, route to the same "None" string the
                # int/None case above uses.
                none_lbl = self.fresh("fstr_inst_none")
                end_lbl = self.fresh("fstr_inst_end")
                self.emitf("test rax, rax", f"jz {none_lbl}")
                owner, method = resolved
                self.emitf(f"mov {self._arg_reg(0)}, rax")
                self.emit_call(self._method_symbol(owner, method))
                self.emitf(f"jmp {end_lbl}")
                self.label(none_lbl)
                self.emitf("lea rax, [_runtime_none_str]")
                self.label(end_lbl)
            else:
                self._emit_int_to_str()
        elif t == "str" and conv in ("r", "a"):
            # repr()/!a of a string wraps it in quotes (matches the
            # str-element formatting used for container reprs).
            self.emitf("mov rbx, 1", "call _runtime_fmt_elem")
        # "str"/"any" (without !r/!a) stay as-is

    def _gen_attr(self, e: A.Attr, info: FuncInfo) -> None:
        # Class-level variable read: `ClassName.x`. Loads the static global.
        if isinstance(e.obj, A.Name):
            cv = self.class_var_labels.get(f"{e.obj.name}.{e.name}")
            if cv is not None:
                if A.expr_type(e) == "float":
                    self.emitf(f"movq xmm0, [rel {cv}]")
                else:
                    self.emitf(f"mov rax, [rel {cv}]")
                return
        # Module constant access: math.pi etc.
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
            bindings = self.imported_modules[e.obj.name]
            b = bindings.get(e.name)
            if b is not None and not hasattr(b, "arg_types"):  # Const
                self._gen_const_load(b)
                return
        # Instance attribute access: obj.name -> dict_get_default(obj, "name", 0).
        # Unset attributes return 0 rather than raising — matches the int-default
        # semantics most code expects when fields haven't been initialized yet.
        # An "any" (opaque) value is also treated as an instance dict: at runtime
        # it's a pointer to the same dict layout, so the field read is identical.
        obj_t = A.expr_type(e.obj)
        # "str" included: an `except ... as e` binding is typed str (the
        # message), but a raised exception INSTANCE arrives as the same 8-byte
        # pointer — attribute access reads its field dict, mirroring sema's
        # leniency for the exception-object-as-string case.
        if obj_t.startswith("instance:") or obj_t in ("any", "str"):
            key_label, _ = self.intern_string(e.name)
            self.gen_expr(e.obj, info)  # rax = instance dict
            self.emitf(
                f"lea rbx, [{key_label}]",
                "xor rcx, rcx",  # default = 0
                "call _runtime_dict_get_default",
            )
            if A.expr_type(e) == "float":
                self.emitf("movq xmm0, rax")  # bit pattern -> double
            return
        if obj_t == "module" and e.name in self.class_ids:
            # `A.Call` used as a value: whole-program merging flattened the
            # module, so the attribute IS the merged class — load its RTTI id
            # (mirrors the bare-class-name-as-value rule).
            self.emitf(f"mov rax, {self.class_ids[e.name]}")
            return
        if obj_t == "module":
            # An attribute of a project module that isn't a merged class —
            # e.g. a module-level constant merged as a global. Load the global
            # if it exists; else 0 (opaque-lenient, mirrors unset fields).
            if e.name in self.global_vars:
                self.emitf(f"mov rax, [rel {self._global_label(e.name)}]")
                return
            self.emitf("xor rax, rax")
            return
        # `os.environ`, used any way other than `.get(...)` (which has its
        # own dedicated lowering -- see the MethodCall handling above):
        # `.copy()`, subscript-assign, etc. Sema types this "dict" (see
        # sema.py's Attr check), so codegen must hand back a REAL dict
        # header, not the usual opaque-attribute stub -- a real process
        # environment snapshot is more than this stub spawner needs, so
        # lazily allocate one empty dict the first time it's touched and
        # cache it in a .bss slot; every later read returns the same
        # pointer, so mutations against it persist for the process's
        # lifetime like a real `os.environ` object would.
        if isinstance(e.obj, A.Name) and e.obj.name == "os" and e.name == "environ":
            ready = self.fresh("environ_ready")
            self.emitf("mov rax, [rel _environ_dict]", "test rax, rax", f"jnz {ready}")
            self._emit_malloc(self.DICT_HEADER)
            self.emitf(
                f"mov qword [rax+{self.DICT_CAP_OFF}], 8",
                f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
                f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
                "mov [rel _environ_dict], rax",
            )
            self.emitf(f"mov rbx, {8 * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
            self.emitf("mov rbx, [rel _environ_dict]", f"mov [rbx+{self.DICT_BUF_OFF}], rax")
            self.emitf("mov rbx, 64", "call _runtime_zalloc")
            self.emitf("mov rbx, [rel _environ_dict]", f"mov [rbx+{self.DICT_ORDER_OFF}], rax")
            self.emitf("mov rax, [rel _environ_dict]")
            self.label(ready)
            return
        # Unknown attr on an opaque/int type (external module attribute like
        # os.sep, or unresolved field access). Return 0 as a stub.
        self.gen_expr(e.obj, info)
        self.emitf("xor rax, rax")

    def emit_dict_runtime(self) -> None:
        """Emit all dict-related runtime helpers.

        Internal ABI for these helpers (compiler convention, *not* C ABI):
          rax = primary arg in / result out
          rbx = secondary arg
          rcx = tertiary arg
        They internally translate to the target's libc ABI when calling out.
        """
        if self.use_runtime_lib:
            # Reference rather than define. The library was assembled once
            # by asmpython/_runtime/build.py and gcc will resolve these at link.
            for sym in (
                "_runtime_zalloc",
                "_runtime_hash_string",
                "_runtime_dict_lookup_slot",
                "_runtime_dict_set",
                "_runtime_dict_get",
                "_runtime_dict_get_default",
                "_runtime_dict_contains",
                "_runtime_dict_grow",
                "_runtime_dict_keys",
                "_runtime_dict_values",
                "_runtime_dict_update",
                "_runtime_dict_items",
                "_runtime_sort_str",
                "_runtime_sort_int",
                "_runtime_sort_pairs_str",
                "_runtime_sort_pairs_int",
                "_runtime_list_extend",
                "_runtime_list_slice",
                "_runtime_list_reverse",
                "_runtime_list_insert",
                "_runtime_dict_clear",
                "_runtime_dict_pop",
            ):
                self.emit(f"extern {sym}")
            return
        self.emit("section .text")

        # ---- _runtime_zalloc: malloc rbx bytes, zero-fill, return rax.
        self.label("_runtime_zalloc")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        self.emitf("mov [rbp-8], rbx")
        # malloc(size)
        self.emitf("mov rax, rbx")
        self._emit_libc_malloc_size_in_rax()  # rax = ptr
        # memset(rax, 0, size)
        self.emitf("mov rbx, [rbp-8]")
        self._emit_libc_memset_zero()
        self.emitf("leave", "ret")

        # ---- _runtime_hash_string: FNV-1a 64-bit. rax = str ptr -> rax = hash.
        self.label("_runtime_hash_string")
        # h = 0xcbf29ce484222325; for each byte b: h ^= b; h *= 0x100000001b3
        self.emitf(
            "mov rcx, rax",  # rcx = cursor
            "mov rax, 0xcbf29ce484222325",
        )  # FNV offset basis
        self.emitf("mov r9, 0x100000001b3")  # FNV prime
        self.label("._hs_loop")
        self.emitf(
            "movzx rdx, byte [rcx]",
            "test rdx, rdx",
            "jz ._hs_done",
            "xor rax, rdx",
            "mul r9",  # rax *= r9 (unsigned)
            "inc rcx",
            "jmp ._hs_loop",
        )
        self.label("._hs_done")
        self.emitf("ret")

        # ---- _runtime_dict_lookup_slot
        # In:  rax = header, rbx = key ptr
        # Out: rax = slot ptr; rcx = first-tombstone-or-empty slot ptr.
        # Walks the probe sequence until it finds either:
        #   - a slot whose key matches: returns that slot in rax, rcx = NULL
        #   - an empty slot: returns rax = NULL, rcx = (first tombstone or this empty)
        # Strategy: hash the key, mask to capacity, linear probe.
        self.label("_runtime_dict_lookup_slot")
        # Locals span [rbp-8..rbp-40]; reserve 80 = 48 locals + 32 shadow.
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        # Save inputs.
        self.emitf(
            "mov [rbp-8], rax",  # header
            "mov [rbp-16], rbx",  # key
            "mov qword [rbp-32], 0",
        )  # first_tombstone = NULL
        # Hash the key.
        self.emitf("mov rax, rbx", "call _runtime_hash_string")
        # mask = capacity - 1
        self.emitf(
            "mov rcx, [rbp-8]", f"mov rcx, [rcx+{self.DICT_CAP_OFF}]", "dec rcx"
        )  # mask
        self.emitf(
            "and rax, rcx",  # idx = hash & mask
            "mov [rbp-24], rax",
        )  # idx
        self.label("._dl_probe")
        # slot = buf + idx*16
        self.emitf(
            "mov r8, [rbp-8]",
            f"mov r8, [r8+{self.DICT_BUF_OFF}]",
            "mov r9, [rbp-24]",
            "shl r9, 4",
            "add r8, r9",  # r8 = slot ptr
            "mov r10, [r8]",
        )  # r10 = key in slot
        # Empty?
        self.emitf("test r10, r10", "jz ._dl_empty")
        # Tombstone?
        self.emitf(
            "cmp r10, 1",
            "jne ._dl_compare",
            # Remember the first tombstone we see.
            "mov r11, [rbp-32]",
            "test r11, r11",
            "jnz ._dl_advance",
            "mov [rbp-32], r8",
            "jmp ._dl_advance",
        )
        self.label("._dl_compare")
        # Both string ptrs non-null and non-tombstone — strcmp them.
        self.emitf("mov [rbp-40], r8")  # save slot ptr
        self.emitf(
            "mov rax, r10",  # rax = slot.key
            "mov rbx, [rbp-16]",
        )  # rbx = our key
        self._emit_libc_strcmp()  # rax = result
        self.emitf(
            "test rax, rax",
            "jnz ._dl_advance",
            # Match!
            "mov rax, [rbp-40]",  # slot ptr
            "xor rcx, rcx",
            "leave",
            "ret",
        )
        self.label("._dl_advance")
        self.emitf(
            "mov rax, [rbp-24]",
            "inc rax",
            "mov rcx, [rbp-8]",
            f"mov rcx, [rcx+{self.DICT_CAP_OFF}]",
            "dec rcx",
            "and rax, rcx",
            "mov [rbp-24], rax",
            "jmp ._dl_probe",
        )
        self.label("._dl_empty")
        # Not found. Return rax=NULL, rcx = first_tombstone or this empty slot.
        self.emitf(
            "xor rax, rax",
            "mov rcx, [rbp-32]",
            "test rcx, rcx",
            "jnz ._dl_ret_empty",
            "mov rcx, r8",
        )  # no tombstone; use this empty
        self.label("._dl_ret_empty")
        self.emitf("leave", "ret")

        # ---- _runtime_dict_set
        # rax = header, rbx = key, rcx = value
        # Locals span [rbp-8..rbp-32]; reserve 64 = 32 locals + 32 shadow.
        self.label("_runtime_dict_set")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",  # header
            "mov [rbp-16], rbx",  # key
            "mov [rbp-24], rcx",
        )  # value
        # Check load factor; grow if (length + tombstones) >= cap * 3/4.
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rcx, [rax+{self.DICT_LEN_OFF}]",
            f"add rcx, [rax+{self.DICT_TOMB_OFF}]",
            f"mov rdx, [rax+{self.DICT_CAP_OFF}]",
            "mov r9, rdx",
            "shr rdx, 2",
            "sub r9, rdx",  # r9 = cap * 3/4
            "cmp rcx, r9",
            "jl ._ds_no_grow",
            "call _runtime_dict_grow",
        )
        self.label("._ds_no_grow")
        # Find slot.
        self.emitf(
            "mov rax, [rbp-8]", "mov rbx, [rbp-16]", "call _runtime_dict_lookup_slot"
        )
        # rax = matched slot or NULL; rcx = empty/tombstone slot if no match.
        self.emitf(
            "test rax, rax",
            "jz ._ds_new",
            # Update in place.
            "mov rcx, [rbp-24]",
            "mov [rax+8], rcx",
            "leave",
            "ret",
        )
        self.label("._ds_new")
        # rcx is the insertion slot. Check if it was a tombstone so we
        # decrement the tombstone count.
        self.emitf(
            "mov r8, [rcx]",  # current key in slot
            "cmp r8, 1",
            "jne ._ds_no_tomb",
            "mov r9, [rbp-8]",
            f"dec qword [r9+{self.DICT_TOMB_OFF}]",
        )
        self.label("._ds_no_tomb")
        # strdup the key so the dict owns it.
        self.emitf("mov [rbp-32], rcx")  # save slot ptr
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strdup()  # rax = owned copy
        self.emitf(
            "mov rcx, [rbp-32]",
            "mov [rcx], rax",  # slot.key = strdup'd
            "mov r9, [rbp-24]",
            "mov [rcx+8], r9",  # slot.value
            "mov r9, [rbp-8]",
            # Append the new key to the insertion-order array: order_buf[len] = key.
            f"mov r10, [r9+{self.DICT_ORDER_OFF}]",
            f"mov r11, [r9+{self.DICT_LEN_OFF}]",
            "mov rcx, r11",
            "shl rcx, 3",
            "mov [r10+rcx], rax",
            f"inc qword [r9+{self.DICT_LEN_OFF}]",
            "leave",
            "ret",
        )

        # ---- _runtime_dict_get: raise KeyError if missing.
        # rax = header, rbx = key -> rax = value
        _ke_msg, _ = self.intern_string("KeyError: key not in dict")
        self.label("_runtime_dict_get")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf(
            "test rax, rax",
            "jnz ._dg_found",
            # Missing key -> raise catchable KeyError.
            f"lea rax, [rel {_ke_msg}]",
            f"mov rbx, {self._exc_type_id('KeyError')}",
            "leave",
            "jmp _runtime_raise",
        )
        self.label("._dg_found")
        self.emitf("mov rax, [rax+8]", "leave", "ret")

        # ---- _runtime_dict_get_default
        # rax = header, rbx = key, rcx = default -> rax = value or default
        self.label("_runtime_dict_get_default")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        self.emitf("mov [rbp-8], rcx")  # save default
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf(
            "test rax, rax", "jnz ._dgd_found", "mov rax, [rbp-8]", "leave", "ret"
        )
        self.label("._dgd_found")
        self.emitf("mov rax, [rax+8]", "leave", "ret")

        # ---- _runtime_dict_contains
        # rax = header, rbx = key -> rax = 0/1
        self.label("_runtime_dict_contains")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf("test rax, rax", "setne al", "movzx rax, al", "leave", "ret")

        # ---- _runtime_dict_grow
        # rax = header. Doubles capacity, rehashes all live entries.
        # Strategy: snapshot the old slot buffer, allocate a bigger one,
        # then walk the old slots and call dict_set for each live one.
        # The order array's contents (key pointers, insertion order) don't
        # change across a grow -- only its capacity does -- so it's just
        # copied verbatim into a bigger buffer.
        # Locals span [rbp-8..rbp-72]; reserve 112 = 72 locals + 32 shadow
        # (rounded to 16).
        self.label("_runtime_dict_grow")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf("mov [rbp-8], rax")  # header
        # old_cap, old_buf, old_len, old_order_buf
        self.emitf(
            f"mov rcx, [rax+{self.DICT_CAP_OFF}]",
            "mov [rbp-16], rcx",
            f"mov rcx, [rax+{self.DICT_BUF_OFF}]",
            "mov [rbp-24], rcx",
            f"mov rcx, [rax+{self.DICT_LEN_OFF}]",
            "mov [rbp-56], rcx",
            f"mov rcx, [rax+{self.DICT_ORDER_OFF}]",
            "mov [rbp-64], rcx",
        )
        # new_cap = old_cap * 2
        self.emitf("mov rax, [rbp-16]", "shl rax, 1", "mov [rbp-32], rax")  # new_cap
        # new_buf = zalloc(new_cap * 16)
        self.emitf("mov rbx, rax", "shl rbx, 4", "call _runtime_zalloc")
        self.emitf("mov [rbp-40], rax")  # new_buf
        # new_order_buf = zalloc(new_cap * 8); copy old_order_buf[0..old_len).
        self.emitf("mov rax, [rbp-32]", "mov rbx, rax", "shl rbx, 3", "call _runtime_zalloc")
        self.emitf("mov [rbp-72], rax")  # new_order_buf
        self.emitf(
            "mov rax, [rbp-72]",  # dst
            "mov rbx, [rbp-64]",  # src
            "mov rcx, [rbp-56]",  # old_len
            "shl rcx, 3",  # bytes = old_len * 8
        )
        self._emit_libc_memcpy()
        # Reset header: cap = new_cap; len = 0; tomb = 0; buf = new_buf;
        # order_buf = new_order_buf.
        self.emitf(
            "mov r8, [rbp-8]",
            "mov r9, [rbp-32]",
            f"mov [r8+{self.DICT_CAP_OFF}], r9",
            f"mov qword [r8+{self.DICT_LEN_OFF}], 0",
            f"mov qword [r8+{self.DICT_TOMB_OFF}], 0",
            "mov r9, [rbp-40]",
            f"mov [r8+{self.DICT_BUF_OFF}], r9",
            "mov r9, [rbp-72]",
            f"mov [r8+{self.DICT_ORDER_OFF}], r9",
        )
        # Walk old buffer.
        self.emitf("xor rcx, rcx")  # i
        self.label("._gr_loop")
        self.emitf(
            "cmp rcx, [rbp-16]",
            "jge ._gr_done",
            "mov r8, [rbp-24]",
            "mov rdx, rcx",
            "shl rdx, 4",
            "add r8, rdx",  # r8 = old slot
            "mov r9, [r8]",
            "cmp r9, 1",
            "jbe ._gr_next",  # empty or tombstone
            # Direct slot move into the new buffer, reusing the
            # already-owned key bytes (no double strdup).
            "mov [rbp-48], rcx",
        )  # save i
        self.emitf("mov rax, [rbp-8]", "mov rbx, r9", "call _runtime_dict_lookup_slot")
        # rax should be NULL (the new buffer is empty so far), rcx points to
        # the empty slot we should fill.
        self.emitf(
            "mov r8, [rbp-24]",  # old buf
            "mov rdx, [rbp-48]",
            "shl rdx, 4",
            "add r8, rdx",  # r8 = old slot
            "mov r9, [r8]",  # old key
            "mov r10, [r8+8]",  # old value
            "mov [rcx], r9",  # new slot key
            "mov [rcx+8], r10",  # new slot value
            "mov rdx, [rbp-8]",
            f"inc qword [rdx+{self.DICT_LEN_OFF}]",
            "mov rcx, [rbp-48]",
        )  # restore i
        self.label("._gr_next")
        self.emitf("inc rcx", "jmp ._gr_loop")
        self.label("._gr_done")
        # Free the old slot buffer and the old order array.
        self.emitf("mov rax, [rbp-24]")
        self._emit_libc_free()
        self.emitf("mov rax, [rbp-64]")
        self._emit_libc_free()
        self.emitf("leave", "ret")

        self._emit_dict_keys_or_values_helper("_runtime_dict_keys", value_field=False)
        self._emit_dict_keys_or_values_helper("_runtime_dict_values", value_field=True)
        self._emit_dict_update_helper()
        self._emit_dict_items_helper()
        self._emit_sort_helpers()
        self._emit_sort_pairs_helpers()
        self._emit_list_extend_helper()
        self._emit_list_repeat_helper()
        self._emit_list_slice_helper()
        self._emit_list_slice_step_helper()
        self._emit_list_slice_assign_helper()
        self._emit_list_reverse_helper()
        self._emit_list_insert_helper()
        self._emit_dict_clear_helper()
        self._emit_dict_pop_helper()

        # Error message.
        self.emit("section .rodata")
        self.emit('_runtime_dict_key_error_msg: db "KeyError: key not in dict",10,0')

    def _emit_dict_keys_or_values_helper(self, name: str, *, value_field: bool) -> None:
        """Generate `_runtime_dict_keys` or `_runtime_dict_values`.

        In:  rax = dict header.
        Out: rax = newly-allocated list header.
             - keys: list[str] of live key pointers, in insertion order.
             - values: list[int] of each live key's value, in insertion order.

        Walks order_buf[0..len), which holds exactly the live keys in the
        order they were first inserted (CPython 3.7+ dict ordering).

        Locals span [rbp-8..rbp-32]; reserve 80 = 32 locals + 32 shadow so
        malloc's/lookup_slot's shadow store can't clobber them.
        """
        self.label(name)
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        # [rbp-8]  = dict header (input)
        # [rbp-16] = list header (output)
        # [rbp-24] = list buffer ptr (also stored into header)
        # [rbp-32] = i (order index / write index -- they're the same since
        #             order_buf[0..len) is exactly the live keys)
        self.emitf("mov [rbp-8], rax")
        # n = dict.len
        self.emitf(f"mov rbx, [rax+{self.DICT_LEN_OFF}]")
        # cap = max(n, 4)
        cap_ok = self.fresh("dkv_cap_ok")
        self.emitf("cmp rbx, 4", f"jge {cap_ok}", "mov rbx, 4")
        self.label(cap_ok)
        # Allocate list header (24 bytes).
        self.emitf("mov rcx, 24", "call malloc", "mov [rbp-16], rax")
        # Write cap and len (len = n).
        self.emitf(
            "mov rcx, [rbp-8]",
            f"mov rcx, [rcx+{self.DICT_LEN_OFF}]",  # rcx = n (the real length)
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_LEN_OFF}], rcx",
        )
        # Recompute cap = max(n, 4) for cap field + buffer size.
        self.emitf("cmp rcx, 4")
        cap_ok2 = self.fresh("dkv_cap_ok2")
        self.emitf(f"jge {cap_ok2}", "mov rcx, 4")
        self.label(cap_ok2)
        self.emitf(
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_CAP_OFF}], rcx",
            # rcx = cap, allocate cap*8 bytes for the buffer
            "shl rcx, 3",
            "call malloc",
            "mov [rbp-24], rax",
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_BUF_OFF}], rax",
        )
        # Walk order_buf[0..len): every entry is a live key.
        self.emitf("mov qword [rbp-32], 0")  # i = 0
        loop = self.fresh("dkv_loop")
        done = self.fresh("dkv_done")
        self.label(loop)
        self.emitf(
            "mov rax, [rbp-8]",  # dict header
            f"mov rbx, [rax+{self.DICT_LEN_OFF}]",
            "mov rcx, [rbp-32]",
            "cmp rcx, rbx",
            f"jge {done}",
            # key = order_buf[i]
            f"mov rdx, [rax+{self.DICT_ORDER_OFF}]",
            "mov r9, [rdx+rcx*8]",  # r9 = key ptr
        )
        if value_field:
            self.emitf(
                "mov rbx, r9",
                "call _runtime_dict_lookup_slot",  # rax = slot ptr
                "mov rax, [rax+8]",  # rax = value
                "mov r10, [rbp-24]",  # list buf
                "mov r11, [rbp-32]",
                "mov [r10+r11*8], rax",
            )
        else:
            self.emitf(
                "mov r10, [rbp-24]",  # list buf
                "mov r11, [rbp-32]",
                "mov [r10+r11*8], r9",
            )
        self.emitf("inc qword [rbp-32]", f"jmp {loop}")
        self.label(done)
        # Return the list header.
        self.emitf("mov rax, [rbp-16]", "leave", "ret")

    def _emit_dict_update_helper(self) -> None:
        """`_runtime_dict_update`: dst.update(src) — copy every live entry of
        src into dst (overwriting existing keys), like Python's dict.update.

        In:  rax = dst dict header, rbx = src dict header.
        Out: rax = dst (so the call can leave the receiver in rax).

        Walks src's order_buf[0..src.len) (the live keys in src's insertion
        order) and calls `_runtime_dict_set(dst, key, value)` for each entry,
        so keys already in dst keep their position (only their value is
        updated) while new keys from src are appended to dst in src's
        insertion order -- matching CPython's `dict.update`/`|`/`{**a, **b}`.
        dst/src/i/key are parked in frame slots across calls (lookup_slot and
        dict_set both use r8-r11 as scratch and dict_set may grow dst).
        Locals [rbp-8..rbp-32]; reserve 64 = 32 + 32 shadow.
        """
        self.label("_runtime_dict_update")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",  # dst
            "mov [rbp-16], rbx",  # src
            "mov qword [rbp-24], 0",  # i
        )
        loop = self.fresh("du_loop")
        done = self.fresh("du_done")
        # A NULL src (e.g. an unresolved external attribute's stub value
        # used as if it were a dict) has nothing to copy -- treat it as
        # already-empty rather than dereferencing it.
        self.emitf("mov rax, [rbp-16]", "test rax, rax", f"jz {done}")
        self.label(loop)
        self.emitf(
            "mov rax, [rbp-16]",  # src
            f"mov rbx, [rax+{self.DICT_LEN_OFF}]",
            "mov rcx, [rbp-24]",  # i
            "cmp rcx, rbx",
            f"jge {done}",
            f"mov rdx, [rax+{self.DICT_ORDER_OFF}]",
            "mov r9, [rdx+rcx*8]",  # r9 = key ptr (src.order_buf[i])
            "mov [rbp-32], r9",  # save key ptr across the lookup call
            "mov rbx, r9",
            "call _runtime_dict_lookup_slot",  # rax (src) -> rax = slot ptr
            "mov rcx, [rax+8]",  # rcx = value
            "mov rax, [rbp-8]",  # dst
            "mov rbx, [rbp-32]",  # key
            "call _runtime_dict_set",
        )
        self.emitf("inc qword [rbp-24]", f"jmp {loop}")
        self.label(done)
        self.emitf("mov rax, [rbp-8]", "leave", "ret")

        # ---- _runtime_set_subset
        # rax = a (header), rbx = b (header) -> rax = 1 if every member of a
        # is also a member of b (a <= b, i.e. a.issubset(b)), else 0.
        # Walks a's order_buf, short-circuiting false on the first member of
        # a not found in b via _runtime_dict_contains. The empty set is a
        # subset of everything, including itself, so an empty a (len 0)
        # falls straight through the loop to "all members found" -> 1.
        # Locals [rbp-8..rbp-32]; reserve 64 = 32 + 32 shadow.
        self.label("_runtime_set_subset")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",  # a
            "mov [rbp-16], rbx",  # b
            "mov qword [rbp-24], 0",  # i
        )
        loop = self.fresh("ss_loop")
        miss = self.fresh("ss_miss")
        hit = self.fresh("ss_hit")
        self.label(loop)
        self.emitf(
            "mov rax, [rbp-8]",  # a
            f"mov rbx, [rax+{self.DICT_LEN_OFF}]",
            "mov rcx, [rbp-24]",  # i
            "cmp rcx, rbx",
            f"jge {hit}",  # ran off the end with no miss -> subset
            f"mov rdx, [rax+{self.DICT_ORDER_OFF}]",
            "mov r9, [rdx+rcx*8]",  # r9 = a.order_buf[i] (key ptr)
            "mov [rbp-32], r9",
        )
        self.emitf(
            "mov rax, [rbp-16]",  # b
            "mov rbx, [rbp-32]",  # key
            "call _runtime_dict_contains",
        )
        self.emitf("test rax, rax", f"jz {miss}")
        self.emitf("inc qword [rbp-24]", f"jmp {loop}")
        self.label(miss)
        self.emitf("xor rax, rax", "leave", "ret")
        self.label(hit)
        self.emitf("mov rax, 1", "leave", "ret")

    def _emit_dict_items_helper(self) -> None:
        """`_runtime_dict_items`: d.items() -> list of (key, value) pairs.

        In:  rax = dict header. Out: rax = list header whose elements are
        2-slot tuples (shared list layout) of each live entry's key and value,
        in insertion order (walks order_buf[0..len), CPython 3.7+ ordering).
        Locals [rbp-8..rbp-72]; 72 + 32 shadow = 104, rounded to 112
        (16-aligned). [rbp-72] (`cap`) was added to fix a real bug: the
        original version parked `cap` via push/pop around the first malloc
        call instead of a frame slot, placing the saved value only 8 bytes
        below that call's rsp - squarely inside the 32-byte shadow space
        malloc's own prologue is allowed to write into on Win64, silently
        corrupting it. Every other _runtime_dict_* helper in this file
        already uses frame slots instead of push/pop around a call for
        exactly this reason; this one didn't, and it broke.
        """
        self.label("_runtime_dict_items")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf("mov [rbp-8], rax")  # dict
        # Result list sized to the dict's length (cap >= 4).
        self.emitf(f"mov rbx, [rax+{self.DICT_LEN_OFF}]")
        cap_ok = self.fresh("ditems_cap")
        self.emitf("cmp rbx, 4", f"jge {cap_ok}", "mov rbx, 4")
        self.label(cap_ok)
        # `cap` parked in [rbp-72] across the malloc call - see this
        # function's docstring for why (push/pop landed it in malloc's
        # shadow space).
        self.emitf("mov [rbp-72], rbx", "mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rbx, [rbp-72]",
            "mov [rbp-16], rax",  # list header
            f"mov [rax+{self.LIST_CAP_OFF}], rbx",
            "mov rcx, [rbp-8]",
            f"mov rcx, [rcx+{self.DICT_LEN_OFF}]",
            f"mov [rax+{self.LIST_LEN_OFF}], rcx",
            "mov rax, rbx",
            "shl rax, 3",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",  # list buffer
            "mov rcx, [rbp-16]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov qword [rbp-32], 0",  # i (order index / write index)
        )
        loop = self.fresh("ditems_loop")
        done = self.fresh("ditems_done")
        self.label(loop)
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rbx, [rax+{self.DICT_LEN_OFF}]",
            "mov rcx, [rbp-32]",
            "cmp rcx, rbx",
            f"jge {done}",
            f"mov rdx, [rax+{self.DICT_ORDER_OFF}]",
            "mov r9, [rdx+rcx*8]",  # key ptr
            "mov [rbp-48], r9",  # key
            "mov rbx, r9",
            "call _runtime_dict_lookup_slot",  # rax (dict) -> rax = slot ptr
            "mov rax, [rax+8]",  # value
            "mov [rbp-56], rax",  # value
        )
        # Build the pair tuple: header (cap=2, len=2) + 2-slot buffer.
        self.emitf("mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-64], rax",
            f"mov qword [rax+{self.LIST_CAP_OFF}], 2",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 2",
            "mov rax, 16",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rcx, [rbp-64]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov rdx, [rbp-48]",
            "mov [rax], rdx",
            "mov rdx, [rbp-56]",
            "mov [rax+8], rdx",
            # list_buf[i*8] = pair header
            "mov rax, [rbp-24]",
            "mov rcx, [rbp-32]",
            "mov rdx, [rbp-64]",
            "mov [rax+rcx*8], rdx",
        )
        self.emitf("inc qword [rbp-32]", f"jmp {loop}")
        self.label(done)
        self.emitf("mov rax, [rbp-16]", "leave", "ret")

    def _emit_sort_helpers(self) -> None:
        """`_runtime_sort_str` / `_runtime_sort_int`: in-place insertion sort
        of a list's buffer; returns the same header. Str variant compares with
        `_runtime_str_cmp`, int variant with a signed compare. Loop state lives
        in frame slots (str_cmp clobbers caller-saved regs); the buffer pointer
        is reloaded from the header on each access (it never reallocs here, but
        reloading keeps the code uniform). Locals [rbp-8..rbp-40]; 40+32 -> 80.
        """
        for variant in ("str", "int"):
            name = f"_runtime_sort_{variant}"
            outer = self.fresh(f"so_{variant}_outer")
            inner = self.fresh(f"so_{variant}_inner")
            place = self.fresh(f"so_{variant}_place")
            done = self.fresh(f"so_{variant}_done")
            self.label(name)
            self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
            self.emitf(
                "mov [rbp-8], rax",  # header
                f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
                "mov [rbp-40], rcx",  # n
                "mov qword [rbp-16], 1",  # i
            )
            self.label(outer)
            self.emitf(
                "mov rcx, [rbp-16]",
                "cmp rcx, [rbp-40]",
                f"jge {done}",
                # key = buf[i]; j = i - 1
                "mov rax, [rbp-8]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rax, [rdx+rcx*8]",
                "mov [rbp-32], rax",  # key
                "dec rcx",
                "mov [rbp-24], rcx",  # j
            )
            self.label(inner)
            self.emitf("mov rcx, [rbp-24]", "test rcx, rcx", f"js {place}")
            if variant == "str":
                self.emitf(
                    "mov rax, [rbp-8]",
                    f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                    "mov rax, [rdx+rcx*8]",  # buf[j]
                    "mov rbx, [rbp-32]",  # key
                    "call _runtime_str_cmp",
                    "cmp rax, 0",
                    f"jle {place}",
                )
            else:
                self.emitf(
                    "mov rax, [rbp-8]",
                    f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                    "mov rax, [rdx+rcx*8]",  # buf[j]
                    "cmp rax, [rbp-32]",
                    f"jle {place}",
                )
            # shift: buf[j+1] = buf[j]; j--
            self.emitf(
                "mov rcx, [rbp-24]",
                "mov rax, [rbp-8]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rax, [rdx+rcx*8]",
                "mov [rdx+rcx*8+8], rax",
                "dec qword [rbp-24]",
                f"jmp {inner}",
            )
            self.label(place)
            self.emitf(
                "mov rcx, [rbp-24]",
                "mov rax, [rbp-8]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rbx, [rbp-32]",
                "mov [rdx+rcx*8+8], rbx",  # buf[j+1] = key
                "inc qword [rbp-16]",
                f"jmp {outer}",
            )
            self.label(done)
            self.emitf("mov rax, [rbp-8]", "leave", "ret")

    def _emit_sort_pairs_helpers(self) -> None:
        """`_runtime_sort_pairs_str` / `_runtime_sort_pairs_int`: in-place
        insertion sort of an "elems" list, ordered by a parallel "keys" list
        of equal length (for `sorted(xs, key=...)` / `xs.sort(key=...)`).
        Both buffers are reordered in lockstep; the str/int variant selects
        how *keys* compare. Returns the elems header.

        In:  rax = elems list header, rbx = keys list header.
        Out: rax = elems header (sorted; keys is left sorted too but the
             caller discards it).

        Locals [rbp-8..rbp-56] (56 bytes); 56 + 32 shadow (for
        _runtime_str_cmp) -> rounded to 96.
        """
        for variant in ("str", "int"):
            name = f"_runtime_sort_pairs_{variant}"
            outer = self.fresh(f"sp_{variant}_outer")
            inner = self.fresh(f"sp_{variant}_inner")
            place = self.fresh(f"sp_{variant}_place")
            done = self.fresh(f"sp_{variant}_done")
            self.label(name)
            self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
            self.emitf(
                "mov [rbp-8], rax",  # elems header
                "mov [rbp-16], rbx",  # keys header
                f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
                "mov [rbp-48], rcx",  # n
                "mov qword [rbp-24], 1",  # i
            )
            self.label(outer)
            self.emitf(
                "mov rcx, [rbp-24]",
                "cmp rcx, [rbp-48]",
                f"jge {done}",
                # key_elem = elems_buf[i]; key_key = keys_buf[i]; j = i - 1
                "mov rax, [rbp-8]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rax, [rdx+rcx*8]",
                "mov [rbp-40], rax",  # key_elem
                "mov rax, [rbp-16]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rax, [rdx+rcx*8]",
                "mov [rbp-32], rax",  # key_key
                "dec rcx",
                "mov [rbp-56], rcx",  # j
            )
            self.label(inner)
            self.emitf("mov rcx, [rbp-56]", "test rcx, rcx", f"js {place}")
            if variant == "str":
                self.emitf(
                    "mov rax, [rbp-16]",
                    f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                    "mov rax, [rdx+rcx*8]",  # keys_buf[j]
                    "mov rbx, [rbp-32]",  # key_key
                    "call _runtime_str_cmp",
                    "cmp rax, 0",
                    f"jle {place}",
                )
            else:
                self.emitf(
                    "mov rax, [rbp-16]",
                    f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                    "mov rax, [rdx+rcx*8]",  # keys_buf[j]
                    "cmp rax, [rbp-32]",
                    f"jle {place}",
                )
            # shift: elems_buf[j+1] = elems_buf[j]; keys_buf[j+1] = keys_buf[j]; j--
            self.emitf(
                "mov rcx, [rbp-56]",
                "mov rax, [rbp-8]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rax, [rdx+rcx*8]",
                "mov [rdx+rcx*8+8], rax",
                "mov rax, [rbp-16]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rax, [rdx+rcx*8]",
                "mov [rdx+rcx*8+8], rax",
                "dec qword [rbp-56]",
                f"jmp {inner}",
            )
            self.label(place)
            self.emitf(
                "mov rcx, [rbp-56]",
                "mov rax, [rbp-8]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rbx, [rbp-40]",
                "mov [rdx+rcx*8+8], rbx",  # elems_buf[j+1] = key_elem
                "mov rax, [rbp-16]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "mov rbx, [rbp-32]",
                "mov [rdx+rcx*8+8], rbx",  # keys_buf[j+1] = key_key
                "inc qword [rbp-24]",
                f"jmp {outer}",
            )
            self.label(done)
            self.emitf("mov rax, [rbp-8]", "leave", "ret")

    def _emit_list_extend_helper(self) -> None:
        """`_runtime_list_extend`: dst.extend(src) — append every element of
        src onto dst (lists/tuples share the layout, so src may be either).

        In:  rax = dst list header, rbx = src list/tuple header.
        Out: rax = dst. Locals [rbp-8..rbp-32]; 32 + 32 shadow = 64.
        """
        self.label("_runtime_list_extend")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",  # dst
            "mov [rbp-16], rbx",  # src
            f"mov rcx, [rbx+{self.LIST_LEN_OFF}]",
            "mov [rbp-24], rcx",  # n
            "mov qword [rbp-32], 0",  # i
        )
        loop = self.fresh("lext")
        done = self.fresh("lext_done")
        self.label(loop)
        self.emitf(
            "mov rcx, [rbp-32]",
            "cmp rcx, [rbp-24]",
            f"jge {done}",
            "mov rbx, [rbp-16]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rbx, [rbx+rcx*8]",  # src element
            "mov rax, [rbp-8]",  # dst header
            "call _runtime_list_append",
            "inc qword [rbp-32]",
            f"jmp {loop}",
        )
        self.label(done)
        self.emitf("mov rax, [rbp-8]", "leave", "ret")

    def _emit_list_repeat_helper(self) -> None:
        """`_runtime_list_repeat`: [x, y] * n -> new list with src repeated n times.

        In:  rax = src list/tuple header, rbx = count (int64).
        Out: rax = new list header (len = src.len * n, or empty if n <= 0).

        Strategy: loop count times, calling _runtime_list_extend each iteration
        to append a full copy of src to the result list.
        Frame: [rbp-8]=src, [rbp-16]=count, [rbp-24]=result, [rbp-32]=i.
        +32 bytes shadow = 80; round up to 96 (multiple of 16).
        """
        self.label("_runtime_list_repeat")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf(
            "mov [rbp-8], rax",   # src header
            "mov [rbp-16], rbx",  # count
        )
        # Allocate empty result list (cap 4)
        self.emitf("mov rcx, 24", "call malloc")
        self.emitf(
            "mov qword [rax+0], 4",   # cap
            "mov qword [rax+8], 0",   # len
            "mov [rbp-24], rax",      # result
        )
        self.emitf("mov rcx, 32", "call malloc")  # 4 * 8 = 32 bytes initial buffer
        self.emitf(
            "mov rbx, [rbp-24]",
            "mov [rbx+16], rax",  # LIST_BUF_OFF = 16
        )
        # Loop count times, extending result with src each pass.
        self.emitf("mov qword [rbp-32], 0")  # i = 0
        _lrep_top = self.fresh("lrep_top")
        _lrep_done = self.fresh("lrep_done")
        self.label(_lrep_top)
        self.emitf(
            "mov rax, [rbp-32]",
            "cmp rax, [rbp-16]",
            f"jge {_lrep_done}",
            "mov rax, [rbp-24]",  # dst
            "mov rbx, [rbp-8]",   # src
            "call _runtime_list_extend",
            "mov [rbp-24], rax",  # update result (may have reallocated)
            "inc qword [rbp-32]",
            f"jmp {_lrep_top}",
        )
        self.label(_lrep_done)
        self.emitf("mov rax, [rbp-24]", "leave", "ret")

    def _emit_list_slice_helper(self) -> None:
        """`_runtime_list_slice`: `xs[start:stop]` -> new list (no step yet).

        In:  rax = src list header, rbx = start (or INT64_MIN sentinel),
             rcx = stop (or INT64_MAX sentinel).
        Out: rax = newly-allocated list header.

        Element type is irrelevant at runtime — slots are 8 bytes regardless.
        Locals span [rbp-8..rbp-72] (72 bytes). The inner `call malloc` /
        `call memcpy` need 32 bytes of shadow space at the top of our frame, so
        reserve 72 + 32 = 104, rounded up to the next multiple of 16 -> 112.
        (A previous 80-byte frame let the callee's shadow space overlap and
        clobber the [rbp-48..rbp-72] locals — n, cap, and the new header ptr —
        which corrupted the result and crashed on use.)
        """
        INT64_MIN = "0x8000000000000000"
        INT64_MAX = "0x7fffffffffffffff"

        self.label("_runtime_list_slice")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf(
            "mov [rbp-8], rax",  # src header
            "mov [rbp-16], rbx",  # start raw
            "mov [rbp-24], rcx",  # stop raw
        )
        # len = src.len
        self.emitf(f"mov rax, [rax+{self.LIST_LEN_OFF}]", "mov [rbp-32], rax")
        # Normalize start.
        have_start = self.fresh("ls_start_have")
        s_pos = self.fresh("ls_s_pos")
        s_ge0 = self.fresh("ls_s_ge0")
        s_lel = self.fresh("ls_s_lel")
        self.emitf("mov rax, [rbp-16]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
        self.emitf(f"jne {have_start}", "xor rax, rax")
        self.label(have_start)
        self.emitf("test rax, rax", f"jns {s_pos}", "add rax, [rbp-32]")
        self.label(s_pos)
        self.emitf("test rax, rax", f"jns {s_ge0}", "xor rax, rax")
        self.label(s_ge0)
        self.emitf("cmp rax, [rbp-32]", f"jle {s_lel}", "mov rax, [rbp-32]")
        self.label(s_lel)
        self.emitf("mov [rbp-40], rax")  # effective start
        # Normalize stop.
        have_stop = self.fresh("ls_stop_have")
        t_pos = self.fresh("ls_t_pos")
        t_ge0 = self.fresh("ls_t_ge0")
        t_lel = self.fresh("ls_t_lel")
        self.emitf("mov rax, [rbp-24]", f"mov rbx, {INT64_MAX}", "cmp rax, rbx")
        self.emitf(f"jne {have_stop}", "mov rax, [rbp-32]")
        self.label(have_stop)
        self.emitf("test rax, rax", f"jns {t_pos}", "add rax, [rbp-32]")
        self.label(t_pos)
        self.emitf("test rax, rax", f"jns {t_ge0}", "xor rax, rax")
        self.label(t_ge0)
        self.emitf("cmp rax, [rbp-32]", f"jle {t_lel}", "mov rax, [rbp-32]")
        self.label(t_lel)
        self.emitf("mov [rbp-48], rax")  # effective stop
        # n = max(0, stop - start)
        nle = self.fresh("ls_n_le")
        self.emitf(
            "mov rax, [rbp-48]", "sub rax, [rbp-40]", f"jg {nle}", "xor rax, rax"
        )
        self.label(nle)
        self.emitf("mov [rbp-56], rax")  # n
        # cap = max(n, 4)
        cap_ok = self.fresh("ls_cap_ok")
        self.emitf("cmp rax, 4", f"jge {cap_ok}", "mov rax, 4")
        self.label(cap_ok)
        self.emitf("mov [rbp-64], rax")  # cap
        # Allocate list header (24)
        self.emitf("mov rcx, 24", "call malloc", "mov [rbp-72], rax")
        # Init header
        self.emitf(
            "mov rdx, [rbp-72]",
            "mov rax, [rbp-64]",
            f"mov [rdx+{self.LIST_CAP_OFF}], rax",
            "mov rax, [rbp-56]",
            f"mov [rdx+{self.LIST_LEN_OFF}], rax",
        )
        # Allocate buffer cap*8
        self.emitf("mov rcx, [rbp-64]", "shl rcx, 3", "call malloc")
        self.emitf(
            "mov rdx, [rbp-72]",
            f"mov [rdx+{self.LIST_BUF_OFF}], rax",
        )
        # memcpy(new_buf, src.buf + start*8, n*8) — but n could be 0; skip.
        skip_copy = self.fresh("ls_skip_copy")
        self.emitf("mov rcx, [rbp-56]", "test rcx, rcx", f"jz {skip_copy}")
        self.emitf(
            "shl rcx, 3",
            # src start ptr
            "mov rbx, [rbp-8]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rdx, [rbp-40]",
            "shl rdx, 3",
            "add rbx, rdx",
        )
        self._emit_libc_memcpy()
        self.label(skip_copy)
        self.emitf("mov rax, [rbp-72]", "leave", "ret")

    def _emit_list_slice_step_helper(self) -> None:
        """`_runtime_list_slice_step`: xs[start:stop:step] -> new list.

        In:  rax=src, rbx=start(sentinel INT64_MIN=omit), rcx=stop(sentinel
             INT64_MAX=omit), rdx=step (non-zero).
        Out: rax = new list header.

        Frame slots (rbp-relative):
          -8=src, -16=start_raw, -24=stop_raw, -32=step,
          -40=slen, -48=eff_start, -56=eff_stop, -64=n, -72=cap, -80=hdr,
          -88=i (loop index), -96=out_idx.
        Total locals=96, add 32 shadow = 128 (already 16-byte aligned).
        """
        INT64_MIN = "0x8000000000000000"
        INT64_MAX = "0x7fffffffffffffff"
        self.label("_runtime_list_slice_step")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 128")
        self.emitf(
            "mov [rbp-8], rax",
            "mov [rbp-16], rbx",
            "mov [rbp-24], rcx",
            "mov [rbp-32], rdx",
        )
        # slen = src.len
        self.emitf(f"mov rax, [rax+{self.LIST_LEN_OFF}]", "mov [rbp-40], rax")

        # -- Normalize start -----------------------------------------------
        no_start = self.fresh("lss_no_start")
        has_start = self.fresh("lss_has_start")
        s_pos = self.fresh("lss_s_pos")
        s_nn = self.fresh("lss_s_nn")
        s_lt = self.fresh("lss_s_lt")
        self.emitf(f"mov rax, [rbp-16]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx",
                   f"jne {has_start}")
        self.label(no_start)
        # default: step<0 → len-1, else → 0
        self.emitf("mov rcx, [rbp-32]", "test rcx, rcx", f"jns {has_start}",
                   "mov rax, [rbp-40]", "dec rax", f"jmp {s_nn}")
        self.label(has_start)
        # rax = raw start; wrap negatives
        self.emitf("test rax, rax", f"jns {s_pos}", "add rax, [rbp-40]")
        self.label(s_pos)
        self.emitf("test rax, rax", f"jns {s_nn}", "xor rax, rax")
        self.label(s_nn)
        # step>0: clamp to len; step<0: clamp to len-1
        self.emitf("mov rcx, [rbp-32]", "test rcx, rcx", f"jns {s_lt}")
        self.emitf("cmp rax, [rbp-40]", f"jl {s_lt}", "mov rax, [rbp-40]", "dec rax")
        self.label(s_lt)
        self.emitf("mov [rbp-48], rax")

        # -- Normalize stop -------------------------------------------------
        no_stop = self.fresh("lss_no_stop")
        has_stop = self.fresh("lss_has_stop")
        t_pos = self.fresh("lss_t_pos")
        t_nn = self.fresh("lss_t_nn")
        t_lt = self.fresh("lss_t_lt")
        t_neg1ok = self.fresh("lss_t_neg1ok")
        self.emitf(f"mov rax, [rbp-24]", f"mov rbx, {INT64_MAX}", "cmp rax, rbx",
                   f"jne {has_stop}")
        self.label(no_stop)
        # default: step<0 → -1 (exclusive before 0), else → len
        self.emitf("mov rcx, [rbp-32]", "test rcx, rcx", f"jns {has_stop}",
                   "mov rax, -1", f"jmp {t_neg1ok}")
        self.label(has_stop)
        # For step<0: -1 is a legal exclusive sentinel; don't wrap it.
        self.emitf("mov rcx, [rbp-32]", "test rcx, rcx", f"jns {t_pos}")
        self.emitf("cmp rax, -1", f"je {t_neg1ok}")
        # fall-through: wrap negative stop for neg step too
        self.label(t_pos)
        self.emitf("test rax, rax", f"jns {t_nn}", "add rax, [rbp-40]")
        self.label(t_nn)
        self.emitf("test rax, rax", f"jns {t_lt}", "xor rax, rax")
        self.label(t_lt)
        self.emitf("cmp rax, [rbp-40]", f"jle {t_neg1ok}", "mov rax, [rbp-40]")
        self.label(t_neg1ok)
        self.emitf("mov [rbp-56], rax")  # eff_stop

        # -- Count n (pass 1) -----------------------------------------------
        # i = eff_start; n=0
        # while (step>0 ? i<stop : i>stop): n++, i+=step
        cnt_loop = self.fresh("lss_cnt_loop")
        cnt_done = self.fresh("lss_cnt_done")
        cnt_pos = self.fresh("lss_cnt_pos")
        cnt_neg = self.fresh("lss_cnt_neg")
        self.emitf("mov rax, [rbp-48]", "mov [rbp-88], rax")  # i = start
        self.emitf("xor rax, rax", "mov [rbp-64], rax")  # n = 0
        self.label(cnt_loop)
        self.emitf("mov rcx, [rbp-32]", "test rcx, rcx", f"js {cnt_neg}")
        self.label(cnt_pos)
        self.emitf("mov rax, [rbp-88]", "cmp rax, [rbp-56]", f"jge {cnt_done}")
        self.emitf(f"jmp {cnt_pos}_body")
        self.label(cnt_neg)
        self.emitf("mov rax, [rbp-88]", "cmp rax, [rbp-56]", f"jle {cnt_done}")
        cnt_body = self.fresh("lss_cnt_body")
        self.emitf(f"jmp {cnt_body}")
        self.label(f"{cnt_pos}_body")
        self.label(cnt_body)
        self.emitf("inc qword [rbp-64]",
                   "mov rax, [rbp-88]", "add rax, [rbp-32]", "mov [rbp-88], rax",
                   f"jmp {cnt_loop}")
        self.label(cnt_done)
        # n = [rbp-64]

        # -- Allocate header + buffer (pass 2) --------------------------------
        # cap = max(n, 4)
        cap_ok = self.fresh("lss_cap_ok")
        self.emitf("mov rax, [rbp-64]", "cmp rax, 4", f"jge {cap_ok}", "mov rax, 4")
        self.label(cap_ok)
        self.emitf("mov [rbp-72], rax")
        self.emitf("mov rcx, 24", "call malloc", "mov [rbp-80], rax")
        self.emitf(
            "mov rdx, [rbp-80]",
            "mov rax, [rbp-72]",
            f"mov [rdx+{self.LIST_CAP_OFF}], rax",
            "mov rax, [rbp-64]",
            f"mov [rdx+{self.LIST_LEN_OFF}], rax",
        )
        self.emitf("mov rcx, [rbp-72]", "shl rcx, 3", "call malloc")
        self.emitf("mov rdx, [rbp-80]", f"mov [rdx+{self.LIST_BUF_OFF}], rax")

        # -- Fill loop (pass 2) -----------------------------------------------
        # i = eff_start; out_idx = 0
        fill_loop = self.fresh("lss_fill_loop")
        fill_done = self.fresh("lss_fill_done")
        fill_neg = self.fresh("lss_fill_neg")
        fill_body = self.fresh("lss_fill_body")
        self.emitf("mov rax, [rbp-48]", "mov [rbp-88], rax")
        self.emitf("xor rax, rax", "mov [rbp-96], rax")  # out_idx = 0
        self.label(fill_loop)
        self.emitf("mov rcx, [rbp-32]", "test rcx, rcx", f"js {fill_neg}")
        self.emitf("mov rax, [rbp-88]", "cmp rax, [rbp-56]", f"jge {fill_done}")
        self.emitf(f"jmp {fill_body}")
        self.label(fill_neg)
        self.emitf("mov rax, [rbp-88]", "cmp rax, [rbp-56]", f"jle {fill_done}")
        self.label(fill_body)
        # new_buf[out_idx] = src_buf[i]
        self.emitf(
            "mov rbx, [rbp-8]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rcx, [rbp-88]",
            "mov rax, [rbx+rcx*8]",
            # store into dest buf
            "mov rbx, [rbp-80]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rcx, [rbp-96]",
            "mov [rbx+rcx*8], rax",
        )
        self.emitf("inc qword [rbp-96]")
        self.emitf("mov rax, [rbp-88]", "add rax, [rbp-32]", "mov [rbp-88], rax")
        self.emitf(f"jmp {fill_loop}")
        self.label(fill_done)
        self.emitf("mov rax, [rbp-80]", "leave", "ret")

    def _emit_list_slice_assign_helper(self) -> None:
        """`_runtime_list_slice_assign`: dst[start:stop] = src (in-place).

        In:  rax=dst header, rbx=src header, rcx=start (sentinel INT64_MIN=0),
             rdx=stop (sentinel INT64_MAX=len(dst)).
        Out: nothing (rax trashed).

        Copies min(stop-start, len(src)) elements from src into dst starting at
        the normalized start index. Does NOT resize dst.
        Frame slots:
          [rbp- 8] = dst header
          [rbp-16] = src header
          [rbp-24] = eff_start (normalized)
          [rbp-32] = dst.len
          [rbp-40] = count (elements to copy)
          [rbp-48] = loop i (src index, 0..count-1)
        """
        INT64_MIN = "0x8000000000000000"
        INT64_MAX = "0x7fffffffffffffff"
        self.label("_runtime_list_slice_assign")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",   # dst
            "mov [rbp-16], rbx",  # src
        )
        # dstlen = dst.len
        self.emitf(f"mov rax, [rax+{self.LIST_LEN_OFF}]", "mov [rbp-32], rax")
        # srclen = src.len
        self.emitf("mov rax, [rbp-16]", f"mov rax, [rax+{self.LIST_LEN_OFF}]")
        # normalize start (rcx)
        ns_have = self.fresh("lsa_s_have")
        ns_pos = self.fresh("lsa_s_pos")
        ns_ge0 = self.fresh("lsa_s_ge0")
        ns_lel = self.fresh("lsa_s_lel")
        self.emitf(f"mov r8, {INT64_MIN}", "cmp rcx, r8", f"jne {ns_have}", "xor rcx, rcx")
        self.label(ns_have)
        self.emitf("test rcx, rcx", f"jns {ns_pos}", "add rcx, [rbp-32]")
        self.label(ns_pos)
        self.emitf("test rcx, rcx", f"jns {ns_ge0}", "xor rcx, rcx")
        self.label(ns_ge0)
        self.emitf("cmp rcx, [rbp-32]", f"jle {ns_lel}", "mov rcx, [rbp-32]")
        self.label(ns_lel)
        self.emitf("mov [rbp-24], rcx")  # eff_start
        # normalize stop (rdx) -> compute count = min(stop-start, srclen)
        nt_have = self.fresh("lsa_t_have")
        nt_pos = self.fresh("lsa_t_pos")
        nt_ge0 = self.fresh("lsa_t_ge0")
        nt_lel = self.fresh("lsa_t_lel")
        self.emitf(f"mov r8, {INT64_MAX}", "cmp rdx, r8", f"jne {nt_have}", "mov rdx, [rbp-32]")
        self.label(nt_have)
        self.emitf("test rdx, rdx", f"jns {nt_pos}", "add rdx, [rbp-32]")
        self.label(nt_pos)
        self.emitf("test rdx, rdx", f"jns {nt_ge0}", "xor rdx, rdx")
        self.label(nt_ge0)
        self.emitf("cmp rdx, [rbp-32]", f"jle {nt_lel}", "mov rdx, [rbp-32]")
        self.label(nt_lel)
        # count = min(rdx - eff_start, srclen)
        self.emitf("sub rdx, [rbp-24]")   # rdx = stop - start (may be <= 0)
        lo_lbl = self.fresh("lsa_lo")
        nonneg = self.fresh("lsa_nn")
        self.emitf("test rdx, rdx", f"jns {nonneg}", "xor rdx, rdx")
        self.label(nonneg)
        self.emitf("cmp rdx, rax", f"jle {lo_lbl}", "mov rdx, rax")  # rax=srclen
        self.label(lo_lbl)
        self.emitf("mov [rbp-40], rdx")  # count
        # loop: i = 0 to count-1
        self.emitf("xor rax, rax", "mov [rbp-48], rax")  # i = 0
        lp = self.fresh("lsa_loop")
        le = self.fresh("lsa_done")
        self.label(lp)
        self.emitf("mov rax, [rbp-48]", "cmp rax, [rbp-40]", f"jge {le}")
        # src.buf[i]
        self.emitf(
            "mov rcx, rax",
            "mov rbx, [rbp-16]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rbx, [rbx+rcx*8]",   # rbx = src.buf[i]
        )
        # dst.buf[start + i] = rbx
        self.emitf(
            "mov rdx, [rbp-24]",       # start
            "add rdx, rcx",            # start + i
            "mov rcx, [rbp-8]",
            f"mov rcx, [rcx+{self.LIST_BUF_OFF}]",
            "mov [rcx+rdx*8], rbx",
        )
        self.emitf("inc qword [rbp-48]", f"jmp {lp}")
        self.label(le)
        self.emitf("leave", "ret")

    def _emit_list_reverse_helper(self) -> None:
        """`_runtime_list_reverse`: in-place reverse of a list.

        In:  rax = list header.
        Out: rax = same header.  Uses rbx/rcx/rdx/r8/r9 (caller-saved).
        """
        done = self.fresh("lrev_done")
        loop = self.fresh("lrev_loop")
        self.label("_runtime_list_reverse")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        self.emitf(
            "mov [rbp-8], rax",
            f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
            "test rcx, rcx",
            f"jz {done}",
            f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
            "xor rbx, rbx",        # lo = 0
            "dec rcx",             # hi = len-1
        )
        self.label(loop)
        self.emitf(
            "cmp rbx, rcx",
            f"jge {done}",
            "mov r8, [rdx+rbx*8]",
            "mov r9, [rdx+rcx*8]",
            "mov [rdx+rbx*8], r9",
            "mov [rdx+rcx*8], r8",
            "inc rbx",
            "dec rcx",
            f"jmp {loop}",
        )
        self.label(done)
        self.emitf("mov rax, [rbp-8]", "leave", "ret")

    def _emit_list_insert_helper(self) -> None:
        """`_runtime_list_insert`: insert a value at index i.

        In:  rax = header, rbx = index (clipped to [0,len]), rcx = value.
        Out: rax = same header.
        Appends dummy 0 first (so capacity grows if needed), then shifts
        elements right from len-2 down to index, then writes value at index.
        Locals [rbp-8..rbp-24]; 24 + 32 shadow = 56, round up to 64.
        """
        shift_loop = self.fresh("lins_shift")
        shift_done = self.fresh("lins_done")
        self.label("_runtime_list_insert")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",   # header
            "mov [rbp-16], rbx",  # index
            "mov [rbp-24], rcx",  # value
            # Append dummy 0 to grow len by 1 (handles capacity growth).
            "xor rbx, rbx",
            "call _runtime_list_append",
        )
        # Clip index to [0, len-1] (len was just incremented, so old len = new_len-1).
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
            "dec rcx",             # old_len = new_len - 1
            "mov rbx, [rbp-16]",
            "test rbx, rbx",
            "jns ._li_nonneg",
            "xor rbx, rbx",        # clip to 0
        )
        self.emit("._li_nonneg:")
        self.emitf("cmp rbx, rcx", "jle ._li_clip_ok", "mov rbx, rcx")
        self.emit("._li_clip_ok:")
        self.emitf(
            "mov [rbp-16], rbx",   # save clipped index
            # Shift buf[old_len-1] down to buf[index] one slot right.
            f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
            "dec rcx",             # i = old_len - 1
        )
        self.label(shift_loop)
        self.emitf(
            "cmp rcx, [rbp-16]",
            f"jl {shift_done}",
            "mov r8, [rdx+rcx*8]",
            "mov [rdx+rcx*8+8], r8",
            "dec rcx",
            f"jmp {shift_loop}",
        )
        self.label(shift_done)
        # Write value at index.
        self.emitf(
            "mov rcx, [rbp-16]",
            "mov r8, [rbp-24]",
            "mov [rdx+rcx*8], r8",
            "mov rax, [rbp-8]",
            "leave", "ret",
        )

    def _emit_dict_clear_helper(self) -> None:
        """`_runtime_dict_clear`: remove all entries from a dict/set.

        In:  rax = header.
        Out: rax = same header. Zeros len, tombstones, and all slot key+value ptrs.
        Each dict slot is 16 bytes (key_ptr + value). Zero them all.
        Locals [rbp-8]; 8 + 32 shadow = 40, round up to 48.
        """
        loop = self.fresh("dcl_loop")
        done = self.fresh("dcl_done")
        self.label("_runtime_dict_clear")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self.emitf(
            "mov [rbp-8], rax",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov rcx, [rax+{self.DICT_CAP_OFF}]",
            f"mov rdx, [rax+{self.DICT_BUF_OFF}]",
            "xor rbx, rbx",  # slot index
        )
        self.label(loop)
        self.emitf(
            "cmp rbx, rcx",
            f"jge {done}",
            # Each slot is DICT_SLOT_SIZE=16 bytes; byte offset = rbx * 16.
            "mov r8, rbx",
            "shl r8, 4",
            "mov qword [rdx+r8], 0",    # key_ptr = 0 (empty marker)
            "mov qword [rdx+r8+8], 0",  # value = 0
            "inc rbx",
            f"jmp {loop}",
        )
        self.label(done)
        self.emitf("mov rax, [rbp-8]", "leave", "ret")

    def _emit_dict_pop_helper(self) -> None:
        """`_runtime_dict_pop`: remove and return the value for a key.

        In:  rax = header, rbx = key ptr.
        Out: rax = value (or calls _runtime_raise on missing key).
        Marks the slot as tombstone, decrements len, increments tombstones,
        and compacts the removed key out of the insertion-order array (shift
        every later entry left by one) so iteration order stays correct.
        Locals [rbp-8..rbp-56]; 56 + 32 shadow = 88, round up to 96.
        """
        found = self.fresh("dpop_found")
        self.label("_runtime_dict_pop")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf(
            "mov [rbp-8], rax",
            "mov [rbp-16], rbx",
            "call _runtime_dict_lookup_slot",
            # rax = slot ptr or NULL
            "test rax, rax",
            f"jnz {found}",
        )
        # Not found: raise KeyError.
        msg, _ = self.intern_string("KeyError: key not in dict")
        self.emitf(
            f"lea rax, [{msg}]",
            f"mov rbx, {self._exc_type_id('KeyError')}",
            "call _runtime_raise",
        )
        self.label(found)
        # rax = slot ptr; save value and the key ptr being removed, mark
        # tombstone, and stash order_buf/old_len for the compaction below.
        self.emitf(
            "mov rcx, [rax+8]",    # saved value
            "mov [rbp-24], rcx",
            "mov rcx, [rax]",      # key ptr being removed
            "mov [rbp-32], rcx",
            "mov qword [rax], 1",  # key = tombstone
            "mov qword [rax+8], 0",
            "mov rdx, [rbp-8]",
            f"mov rcx, [rdx+{self.DICT_LEN_OFF}]",
            "mov [rbp-48], rcx",   # old_len
            f"dec qword [rdx+{self.DICT_LEN_OFF}]",
            f"inc qword [rdx+{self.DICT_TOMB_OFF}]",
            f"mov rcx, [rdx+{self.DICT_ORDER_OFF}]",
            "mov [rbp-40], rcx",   # order_buf
            "mov qword [rbp-56], 0",  # i = 0
        )
        # Find the removed key's index in order_buf.
        find_loop = self.fresh("dpop_find")
        shift_loop = self.fresh("dpop_shift")
        shift_done = self.fresh("dpop_shift_done")
        self.label(find_loop)
        self.emitf(
            "mov rax, [rbp-56]",
            "cmp rax, [rbp-48]",
            f"jge {shift_done}",  # not found (shouldn't happen) -> done
            "mov rcx, [rbp-40]",
            "mov rdx, [rbp-32]",
            "cmp [rcx+rax*8], rdx",
            f"je {shift_loop}",
            "inc qword [rbp-56]",
            f"jmp {find_loop}",
        )
        # Shift order_buf[i+1 .. old_len) left by one, closing the gap at i.
        self.label(shift_loop)
        self.emitf(
            "mov rax, [rbp-56]",
            "lea rax, [rax+1]",
            "cmp rax, [rbp-48]",
            f"jge {shift_done}",
            "mov rcx, [rbp-40]",
            "mov rdx, [rcx+rax*8]",  # order_buf[i+1]
            "mov rax, [rbp-56]",
            "mov [rcx+rax*8], rdx",  # order_buf[i] = order_buf[i+1]
            "inc qword [rbp-56]",
            f"jmp {shift_loop}",
        )
        self.label(shift_done)
        self.emitf("mov rax, [rbp-24]", "leave", "ret")

    def _emit_str_slice_step_helper(self) -> None:
        """`_runtime_str_slice_step`: full s[start:stop:step].

        In:  rax = s, rbx = start, rcx = stop, r8 = step.
             Caller passes sentinels for missing endpoints:
               start missing -> INT64_MIN  (0x8000000000000000)
               stop  missing -> INT64_MIN when step < 0, INT64_MAX otherwise.
        Out: rax = newly-allocated substring.

        Step must be non-zero; we don't raise on step=0, the caller's
        responsibility (sema will reject literal 0; runtime 0 falls through
        and returns an empty string).
        """
        INT64_MIN = "0x8000000000000000"

        self.label("_runtime_str_slice_step")
        # Locals span [rbp-8..rbp-72]; reserve 112 = 72 locals + 32 shadow,
        # rounded up to a multiple of 16.
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf(
            "mov [rbp-8], rax",  # s
            "mov [rbp-16], rbx",  # start (raw)
            "mov [rbp-24], rcx",  # stop  (raw)
            "mov [rbp-32], r8",  # step
        )
        # len = strlen(s)
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-40], rax")  # len

        # Normalize start.
        # if step > 0: default = 0, clamp to [0, len]
        # if step < 0: default = len - 1, clamp to [-1, len - 1]
        step_pos = self.fresh("ssl_step_pos")
        step_neg = self.fresh("ssl_step_neg")
        start_done = self.fresh("ssl_start_done")
        self.emitf(
            "mov rax, [rbp-32]", "test rax, rax", f"jg {step_pos}", f"jmp {step_neg}"
        )

        self.label(step_pos)
        # start (missing sentinel -> 0; else normalize + clamp to [0, len])
        self.emitf("mov rax, [rbp-16]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
        sp_have = self.fresh("ssl_sp_have")
        self.emitf(f"jne {sp_have}", "xor rax, rax")  # default 0
        self.label(sp_have)
        sp_pos = self.fresh("ssl_sp_pos")
        self.emitf("test rax, rax", f"jns {sp_pos}", "add rax, [rbp-40]")
        self.label(sp_pos)
        self.emitf("test rax, rax")
        sp_ge0 = self.fresh("ssl_sp_ge0")
        self.emitf(f"jns {sp_ge0}", "xor rax, rax")
        self.label(sp_ge0)
        self.emitf("cmp rax, [rbp-40]")
        sp_lel = self.fresh("ssl_sp_lel")
        self.emitf(f"jle {sp_lel}", "mov rax, [rbp-40]")
        self.label(sp_lel)
        self.emitf("mov [rbp-48], rax")  # effective start
        # stop (missing sentinel INT64_MIN -> len; else normalize + clamp)
        self.emitf("mov rax, [rbp-24]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
        st_have_p = self.fresh("ssl_st_have_p")
        self.emitf(f"jne {st_have_p}", "mov rax, [rbp-40]", f"jmp {st_have_p}_done")
        self.label(st_have_p)
        st_pos = self.fresh("ssl_st_pos")
        self.emitf("test rax, rax", f"jns {st_pos}", "add rax, [rbp-40]")
        self.label(st_pos)
        self.emitf("test rax, rax")
        st_ge0 = self.fresh("ssl_st_ge0")
        self.emitf(f"jns {st_ge0}", "xor rax, rax")
        self.label(st_ge0)
        self.emitf("cmp rax, [rbp-40]")
        st_lel = self.fresh("ssl_st_lel")
        self.emitf(f"jle {st_lel}", "mov rax, [rbp-40]")
        self.label(st_lel)
        self.label(f"{st_have_p}_done")
        self.emitf("mov [rbp-56], rax")  # effective stop
        self.emitf(f"jmp {start_done}")

        self.label(step_neg)
        # start: missing -> len - 1
        self.emitf("mov rax, [rbp-16]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
        sn_have = self.fresh("ssl_sn_have")
        self.emitf(f"jne {sn_have}", "mov rax, [rbp-40]", "dec rax")
        self.label(sn_have)
        sn_pos = self.fresh("ssl_sn_pos")
        self.emitf("test rax, rax", f"jns {sn_pos}", "add rax, [rbp-40]")
        self.label(sn_pos)
        # Clamp to [-1, len-1]
        self.emitf("cmp rax, -1")
        sn_gem1 = self.fresh("ssl_sn_gem1")
        self.emitf(f"jge {sn_gem1}", "mov rax, -1")
        self.label(sn_gem1)
        self.emitf("mov rbx, [rbp-40]", "dec rbx", "cmp rax, rbx")
        sn_lel = self.fresh("ssl_sn_lel")
        self.emitf(f"jle {sn_lel}", "mov rax, rbx")
        self.label(sn_lel)
        self.emitf("mov [rbp-48], rax")
        # stop: missing -> -1
        self.emitf("mov rax, [rbp-24]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
        tn_have = self.fresh("ssl_tn_have")
        self.emitf(f"jne {tn_have}", "mov rax, -1", f"jmp {tn_have}_done")
        self.label(tn_have)
        tn_pos = self.fresh("ssl_tn_pos")
        self.emitf("test rax, rax", f"jns {tn_pos}", "add rax, [rbp-40]")
        self.label(tn_pos)
        # Clamp stop to [-1, len-1] (CPython does this for neg step).
        self.emitf("cmp rax, -1")
        tn_gem1 = self.fresh("ssl_tn_gem1")
        self.emitf(f"jge {tn_gem1}", "mov rax, -1")
        self.label(tn_gem1)
        self.emitf("mov rbx, [rbp-40]", "dec rbx", "cmp rax, rbx")
        tn_lel = self.fresh("ssl_tn_lel")
        self.emitf(f"jle {tn_lel}", "mov rax, rbx")
        self.label(tn_lel)
        self.label(f"{tn_have}_done")
        self.emitf("mov [rbp-56], rax")

        self.label(start_done)
        # Compute output length n = max(0, ceil_div(|stop - start|, |step|)).
        # We'll compute (stop - start) / step rounded toward zero properly
        # for the loop count. Simpler: compute n by counting steps in a loop.
        # We just allocate (len + 1) bytes — generous but safe — and fill.
        self.emitf("mov rax, [rbp-40]", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-64], rax")  # output buffer

        # i = effective_start, w = 0
        self.emitf("mov rax, [rbp-48]", "mov [rbp-72], rax")
        self.emitf("xor rax, rax", "mov [rbp-80], rax")  # write index

        loop = self.fresh("ssl_loop")
        done = self.fresh("ssl_done")
        # Loop condition depends on step sign.
        self.label(loop)
        self.emitf("mov rax, [rbp-32]", "test rax, rax")
        lp_neg = self.fresh("ssl_lp_neg")
        self.emitf(f"js {lp_neg}")
        # step > 0: while i < stop
        self.emitf("mov rax, [rbp-72]", "cmp rax, [rbp-56]", f"jge {done}")
        skip_neg = self.fresh("ssl_skipneg")
        self.emitf(f"jmp {skip_neg}")
        self.label(lp_neg)
        # step < 0: while i > stop
        self.emitf("mov rax, [rbp-72]", "cmp rax, [rbp-56]", f"jle {done}")
        self.label(skip_neg)
        # buf[w] = s[i]
        self.emitf(
            "mov rbx, [rbp-8]",
            "mov rcx, [rbp-72]",
            "movzx rdx, byte [rbx+rcx]",
            "mov r8, [rbp-64]",
            "mov r9, [rbp-80]",
            "mov [r8+r9], dl",
            "inc qword [rbp-80]",
            # i += step
            "mov rax, [rbp-72]",
            "add rax, [rbp-32]",
            "mov [rbp-72], rax",
            f"jmp {loop}",
        )
        self.label(done)
        # nul-terminate at w, return buffer.
        self.emitf(
            "mov rax, [rbp-64]",
            "mov rcx, [rbp-80]",
            "mov byte [rax+rcx], 0",
            "leave",
            "ret",
        )

    def emit_string_runtime(self) -> None:
        """Runtime helpers for first-class string operations: concat, repeat,
        compare, character extract, slicing, substring search.

        Internal ABI: rax = primary arg / result; rbx = second arg; rcx =
        third arg. Callers spill to scratch slots as needed across calls.
        """
        if self.use_runtime_lib:
            for sym in (
                "_runtime_str_concat",
                "_runtime_int_to_base",
                "_runtime_int_to_binary",
                "_runtime_group_digits",
                "_runtime_group_digits_zeropad",
                "_runtime_divmod",
                "_runtime_str_repeat",
                "_runtime_str_eq",
                "_runtime_str_cmp",
                "_runtime_str_char_at",
                "_runtime_str_slice",
                "_runtime_str_slice_step",
                "_runtime_str_contains",
                "_runtime_str_index_of",
                "_runtime_str_count",
                "_runtime_str_starts_with",
                "_runtime_str_ends_with",
                "_runtime_str_removeprefix",
                "_runtime_str_removesuffix",
                "_runtime_str_upper",
                "_runtime_str_lower",
                "_runtime_str_capitalize",
                "_runtime_str_swapcase",
                "_runtime_str_title",
                "_runtime_str_strip",
                "_runtime_str_lstrip",
                "_runtime_str_rstrip",
                "_runtime_str_zfill",
                "_runtime_str_ljust",
                "_runtime_str_rjust",
                "_runtime_str_center",
                "_runtime_str_truncate",
                "_runtime_str_replace",
                "_runtime_str_split",
                "_runtime_str_splitlines",
                "_runtime_str_join",
                "_runtime_str_partition",
                "_runtime_str_rpartition",
                "_runtime_str_rsplit",
                "_runtime_chr",
                "_runtime_str_isdigit",
                "_runtime_str_isalpha",
                "_runtime_str_isalnum",
                "_runtime_str_isspace",
                "_runtime_str_isupper",
                "_runtime_str_islower",
            ):
                self.emit(f"extern {sym}")
            return
        self.emit("section .rodata")
        self.emit('_runtime_str_to_int_err: db "invalid literal for int() with base 10",0')
        self.emit("section .text")

        # ---- _runtime_str_to_int ---------------------------------------------
        # rax = str ptr -> rax = int64, or raises ValueError if not a valid
        # decimal integer literal.  Leading/trailing whitespace is stripped
        # (matching Python's int() semantics). Uses strtoll with an endptr to
        # detect leftover characters after the number.
        # Frame: [rbp-8]=result, [rbp-16]=endptr storage. +32 shadow on Win64.
        self.label("_runtime_str_to_int")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        _sti_skip_ws   = self.fresh("sti_skip_ws")
        _sti_adv_ws    = self.fresh("sti_adv_ws")
        _sti_ws_done   = self.fresh("sti_ws_done")
        _sti_trail     = self.fresh("sti_trail")
        _sti_adv_trail = self.fresh("sti_adv_trail")
        _sti_trail_ok  = self.fresh("sti_trail_ok")
        _sti_ok        = self.fresh("sti_ok")
        # Skip leading whitespace
        self.label(_sti_skip_ws)
        self.emitf("movzx rcx, byte [rax]")
        self.emitf(f"cmp rcx, ' '",  f"je {_sti_adv_ws}")
        self.emitf(f"cmp rcx, 9",    f"je {_sti_adv_ws}")
        self.emitf(f"cmp rcx, 10",   f"je {_sti_adv_ws}")
        self.emitf(f"cmp rcx, 13",   f"je {_sti_adv_ws}")
        self.emitf(f"jmp {_sti_ws_done}")
        self.label(_sti_adv_ws)
        self.emitf("inc rax", f"jmp {_sti_skip_ws}")
        self.label(_sti_ws_done)
        # Empty or all-whitespace → raise
        self.emitf(
            "test rcx, rcx",
        )
        _sti_raise = self.fresh("sti_raise")
        self.emitf(f"jz {_sti_raise}")
        # strtoll(rax, &[rbp-16], 10)
        self.emitf("lea rbx, [rbp-16]", "mov rcx, 10")
        self._emit_strtoll_endptr()
        self.emitf("mov [rbp-8], rax")
        # Skip trailing whitespace in endptr
        self.emitf("mov rax, [rbp-16]")
        self.label(_sti_trail)
        self.emitf("movzx rcx, byte [rax]")
        self.emitf(f"cmp rcx, ' '",  f"je {_sti_adv_trail}")
        self.emitf(f"cmp rcx, 9",    f"je {_sti_adv_trail}")
        self.emitf(f"cmp rcx, 10",   f"je {_sti_adv_trail}")
        self.emitf(f"cmp rcx, 13",   f"je {_sti_adv_trail}")
        self.emitf(f"jmp {_sti_trail_ok}")
        self.label(_sti_adv_trail)
        self.emitf("inc rax", f"jmp {_sti_trail}")
        self.label(_sti_trail_ok)
        # *endptr == '\0' → valid; else raise
        self.emitf("test rcx, rcx", f"jz {_sti_ok}")
        self.label(_sti_raise)
        self.emitf(
            "lea rax, [rel _runtime_str_to_int_err]",
            f"mov rbx, {self._exc_type_id('ValueError')}",
            "leave",
            "jmp _runtime_raise",
        )
        self.label(_sti_ok)
        self.emitf("mov rax, [rbp-8]", "leave", "ret")

        # ---- _runtime_str_concat ---------------------------------------------
        # rax = a (str ptr), rbx = b (str ptr) -> rax = newly-allocated concat.
        # Layout of work: strlen(a), strlen(b), malloc(la+lb+1), memcpy each,
        # store NUL.
        #
        # NOTE: each runtime helper reserves 32 bytes of shadow space below
        # the locals area so callees can spill into it on Win64. Failing to
        # do so corrupts our locals when callees write through `[rsp]..[rsp+31]`.
        # 64 bytes of locals + 32 shadow = 96; keep at multiple of 16.
        self.label("_runtime_str_concat")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        # [rbp-8] = a, [rbp-16] = b, [rbp-24] = la, [rbp-32] = lb, [rbp-40] = new ptr
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        # la = strlen(a)
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")
        # lb = strlen(b)
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")
        # total = la + lb + 1
        self.emitf("mov rax, [rbp-24]", "add rax, [rbp-32]", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-40], rax")
        # memcpy(new, a, la)
        self.emitf(
            "mov rax, rax",  # dst
            "mov rbx, [rbp-8]",  # src
            "mov rcx, [rbp-24]",
        )  # n
        self._emit_libc_memcpy()
        # memcpy(new+la, b, lb)
        self.emitf(
            "mov rax, [rbp-40]",
            "add rax, [rbp-24]",
            "mov rbx, [rbp-16]",
            "mov rcx, [rbp-32]",
        )
        self._emit_libc_memcpy()
        # nul-terminate at new[la+lb]
        self.emitf(
            "mov rax, [rbp-40]",
            "mov rbx, [rbp-24]",
            "add rbx, [rbp-32]",
            "mov byte [rax+rbx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_int_to_base ----------------------------------------------
        # rax = n (signed int), rbx = base (16, 8, or 2), rcx = prefix string
        # ptr (e.g. "0x", with no sign) -> rax = "0x1a" / "-0x1a"-style string
        # (Python hex()/oct()/bin() semantics). 0 -> prefix + "0".
        digits_label, _ = self.intern_string("0123456789abcdef")
        minus_label, _ = self.intern_string("-")
        self.label("_runtime_int_to_base")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",  # n
            "mov [rbp-16], rbx",  # base
            "mov [rbp-24], rcx",  # prefix
            "xor r15, r15",  # neg flag
        )
        self.emitf("cmp qword [rbp-8], 0", "jge ._itb_nonneg", "mov r15, 1", "neg qword [rbp-8]")
        self.label("._itb_nonneg")
        # 72-byte scratch digit buffer; nul-terminator fixed at offset 71.
        self.emitf("mov rax, 72")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-32], rax", "add rax, 71", "mov byte [rax], 0", "mov rdi, rax")
        self.emitf("mov rax, [rbp-8]", "mov rbx, [rbp-16]", "test rax, rax", "jnz ._itb_loop")
        self.emitf("dec rdi", "mov byte [rdi], 48", "jmp ._itb_done")  # n == 0 -> "0"
        self.label("._itb_loop")
        self.emitf("test rax, rax", "jz ._itb_done")
        self.emitf("xor rdx, rdx", "div rbx", "dec rdi")
        self.emitf(f"lea r8, [rel {digits_label}]", "mov dl, [r8+rdx]", "mov [rdi], dl", "jmp ._itb_loop")
        self.label("._itb_done")
        self.emitf("mov [rbp-40], rdi")  # digits start (nul-terminated)
        # with_prefix = concat(prefix, digits)
        self.emitf("mov rax, [rbp-24]", "mov rbx, [rbp-40]", "call _runtime_str_concat")
        self.emitf("test r15, r15", "jz ._itb_ret")
        self.emitf("mov rbx, rax", f"lea rax, [rel {minus_label}]", "call _runtime_str_concat")
        self.label("._itb_ret")
        self.emitf("leave", "ret")

        # ---- _runtime_int_to_binary ---------------------------------------------
        # rax = n (signed int), rbx = min total width (0 = none), rcx = 1 to
        # prepend "0b" else 0 -> rax = binary string for f-string `b`/`#b`
        # format specs, e.g. f"{42:b}" -> "101010", f"{42:#010b}" ->
        # "0b00101010", f"{-5:08b}" -> "-0000101". Zero-padding (from `rbx`)
        # is applied to the digits only, after accounting for the sign and
        # "0b" prefix (matching CPython's width semantics).
        zerob_label, _ = self.intern_string("0b")
        self.label("_runtime_int_to_binary")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "mov [rbp-8], rax",  # n
            "mov [rbp-16], rbx",  # width
            "mov [rbp-24], rcx",  # prefix flag
            "xor r15, r15",  # neg flag
        )
        self.emitf("cmp qword [rbp-8], 0", "jge ._itbin_nonneg", "mov r15, 1", "neg qword [rbp-8]")
        self.label("._itbin_nonneg")
        # 72-byte scratch digit buffer; nul-terminator fixed at offset 71.
        self.emitf("mov rax, 72")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-32], rax", "add rax, 71", "mov byte [rax], 0", "mov rdi, rax")
        # avail = max(0, width - (neg ? 1 : 0) - (prefix ? 2 : 0)): minimum
        # number of digits to emit (zero-padding the rest).
        self.emitf("mov rax, [rbp-16]", "sub rax, r15")
        self.emitf("mov rcx, [rbp-24]", "add rcx, rcx", "sub rax, rcx")
        self.emitf("test rax, rax", "jge ._itbin_avail_ok", "xor rax, rax")
        self.label("._itbin_avail_ok")
        self.emitf("mov [rbp-40], rax")  # avail (remaining min-digit count)
        self.emitf("mov rax, [rbp-8]", "test rax, rax", "jnz ._itbin_loop")
        self.emitf("dec rdi", "mov byte [rdi], 48", "dec qword [rbp-40]", "jmp ._itbin_pad")
        self.label("._itbin_loop")
        self.emitf("test rax, rax", "jz ._itbin_pad")
        self.emitf("mov rdx, rax", "and rdx, 1", "shr rax, 1", "add dl, 48")
        self.emitf("dec rdi", "mov [rdi], dl", "dec qword [rbp-40]", "jmp ._itbin_loop")
        self.label("._itbin_pad")
        self.emitf("cmp qword [rbp-40], 0", "jle ._itbin_digits_done")
        self.label("._itbin_pad_loop")
        self.emitf("dec rdi", "mov byte [rdi], 48", "dec qword [rbp-40]")
        self.emitf("cmp qword [rbp-40], 0", "jg ._itbin_pad_loop")
        self.label("._itbin_digits_done")
        self.emitf("mov [rbp-48], rdi")  # digits start (nul-terminated)
        self.emitf("cmp qword [rbp-24], 0", "je ._itbin_no_prefix")
        self.emitf(f"lea rax, [rel {zerob_label}]", "mov rbx, [rbp-48]", "call _runtime_str_concat", "jmp ._itbin_have_body")
        self.label("._itbin_no_prefix")
        self.emitf("mov rax, [rbp-48]")
        self.label("._itbin_have_body")
        self.emitf("test r15, r15", "jz ._itbin_ret")
        self.emitf("mov rbx, rax", f"lea rax, [rel {minus_label}]", "call _runtime_str_concat")
        self.label("._itbin_ret")
        self.emitf("leave", "ret")

        # ---- _runtime_group_digits -----------------------------------------------
        # rax = numeric string ptr, rbx = separator byte (',' or '_') -> rax =
        # newly-allocated string with `sep` inserted every 3 digits in the
        # integer part (PEP 378/515 thousands separators), e.g.
        # "1234567" -> "1,234,567", "-1234567.89" -> "-1,234,567.89". An
        # optional leading '-' is preserved as-is; everything from the first
        # non-digit char onward (a '.' and fraction digits, for floats) is
        # copied verbatim after the grouped integer part.
        self.label("_runtime_group_digits")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")  # L
        # sign_len = (src[0] == '-') ? 1 : 0
        self.emitf("mov rsi, [rbp-8]", "xor rcx, rcx", "cmp byte [rsi], 45", "jne ._gd_no_sign", "mov rcx, 1")
        self.label("._gd_no_sign")
        self.emitf("mov [rbp-32], rcx")  # sign_len
        # intpart_len = count of ASCII-digit chars starting at sign_len
        self.emitf("mov rdx, rcx", "xor r8, r8")
        self.label("._gd_scan_loop")
        self.emitf("mov al, [rsi+rdx]", "cmp al, 48", "jl ._gd_scan_done", "cmp al, 57", "jg ._gd_scan_done")
        self.emitf("inc r8", "inc rdx", "jmp ._gd_scan_loop")
        self.label("._gd_scan_done")
        self.emitf("mov [rbp-40], r8")  # intpart_len
        # num_seps = (intpart_len - 1) // 3, or 0 if intpart_len == 0
        self.emitf("mov rax, r8", "test rax, rax", "jz ._gd_have_seps")
        self.emitf("dec rax", "mov rcx, 3", "xor rdx, rdx", "div rcx")
        self.label("._gd_have_seps")
        self.emitf("mov [rbp-48], rax")  # num_seps
        # first_group_len = intpart_len - num_seps*3
        self.emitf("mov rcx, rax", "imul rcx, 3", "mov rdx, [rbp-40]", "sub rdx, rcx")
        self.emitf("mov [rbp-56], rdx")  # first_group_len
        # allocate L + num_seps + 1 bytes
        self.emitf("mov rax, [rbp-24]", "add rax, [rbp-48]", "add rax, 1")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-64], rax")  # dst
        # copy sign (if any); init src/dst indices
        self.emitf("xor rcx, rcx", "xor rdx, rdx", "cmp qword [rbp-32], 0", "je ._gd_no_copy_sign")
        self.emitf("mov rsi, [rbp-8]", "mov rdi, [rbp-64]", "mov al, [rsi]", "mov [rdi], al", "mov rcx, 1", "mov rdx, 1")
        self.label("._gd_no_copy_sign")
        self.emitf("mov [rbp-72], rcx", "mov [rbp-80], rdx")  # src idx, dst idx
        # write first_group_len digits
        self.emitf("mov r9, [rbp-56]")
        self.label("._gd_first_group_loop")
        self.emitf("test r9, r9", "jz ._gd_groups_init")
        self.emitf("mov rsi, [rbp-8]", "mov rdi, [rbp-64]", "mov rcx, [rbp-72]", "mov rdx, [rbp-80]")
        self.emitf("mov al, [rsi+rcx]", "mov [rdi+rdx], al", "inc rcx", "inc rdx")
        self.emitf("mov [rbp-72], rcx", "mov [rbp-80], rdx", "dec r9", "jmp ._gd_first_group_loop")
        self.label("._gd_groups_init")
        self.emitf("mov r10, [rbp-48]")
        self.label("._gd_groups_loop")
        self.emitf("test r10, r10", "jz ._gd_copy_rest")
        # write separator
        self.emitf("mov rdi, [rbp-64]", "mov rdx, [rbp-80]", "mov al, [rbp-16]", "mov [rdi+rdx], al", "inc rdx", "mov [rbp-80], rdx")
        # write next 3 digits
        self.emitf("mov r9, 3")
        self.label("._gd_group3_loop")
        self.emitf("test r9, r9", "jz ._gd_group3_done")
        self.emitf("mov rsi, [rbp-8]", "mov rdi, [rbp-64]", "mov rcx, [rbp-72]", "mov rdx, [rbp-80]")
        self.emitf("mov al, [rsi+rcx]", "mov [rdi+rdx], al", "inc rcx", "inc rdx")
        self.emitf("mov [rbp-72], rcx", "mov [rbp-80], rdx", "dec r9", "jmp ._gd_group3_loop")
        self.label("._gd_group3_done")
        self.emitf("dec r10", "jmp ._gd_groups_loop")
        # copy remaining chars (decimal point + fraction, if any) + nul
        self.label("._gd_copy_rest")
        self.emitf("mov rsi, [rbp-8]", "mov rdi, [rbp-64]", "mov rcx, [rbp-72]", "mov rdx, [rbp-80]")
        self.emitf("mov al, [rsi+rcx]", "mov [rdi+rdx], al", "test al, al", "jz ._gd_copy_done")
        self.emitf("inc rcx", "inc rdx", "mov [rbp-72], rcx", "mov [rbp-80], rdx", "jmp ._gd_copy_rest")
        self.label("._gd_copy_done")
        self.emitf("mov rax, [rbp-64]", "leave", "ret")

        # ---- _runtime_group_digits_zeropad ----------------------------------------
        # rax = numeric string ptr (e.g. "1234567" or "-1234567.89"), rbx =
        # target total width, rcx = separator byte -> rax = newly-allocated
        # string: the integer part is zero-padded (on the left) to the
        # smallest digit count `ndigits` such that the *grouped* result
        # reaches at least `width` chars total (matching CPython's
        # zero-pad+grouping combo, e.g. f"{n:015,}" -> "000,001,234,567"),
        # then grouped via _runtime_group_digits. An optional leading '-' and
        # any fractional part (for floats) are preserved/counted but not
        # padded.
        self.label("_runtime_group_digits_zeropad")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx", "mov [rbp-24], rcx")
        # sign_len = (src[0] == '-') ? 1 : 0
        self.emitf(
            "mov rsi, [rbp-8]",
            "xor rdx, rdx",
            "cmp byte [rsi], 45",
            "jne ._gdz_no_sign",
            "mov rdx, 1",
        )
        self.label("._gdz_no_sign")
        self.emitf("mov [rbp-32], rdx")  # sign_len
        # intpart_len = count of ASCII-digit chars starting at sign_len
        self.emitf("mov rcx, rdx", "xor r9, r9")
        self.label("._gdz_scan")
        self.emitf(
            "mov al, [rsi+rcx]",
            "cmp al, 48", "jl ._gdz_scan_done",
            "cmp al, 57", "jg ._gdz_scan_done",
            "inc r9", "inc rcx", "jmp ._gdz_scan",
        )
        self.label("._gdz_scan_done")
        self.emitf("mov [rbp-40], r9")  # intpart_len
        # frac_len = strlen(src) - sign_len - intpart_len
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf(
            "mov rcx, [rbp-32]", "add rcx, [rbp-40]",
            "sub rax, rcx",
            "mov [rbp-48], rax",  # frac_len
        )
        # ndigits = intpart_len; while sign_len+ndigits+(ndigits-1)//3+frac_len
        # < width: ndigits += 1  (ndigits >= 1 always, so ndigits-1 >= 0)
        self.emitf("mov r10, [rbp-40]")
        self.label("._gdz_loop")
        self.emitf(
            "mov rax, r10", "dec rax",
            "mov rcx, 3", "xor rdx, rdx", "div rcx",
            "add rax, r10",
            "add rax, [rbp-32]",
            "add rax, [rbp-48]",
            "cmp rax, [rbp-16]",
            "jge ._gdz_loop_done",
            "inc r10", "jmp ._gdz_loop",
        )
        self.label("._gdz_loop_done")
        # pad_count = ndigits - intpart_len
        self.emitf("mov rax, r10", "sub rax, [rbp-40]", "mov [rbp-64], rax")
        # allocate sign_len + ndigits + frac_len + 1 bytes
        self.emitf(
            "mov rax, [rbp-32]", "add rax, r10", "add rax, [rbp-48]", "add rax, 1",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-72], rax")  # dst
        # write sign (if any); rcx = dst write index
        self.emitf("xor rcx, rcx", "cmp qword [rbp-32], 0", "je ._gdz_no_sign2")
        self.emitf(
            "mov rsi, [rbp-8]", "mov rdi, [rbp-72]",
            "mov al, [rsi]", "mov [rdi], al", "mov rcx, 1",
        )
        self.label("._gdz_no_sign2")
        # write pad_count zero digits
        self.emitf("mov r9, [rbp-64]")
        self.label("._gdz_pad_loop")
        self.emitf("test r9, r9", "jz ._gdz_pad_done")
        self.emitf(
            "mov rdi, [rbp-72]", "mov byte [rdi+rcx], 48",
            "inc rcx", "dec r9", "jmp ._gdz_pad_loop",
        )
        self.label("._gdz_pad_done")
        # copy intpart digits: src[sign_len .. sign_len+intpart_len)
        self.emitf("mov r9, [rbp-40]", "mov r11, [rbp-32]")
        self.label("._gdz_int_loop")
        self.emitf("test r9, r9", "jz ._gdz_int_done")
        self.emitf(
            "mov rsi, [rbp-8]", "mov rdi, [rbp-72]",
            "mov al, [rsi+r11]", "mov [rdi+rcx], al",
            "inc r11", "inc rcx", "dec r9", "jmp ._gdz_int_loop",
        )
        self.label("._gdz_int_done")
        # copy remaining chars (fraction, if any) + NUL terminator
        self.label("._gdz_frac_loop")
        self.emitf(
            "mov rsi, [rbp-8]", "mov rdi, [rbp-72]",
            "mov al, [rsi+r11]", "mov [rdi+rcx], al",
            "test al, al", "jz ._gdz_frac_done",
            "inc r11", "inc rcx", "jmp ._gdz_frac_loop",
        )
        self.label("._gdz_frac_done")
        # group the zero-padded digit string
        self.emitf("mov rax, [rbp-72]", "mov rbx, [rbp-24]", "call _runtime_group_digits")
        self.emitf("leave", "ret")

        # ---- _runtime_divmod ---------------------------------------------------
        # rax = a, rbx = b (signed ints) -> rax = 2-tuple (q, r) in the list
        # [cap,len,buf] layout, where q = a // b and r = a % b using Python's
        # floor semantics (mirrors the adjustment in _emit_binop_inline).
        self.label("_runtime_divmod")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf(
            "test rbx, rbx",
            "jnz ._dm_nonzero",
            "lea rax, [rel _runtime_zerodiv_msg]",
            f"mov rbx, {self._exc_type_id('ZeroDivisionError')}",
            "call _runtime_raise",
        )
        self.label("._dm_nonzero")
        self.emitf("cqo", "idiv rbx")
        self.emitf(
            "test rdx, rdx",
            "jz ._dm_done",
            "mov rcx, rdx",
            "xor rcx, rbx",
            "test rcx, rcx",
            "jns ._dm_done",
            "dec rax",
            "add rdx, rbx",
        )
        self.label("._dm_done")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rdx", "mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",
            f"mov qword [rax+{self.LIST_CAP_OFF}], 2",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 2",
            "mov rax, 16",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rcx, [rbp-24]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov rdx, [rbp-8]",
            "mov [rax], rdx",
            "mov rdx, [rbp-16]",
            "mov [rax+8], rdx",
            "mov rax, [rbp-24]",
            "leave",
            "ret",
        )

        # ---- _runtime_str_repeat ---------------------------------------------
        # rax = a (str ptr), rbx = n (int count) -> rax = newly allocated string.
        # Negative or zero n returns an empty string.
        self.label("_runtime_str_repeat")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        # [rbp-8] = a, [rbp-16] = n, [rbp-24] = la, [rbp-32] = new ptr, [rbp-40] = i
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        # If n <= 0, return empty string.
        self.emitf(
            "mov rax, [rbp-16]", "test rax, rax", "jg ._sr_compute_len", "mov rax, 1"
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov byte [rax], 0", "leave", "ret")
        self.label("._sr_compute_len")
        # la = strlen(a)
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")
        # total = la * n + 1
        self.emitf("mov rax, [rbp-24]", "mov rbx, [rbp-16]", "imul rax, rbx", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-32], rax", "mov qword [rbp-40], 0")
        self.label("._sr_loop")
        self.emitf("mov rax, [rbp-40]", "cmp rax, [rbp-16]", "jge ._sr_done")
        # dst = new + i * la
        self.emitf(
            "mov rax, [rbp-32]",
            "mov rcx, [rbp-40]",
            "imul rcx, [rbp-24]",
            "add rax, rcx",
            "mov rbx, [rbp-8]",
            "mov rcx, [rbp-24]",
        )
        self._emit_libc_memcpy()
        self.emitf("inc qword [rbp-40]", "jmp ._sr_loop")
        self.label("._sr_done")
        # nul-terminate at new + n*la
        self.emitf(
            "mov rax, [rbp-32]",
            "mov rcx, [rbp-16]",
            "imul rcx, [rbp-24]",
            "mov byte [rax+rcx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_str_eq -------------------------------------------------
        # rax = a, rbx = b -> rax = 1 if strcmp(a,b)==0 else 0.
        # NULL-safe: None lowers to the 0 slot value, and `x == "lit"` where x
        # is None is ordinary Python (False, not a crash). Both NULL compares
        # equal (None == None); exactly one NULL compares unequal.
        self.label("_runtime_str_eq")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self.emitf("test rax, rax", "jnz ._se_a_ok")
        # a == NULL: equal iff b is NULL too.
        self.emitf("test rbx, rbx", "sete al", "movzx rax, al", "leave", "ret")
        self.label("._se_a_ok")
        self.emitf("test rbx, rbx", "jnz ._se_b_ok")
        self.emitf("xor rax, rax", "leave", "ret")
        self.label("._se_b_ok")
        self._emit_libc_strcmp()
        self.emitf("test rax, rax", "sete al", "movzx rax, al", "leave", "ret")

        # ---- _runtime_str_cmp ------------------------------------------------
        # rax = a, rbx = b -> rax = -1/0/+1 (signed compare result).
        self.label("_runtime_str_cmp")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self._emit_libc_strcmp()
        # Normalize result to -1/0/+1.
        self.emitf(
            "test rax, rax",
            "jz ._sc_zero",
            "js ._sc_neg",
            "mov rax, 1",
            "jmp ._sc_done",
        )
        self.label("._sc_zero")
        self.emitf("xor rax, rax", "jmp ._sc_done")
        self.label("._sc_neg")
        self.emitf("mov rax, -1")
        self.label("._sc_done")
        self.emitf("leave", "ret")

        # ---- _runtime_str_char_at --------------------------------------------
        # rax = s, rbx = index -> rax = newly-allocated 1-char string.
        # Negative indices count from the end. Out-of-range raises (panic).
        self.label("_runtime_str_char_at")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        # len = strlen(s)
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")
        # Handle negative index.
        self.emitf(
            "mov rax, [rbp-16]",
            "test rax, rax",
            "jns ._sca_check",
            "add rax, [rbp-24]",
            "mov [rbp-16], rax",
        )
        self.label("._sca_check")
        self.emitf(
            "mov rax, [rbp-16]",
            "test rax, rax",
            "js ._sca_oob",
            "cmp rax, [rbp-24]",
            "jge ._sca_oob",
        )
        # Allocate 2-byte buffer.
        self.emitf("mov rax, 2")
        self._emit_libc_malloc_size_in_rax()
        # buf[0] = s[idx]; buf[1] = 0
        self.emitf(
            "mov rbx, [rbp-8]",
            "mov rcx, [rbp-16]",
            "mov dl, [rbx+rcx]",
            "mov [rax], dl",
            "mov byte [rax+1], 0",
            "leave",
            "ret",
        )
        self.label("._sca_oob")
        self.emitf(
            "lea rax, [rel _runtime_str_oob_msg]",
            f"mov rbx, {self._exc_type_id('IndexError')}",
            "call _runtime_raise",
        )
        self.emitf("leave", "ret")  # unreachable

        # ---- _runtime_str_slice ----------------------------------------------
        # rax = s, rbx = start, rcx = stop -> rax = newly-allocated substring.
        # Python semantics: negative indices count from end, clamped to [0, len].
        # Stop < start returns empty.
        self.label("_runtime_str_slice")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf(
            "mov [rbp-8], rax",
            "mov [rbp-16], rbx",  # start
            "mov [rbp-24], rcx",
        )  # stop
        # len = strlen(s)
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")
        # Normalize start: if negative, add len. Clamp to [0, len].
        self.emitf(
            "mov rax, [rbp-16]",
            "test rax, rax",
            "jns ._sl_start_pos",
            "add rax, [rbp-32]",
        )
        self.label("._sl_start_pos")
        self.emitf("test rax, rax", "jns ._sl_start_ok", "xor rax, rax")
        self.label("._sl_start_ok")
        self.emitf("cmp rax, [rbp-32]", "jle ._sl_start_done", "mov rax, [rbp-32]")
        self.label("._sl_start_done")
        self.emitf("mov [rbp-16], rax")
        # Normalize stop the same way.
        self.emitf(
            "mov rax, [rbp-24]",
            "test rax, rax",
            "jns ._sl_stop_pos",
            "add rax, [rbp-32]",
        )
        self.label("._sl_stop_pos")
        self.emitf("test rax, rax", "jns ._sl_stop_ok", "xor rax, rax")
        self.label("._sl_stop_ok")
        self.emitf("cmp rax, [rbp-32]", "jle ._sl_stop_done", "mov rax, [rbp-32]")
        self.label("._sl_stop_done")
        self.emitf("mov [rbp-24], rax")
        # n = stop - start. If <= 0, return empty.
        self.emitf("mov rax, [rbp-24]", "sub rax, [rbp-16]")
        self.emitf("test rax, rax", "jg ._sl_alloc", "mov rax, 1")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov byte [rax], 0", "leave", "ret")
        self.label("._sl_alloc")
        # Save n in [rbp-40], malloc(n+1).
        self.emitf("mov [rbp-40], rax", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-48], rax")
        # memcpy(new, s+start, n)
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rbx, [rbp-8]",
            "add rbx, [rbp-16]",
            "mov rcx, [rbp-40]",
        )
        self._emit_libc_memcpy()
        # NUL-terminate.
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rcx, [rbp-40]",
            "mov byte [rax+rcx], 0",
            "leave",
            "ret",
        )

        self._emit_str_slice_step_helper()

        # ---- _runtime_str_contains -------------------------------------------
        # rax = haystack, rbx = needle -> rax = 1 if needle is a substring.
        # Uses libc strstr.
        self.label("_runtime_str_contains")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self._emit_libc_strstr()
        self.emitf("test rax, rax", "setne al", "movzx rax, al", "leave", "ret")

        # ---- _runtime_str_index_of -------------------------------------------
        # rax = haystack, rbx = needle -> rax = index (or -1 if not found).
        self.label("_runtime_str_index_of")
        self.emitf(
            "push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax"
        )  # save haystack
        self._emit_libc_strstr()
        # rax = pointer in haystack or NULL.
        self.emitf(
            "test rax, rax", "jz ._sio_notfound", "sub rax, [rbp-8]", "leave", "ret"
        )
        self.label("._sio_notfound")
        self.emitf("mov rax, -1", "leave", "ret")

        # ---- _runtime_str_index_of_start ------------------------------------
        # rax = haystack, rbx = needle, rcx = start_pos -> rax = index or -1.
        # Advances haystack by start_pos bytes before calling strstr, then adds
        # start_pos back to the returned offset so the result is absolute.
        self.label("_runtime_str_index_of_start")
        self.emitf(
            "push rbp", "mov rbp, rsp", "sub rsp, 48",
            "mov [rbp-8], rax",   # save original haystack base
            "mov [rbp-16], rcx",  # save start_pos
            "add rax, rcx",       # rax = haystack + start_pos
        )
        self._emit_libc_strstr()
        self.emitf(
            "test rax, rax", f"jz ._siost_notfound",
            "sub rax, [rbp-8]",  # absolute index from base
            "leave", "ret",
        )
        self.label("._siost_notfound")
        self.emitf("mov rax, -1", "leave", "ret")

        # ---- _runtime_str_rindex_of -----------------------------------------
        # rax = haystack, rbx = needle -> rax = last index or -1.
        # Scans forward keeping track of the latest match found.
        self.label("_runtime_str_rindex_of")
        self.emitf(
            "push rbp", "mov rbp, rsp", "sub rsp, 64",
            "mov [rbp-8], rax",   # cursor
            "mov [rbp-16], rbx",  # needle
            "mov qword [rbp-24], -1",  # best = -1
            "mov [rbp-32], rax",  # base
        )
        # nlen = strlen(needle); if 0, return -1
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-40], rax", "test rax, rax", "jz ._srif_done")
        self.label("._srif_loop")
        self.emitf("mov rax, [rbp-8]", "mov rbx, [rbp-16]")
        self._emit_libc_strstr()
        self.emitf(
            "test rax, rax", "jz ._srif_done",
            "mov rbx, rax",
            "sub rbx, [rbp-32]",
            "mov [rbp-24], rbx",  # best = current index
            "mov rbx, [rbp-40]",
            "add rax, rbx",       # advance cursor past this match
            "mov [rbp-8], rax",
            "jmp ._srif_loop",
        )
        self.label("._srif_done")
        self.emitf("mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_expandtabs ----------------------------------------
        # rax = str, rbx = tabsize -> rax = new str with tabs expanded.
        # Scans source; copies non-tab chars; for each \t emits spaces to align
        # to next tabstop (col rounded up to next multiple of tabsize).
        self.label("_runtime_str_expandtabs")
        self.emitf(
            "push rbp", "mov rbp, rsp", "sub rsp, 80",
            "push rdi", "push rsi", "push r12", "push r13", "push r14",
            "mov [rbp-8], rax",   # src ptr
            "mov [rbp-16], rbx",  # tabsize
        )
        # Compute output length: walk src, count spaces needed for tabs
        self.emitf(
            "mov rdi, rax",       # rdi = src cursor
            "xor r12, r12",       # r12 = output length
            "xor r13, r13",       # r13 = current col
        )
        etab_slen_loop = self.fresh("etab_slen")
        etab_slen_end = self.fresh("etab_slen_end")
        etab_slen_tab = self.fresh("etab_slen_tab")
        self.label(etab_slen_loop)
        self.emitf(
            "movzx rax, byte [rdi]",
            "test al, al", f"jz {etab_slen_end}",
            "cmp al, 9",
            f"je {etab_slen_tab}",
            # normal char
            "inc r12", "inc r13", "inc rdi",
            f"jmp {etab_slen_loop}",
        )
        self.label(etab_slen_tab)
        # spaces = tabsize - (col % tabsize); if tabsize==0, drop the tab
        etab_slen_skip = self.fresh("etab_slen_skip")
        self.emitf(
            "mov rax, [rbp-16]",
            "test rax, rax",
            f"jz {etab_slen_skip}",  # tabsize==0: drop tab, continue
        )
        self.emitf(
            "mov rdx, 0", "mov rax, r13", "div qword [rbp-16]",
            # rdx = col % tabsize; spaces = tabsize - rdx
            "mov rax, [rbp-16]", "sub rax, rdx",
            "add r12, rax",   # output_len += spaces
            "add r13, rax",   # col += spaces
        )
        self.label(etab_slen_skip)
        self.emitf("inc rdi", f"jmp {etab_slen_loop}")
        self.label(etab_slen_end)
        # malloc(output_len + 1): size in rax for _emit_libc_malloc_size_in_rax
        self.emitf("lea rax, [r12+1]")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",  # out buffer
            "mov rdi, [rbp-8]",   # reset src cursor
            "mov r14, rax",       # out cursor
            "xor r13, r13",       # col = 0
        )
        etab_copy_loop = self.fresh("etab_copy")
        etab_copy_end = self.fresh("etab_copy_end")
        etab_copy_tab = self.fresh("etab_copy_tab")
        etab_copy_sp = self.fresh("etab_copy_sp")
        self.label(etab_copy_loop)
        self.emitf(
            "movzx rax, byte [rdi]",
            "test al, al", f"jz {etab_copy_end}",
            "cmp al, 9", f"je {etab_copy_tab}",
            "mov byte [r14], al", "inc r14", "inc r13", "inc rdi",
            f"jmp {etab_copy_loop}",
        )
        self.label(etab_copy_tab)
        # emit spaces to fill to next tabstop; if tabsize==0, drop the tab
        etab_copy_skip = self.fresh("etab_copy_skip")
        self.emitf(
            "inc rdi",  # advance past \t
            "mov rax, [rbp-16]", "test rax, rax", f"jz {etab_copy_loop}",
            "mov rdx, 0", "mov rax, r13", "div qword [rbp-16]",
            "mov rax, [rbp-16]", "sub rax, rdx",  # rax = spaces needed
            "mov [rbp-32], rax",  # save count
        )
        self.label(etab_copy_sp)
        self.emitf(
            "cmp qword [rbp-32], 0", f"jle {etab_copy_loop}",
            "mov byte [r14], 0x20", "inc r14", "inc r13",
            "dec qword [rbp-32]",
            f"jmp {etab_copy_sp}",
        )
        self.label(etab_copy_end)
        self.emitf(
            "mov byte [r14], 0",  # NUL-terminate
            "pop r14", "pop r13", "pop r12", "pop rsi", "pop rdi",
            "mov rax, [rbp-24]",
            "leave", "ret",
        )

        # ---- _runtime_str_count ----------------------------------------------
        # rax = haystack, rbx = needle -> rax = non-overlapping occurrence count.
        # Empty needle returns 0 (CPython would return len+1; we simplify).
        # NOTE: locals span [rbp-8..rbp-32]. The MS x64 ABI demands 32 bytes of
        # shadow space below rsp for every call; if we only `sub rsp, 48` then
        # strstr's shadow overwrites our nlen at [rbp-32], the loop advances
        # by garbage, and `.count()` hangs. Reserve 64 so shadow ends at
        # [rbp-33] and never touches the locals.
        self.label("_runtime_str_count")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 64",
            "mov [rbp-8], rax",  # haystack cursor
            "mov [rbp-16], rbx",  # needle
            "xor rcx, rcx",
            "mov [rbp-24], rcx",
        )  # count = 0
        # nlen = strlen(needle); if 0 -> return 0
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax", "test rax, rax", "jz ._sco_done")
        self.label("._sco_loop")
        self.emitf("mov rax, [rbp-8]", "mov rbx, [rbp-16]")
        self._emit_libc_strstr()
        self.emitf(
            "test rax, rax",
            "jz ._sco_done",
            "mov rcx, [rbp-24]",
            "inc rcx",
            "mov [rbp-24], rcx",
            "add rax, [rbp-32]",
            "mov [rbp-8], rax",
            "jmp ._sco_loop",
        )
        self.label("._sco_done")
        self.emitf("mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_starts_with ----------------------------------------
        # rax = s, rbx = prefix -> rax = 1 if memcmp(s, prefix, plen) == 0
        # and len(s) >= plen, else 0.
        self.label("_runtime_str_starts_with")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 48",
            "mov [rbp-8], rax",  # s
            "mov [rbp-16], rbx",
        )  # prefix
        # plen = strlen(prefix)
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")
        # slen = strlen(s)
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("cmp rax, [rbp-24]", "jl ._ssw_no")
        # Compare bytes via a byte-loop (avoids needing memcmp in libc list).
        self.emitf(
            "mov rax, [rbp-8]", "mov rbx, [rbp-16]", "mov rcx, [rbp-24]", "xor rdx, rdx"
        )
        self.label("._ssw_loop")
        self.emitf(
            "test rcx, rcx",
            "jz ._ssw_yes",
            "mov dl, [rax]",
            "cmp dl, [rbx]",
            "jne ._ssw_no",
            "inc rax",
            "inc rbx",
            "dec rcx",
            "jmp ._ssw_loop",
        )
        self.label("._ssw_yes")
        self.emitf("mov rax, 1", "leave", "ret")
        self.label("._ssw_no")
        self.emitf("xor rax, rax", "leave", "ret")

        # ---- _runtime_str_ends_with ------------------------------------------
        # rax = s, rbx = suffix -> rax = 1 if s ends with suffix else 0.
        self.label("_runtime_str_ends_with")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 48",
            "mov [rbp-8], rax",
            "mov [rbp-16], rbx",
        )
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")  # suflen
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf(
            "mov [rbp-32], rax",  # slen
            "cmp rax, [rbp-24]",
            "jl ._sew_no",
        )
        # offset = slen - suflen
        self.emitf(
            "mov rax, [rbp-32]",
            "sub rax, [rbp-24]",
            "add rax, [rbp-8]",  # s + offset
            "mov rbx, [rbp-16]",
            "mov rcx, [rbp-24]",
            "xor rdx, rdx",
        )
        self.label("._sew_loop")
        self.emitf(
            "test rcx, rcx",
            "jz ._sew_yes",
            "mov dl, [rax]",
            "cmp dl, [rbx]",
            "jne ._sew_no",
            "inc rax",
            "inc rbx",
            "dec rcx",
            "jmp ._sew_loop",
        )
        self.label("._sew_yes")
        self.emitf("mov rax, 1", "leave", "ret")
        self.label("._sew_no")
        self.emitf("xor rax, rax", "leave", "ret")

        # ---- _runtime_str_removeprefix -----------------------------------------
        # rax = s, rbx = prefix -> rax = s[len(prefix):] if s.startswith(prefix)
        # else a copy of s.
        self.label("_runtime_str_removeprefix")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf("call _runtime_str_starts_with", "test rax, rax", "jz ._srmp_no")
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")  # plen
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("mov rcx, rax", "mov rbx, [rbp-24]", "mov rax, [rbp-8]", "call _runtime_str_slice", "leave", "ret")
        self.label("._srmp_no")
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf("leave", "ret")

        # ---- _runtime_str_removesuffix -----------------------------------------
        # rax = s, rbx = suffix -> rax = s[:len(s)-len(suffix)] if
        # s.endswith(suffix) else a copy of s.
        self.label("_runtime_str_removesuffix")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf("call _runtime_str_ends_with", "test rax, rax", "jz ._srms_no")
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")  # suflen
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("sub rax, [rbp-24]", "mov rcx, rax", "xor rbx, rbx", "mov rax, [rbp-8]", "call _runtime_str_slice", "leave", "ret")
        self.label("._srms_no")
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf("leave", "ret")

        # ---- _runtime_str_upper ----------------------------------------------
        # rax = s -> rax = newly-allocated upper-case copy. ASCII only.
        # Locals [rbp-8..rbp-24] = 24 bytes + 32 shadow = 56, round to 64
        # (16-aligned). Previously `sub rsp, 48` reserved only 24+32-8=48 -
        # 8 bytes short of the real shadow-space requirement, so strlen/
        # malloc's own shadow-space writes (always [rsp..rsp+31] relative
        # to the call site, per Win64 ABI) clobbered this function's own
        # `dst` local at [rbp-24], corrupting it before the copy loop ever
        # read it back. The corrupted pointer was then used as a malloc'd
        # buffer, which corrupts the heap allocator's own bookkeeping -
        # the actual segfault this surfaces as happens much later, inside
        # an unrelated subsequent malloc() call once the heap metadata
        # itself is damaged, not at this function's own return.
        self.label("_runtime_str_upper")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64", "mov [rbp-8], rax")
        self._emit_libc_strlen()
        self.emitf(
            "mov [rbp-16], rax",  # len
            "inc rax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-24], rax")  # dst
        # Copy + transform.
        self.emitf(
            "mov rcx, [rbp-16]", "mov rsi, [rbp-8]", "mov rdi, [rbp-24]", "xor rdx, rdx"
        )
        self.label("._sup_loop")
        self.emitf(
            "test rcx, rcx",
            "jz ._sup_done",
            "mov dl, [rsi]",
            "cmp dl, 97",  # 'a'
            "jl ._sup_keep",
            "cmp dl, 122",  # 'z'
            "jg ._sup_keep",
            "sub dl, 32",
        )
        self.label("._sup_keep")
        self.emitf("mov [rdi], dl", "inc rsi", "inc rdi", "dec rcx", "jmp ._sup_loop")
        self.label("._sup_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_lower ----------------------------------------------
        # rax = s -> rax = newly-allocated lower-case copy. ASCII only.
        # Same shadow-space fix as _runtime_str_upper just above (24 bytes
        # of locals + 32 shadow = 56, round to 64) - this function has the
        # identical structure and had the identical 8-byte-short bug.
        self.label("_runtime_str_lower")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64", "mov [rbp-8], rax")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-16], rax", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",
            "mov rcx, [rbp-16]",
            "mov rsi, [rbp-8]",
            "mov rdi, [rbp-24]",
            "xor rdx, rdx",
        )
        self.label("._slo_loop")
        self.emitf(
            "test rcx, rcx",
            "jz ._slo_done",
            "mov dl, [rsi]",
            "cmp dl, 65",  # 'A'
            "jl ._slo_keep",
            "cmp dl, 90",  # 'Z'
            "jg ._slo_keep",
            "add dl, 32",
        )
        self.label("._slo_keep")
        self.emitf("mov [rdi], dl", "inc rsi", "inc rdi", "dec rcx", "jmp ._slo_loop")
        self.label("._slo_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_capitalize ------------------------------------------
        # rax = s -> rax = newly-allocated copy with the first character
        # upper-cased and every other character lower-cased. ASCII only.
        self.label("_runtime_str_capitalize")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-16], rax", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",
            "mov rcx, [rbp-16]",
            "mov rsi, [rbp-8]",
            "mov rdi, [rbp-24]",
            "xor r8, r8",
        )
        self.label("._scap_loop")
        self.emitf("test rcx, rcx", "jz ._scap_done", "mov al, [rsi]", "test r8, r8", "jnz ._scap_rest")
        # Index 0: lower-case letter -> upper-case it.
        self.emitf("cmp al, 97", "jl ._scap_store", "cmp al, 122", "jg ._scap_store", "sub al, 32", "jmp ._scap_store")
        self.label("._scap_rest")
        # Index > 0: upper-case letter -> lower-case it.
        self.emitf("cmp al, 65", "jl ._scap_store", "cmp al, 90", "jg ._scap_store", "add al, 32")
        self.label("._scap_store")
        self.emitf("mov [rdi], al", "inc rsi", "inc rdi", "inc r8", "dec rcx", "jmp ._scap_loop")
        self.label("._scap_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_swapcase ---------------------------------------------
        # rax = s -> rax = newly-allocated copy with upper/lower case swapped.
        # ASCII only.
        self.label("_runtime_str_swapcase")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-16], rax", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",
            "mov rcx, [rbp-16]",
            "mov rsi, [rbp-8]",
            "mov rdi, [rbp-24]",
        )
        self.label("._sswap_loop")
        self.emitf("test rcx, rcx", "jz ._sswap_done", "mov al, [rsi]")
        self.emitf("cmp al, 97", "jl ._sswap_upper", "cmp al, 122", "jg ._sswap_store", "sub al, 32", "jmp ._sswap_store")
        self.label("._sswap_upper")
        self.emitf("cmp al, 65", "jl ._sswap_store", "cmp al, 90", "jg ._sswap_store", "add al, 32")
        self.label("._sswap_store")
        self.emitf("mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._sswap_loop")
        self.label("._sswap_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_title -------------------------------------------------
        # rax = s -> rax = newly-allocated copy with the first letter of each
        # run of letters upper-cased and the rest lower-cased. A run ends at
        # any non-letter byte (matches CPython's ASCII str.title()).
        self.label("_runtime_str_title")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-16], rax", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-24], rax",
            "mov rcx, [rbp-16]",
            "mov rsi, [rbp-8]",
            "mov rdi, [rbp-24]",
            "xor r9, r9",  # in_word flag
        )
        self.label("._stit_loop")
        self.emitf("test rcx, rcx", "jz ._stit_done", "mov al, [rsi]")
        self.emitf("cmp al, 97", "jl ._stit_check_upper", "cmp al, 122", "jg ._stit_notalpha")
        # Lower-case letter: start-of-word -> upper-case; mid-word -> keep.
        self.emitf("test r9, r9", "jnz ._stit_setword", "sub al, 32", "jmp ._stit_setword")
        self.label("._stit_check_upper")
        self.emitf("cmp al, 65", "jl ._stit_notalpha", "cmp al, 90", "jg ._stit_notalpha")
        # Upper-case letter: start-of-word -> keep; mid-word -> lower-case.
        self.emitf("test r9, r9", "jz ._stit_setword", "add al, 32", "jmp ._stit_setword")
        self.label("._stit_notalpha")
        self.emitf("xor r9, r9", "jmp ._stit_store")
        self.label("._stit_setword")
        self.emitf("mov r9, 1")
        self.label("._stit_store")
        self.emitf("mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._stit_loop")
        self.label("._stit_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-24]", "leave", "ret")

        # ---- _runtime_str_lstrip ---------------------------------------------
        # rax = s -> rax = newly-allocated copy with leading ASCII whitespace
        # (space, tab, newline, carriage-return) removed.
        self.label("_runtime_str_lstrip")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
        # Advance start past whitespace.
        self.emitf("mov rsi, [rbp-8]")
        self.label("._slst_skip")
        self.emitf(
            "mov dl, [rsi]",
            "cmp dl, 32",
            "je ._slst_adv",
            "cmp dl, 9",
            "je ._slst_adv",
            "cmp dl, 10",
            "je ._slst_adv",
            "cmp dl, 13",
            "je ._slst_adv",
            "jmp ._slst_copy",
        )
        self.label("._slst_adv")
        self.emitf("inc rsi", "jmp ._slst_skip")
        self.label("._slst_copy")
        self.emitf(
            "mov [rbp-16], rsi",  # start ptr
            "mov rax, rsi",
        )
        self._emit_libc_strlen()
        self.emitf(
            "mov [rbp-24], rax",  # n
            "inc rax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-32], rax", "mov rbx, [rbp-16]", "mov rcx, [rbp-24]")
        self._emit_libc_memcpy()
        self.emitf(
            "mov rax, [rbp-32]",
            "mov rcx, [rbp-24]",
            "mov byte [rax+rcx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_str_rstrip ---------------------------------------------
        # rax = s -> rax = newly-allocated copy with trailing ASCII whitespace
        # removed.
        self.label("_runtime_str_rstrip")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-16], rax")  # n
        # Walk back from the end while whitespace.
        self.label("._srst_back")
        self.emitf(
            "mov rcx, [rbp-16]",
            "test rcx, rcx",
            "jz ._srst_alloc",
            "mov rsi, [rbp-8]",
            "dec rcx",
            "mov dl, [rsi+rcx]",
            "cmp dl, 32",
            "je ._srst_dec",
            "cmp dl, 9",
            "je ._srst_dec",
            "cmp dl, 10",
            "je ._srst_dec",
            "cmp dl, 13",
            "je ._srst_dec",
            "jmp ._srst_alloc",
        )
        self.label("._srst_dec")
        self.emitf("mov [rbp-16], rcx", "jmp ._srst_back")
        self.label("._srst_alloc")
        self.emitf("mov rax, [rbp-16]", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-24], rax", "mov rbx, [rbp-8]", "mov rcx, [rbp-16]")
        self._emit_libc_memcpy()
        self.emitf(
            "mov rax, [rbp-24]",
            "mov rcx, [rbp-16]",
            "mov byte [rax+rcx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_str_strip ----------------------------------------------
        # rax = s -> lstrip(rstrip(s)). Two passes; allocates twice.
        self.label("_runtime_str_strip")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 48",
            "call _runtime_str_rstrip",
            "call _runtime_str_lstrip",
            "leave",
            "ret",
        )

        # ---- _runtime_str_zfill -----------------------------------------------
        # rax = s, rbx = width -> rax = newly-allocated copy of s left-padded
        # with '0' to at least `width` bytes. A leading '+'/'-' sign (if
        # present) stays first; zeros are inserted after it.
        self.label("_runtime_str_zfill")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax", "mov [rbp-16], rbx")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")  # len
        self.emitf(
            "mov rcx, [rbp-16]",  # width
            "cmp rcx, rax",
            "jge ._szf_tot_done",
            "mov rcx, rax",  # total = len (width < len)
        )
        self.label("._szf_tot_done")
        self.emitf("mov [rbp-32], rcx")  # total
        self.emitf("mov rax, rcx", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-40], rax")  # dst
        self.emitf(
            "mov rsi, [rbp-8]",
            "mov rdi, [rbp-40]",
            "xor r8, r8",  # sign-present flag
            "mov al, [rsi]",
            "cmp al, 43",  # '+'
            "je ._szf_sign",
            "cmp al, 45",  # '-'
            "jne ._szf_nosign",
        )
        self.label("._szf_sign")
        self.emitf("mov [rdi], al", "inc rsi", "inc rdi", "mov r8, 1")
        self.label("._szf_nosign")
        self.emitf("mov r9, [rbp-32]", "sub r9, [rbp-24]")  # pad = total - len
        self.label("._szf_padloop")
        self.emitf("test r9, r9", "jz ._szf_copyrest", "mov byte [rdi], 48", "inc rdi", "dec r9", "jmp ._szf_padloop")
        self.label("._szf_copyrest")
        self.emitf("mov rcx, [rbp-24]", "sub rcx, r8")  # remaining = len - sign
        self.label("._szf_cploop")
        self.emitf("test rcx, rcx", "jz ._szf_done", "mov al, [rsi]", "mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._szf_cploop")
        self.label("._szf_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-40]", "leave", "ret")

        # ---- _runtime_str_ljust ------------------------------------------------
        # rax = s, rbx = width, rcx = fill byte -> rax = newly-allocated copy of
        # s, right-padded with the fill byte to at least `width` bytes.
        self.label("_runtime_str_ljust")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64", "mov [rbp-8], rax", "mov [rbp-16], rbx", "mov [rbp-24], rcx")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")  # len
        self.emitf(
            "mov rcx, [rbp-16]",
            "cmp rcx, rax",
            "jge ._slj_tot_done",
            "mov rcx, rax",
        )
        self.label("._slj_tot_done")
        self.emitf("mov [rbp-40], rcx")  # total
        self.emitf("mov rax, rcx", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-48], rax")  # dst
        self.emitf("mov rsi, [rbp-8]", "mov rdi, [rbp-48]", "mov rcx, [rbp-32]")
        self.label("._slj_cp")
        self.emitf("test rcx, rcx", "jz ._slj_pad", "mov al, [rsi]", "mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._slj_cp")
        self.label("._slj_pad")
        self.emitf("mov rcx, [rbp-40]", "sub rcx, [rbp-32]", "mov al, [rbp-24]")
        self.label("._slj_padloop")
        self.emitf("test rcx, rcx", "jz ._slj_done", "mov [rdi], al", "inc rdi", "dec rcx", "jmp ._slj_padloop")
        self.label("._slj_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-48]", "leave", "ret")

        # ---- _runtime_str_rjust ------------------------------------------------
        # rax = s, rbx = width, rcx = fill byte -> rax = newly-allocated copy of
        # s, left-padded with the fill byte to at least `width` bytes.
        self.label("_runtime_str_rjust")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64", "mov [rbp-8], rax", "mov [rbp-16], rbx", "mov [rbp-24], rcx")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")  # len
        self.emitf(
            "mov rcx, [rbp-16]",
            "cmp rcx, rax",
            "jge ._srj_tot_done",
            "mov rcx, rax",
        )
        self.label("._srj_tot_done")
        self.emitf("mov [rbp-40], rcx")  # total
        self.emitf("mov rax, rcx", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-48], rax")  # dst
        self.emitf("mov rdi, [rbp-48]", "mov rcx, [rbp-40]", "sub rcx, [rbp-32]", "mov al, [rbp-24]")
        self.label("._srj_padloop")
        self.emitf("test rcx, rcx", "jz ._srj_cp", "mov [rdi], al", "inc rdi", "dec rcx", "jmp ._srj_padloop")
        self.label("._srj_cp")
        self.emitf("mov rsi, [rbp-8]", "mov rcx, [rbp-32]")
        self.label("._srj_cploop")
        self.emitf("test rcx, rcx", "jz ._srj_done", "mov al, [rsi]", "mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._srj_cploop")
        self.label("._srj_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-48]", "leave", "ret")

        # ---- _runtime_str_center -----------------------------------------------
        # rax = s, rbx = width, rcx = fill byte -> rax = newly-allocated copy of
        # s, centered within `width` bytes using the fill byte. Matches
        # CPython's split: left = marg/2 + (marg & width & 1), right = marg-left.
        self.label("_runtime_str_center")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80", "mov [rbp-8], rax", "mov [rbp-16], rbx", "mov [rbp-24], rcx")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")  # len
        self.emitf(
            "mov rcx, [rbp-16]",
            "cmp rcx, rax",
            "jge ._scn_tot_done",
            "mov rcx, rax",
        )
        self.label("._scn_tot_done")
        self.emitf("mov [rbp-40], rcx")  # total
        self.emitf("mov rax, rcx", "sub rax, [rbp-32]", "mov [rbp-48], rax")  # marg
        self.emitf(
            "mov rax, [rbp-48]",
            "shr rax, 1",
            "mov rdx, [rbp-48]",
            "and rdx, [rbp-16]",
            "and rdx, 1",
            "add rax, rdx",
            "mov [rbp-56], rax",  # left
        )
        self.emitf("mov rax, [rbp-48]", "sub rax, [rbp-56]", "mov [rbp-64], rax")  # right
        self.emitf("mov rax, [rbp-40]", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-72], rax")  # dst
        self.emitf("mov rdi, [rbp-72]", "mov al, [rbp-24]", "mov rcx, [rbp-56]")
        self.label("._scn_lpad")
        self.emitf("test rcx, rcx", "jz ._scn_cp", "mov [rdi], al", "inc rdi", "dec rcx", "jmp ._scn_lpad")
        self.label("._scn_cp")
        self.emitf("mov rsi, [rbp-8]", "mov rcx, [rbp-32]")
        self.label("._scn_cploop")
        self.emitf("test rcx, rcx", "jz ._scn_rpad", "mov al, [rsi]", "mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._scn_cploop")
        self.label("._scn_rpad")
        self.emitf("mov al, [rbp-24]", "mov rcx, [rbp-64]")
        self.label("._scn_rpadloop")
        self.emitf("test rcx, rcx", "jz ._scn_done", "mov [rdi], al", "inc rdi", "dec rcx", "jmp ._scn_rpadloop")
        self.label("._scn_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-72]", "leave", "ret")

        # ---- _runtime_str_truncate ----------------------------------------------
        # rax = s, rbx = max length n -> rax = newly-allocated copy of s,
        # truncated to min(len(s), n) bytes (for f-string `.precision` on str).
        self.label("_runtime_str_truncate")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax", "mov [rbp-16], rbx")
        self._emit_libc_strlen()
        self.emitf("mov rcx, [rbp-16]", "cmp rcx, rax", "jle ._strn_tot_done", "mov rcx, rax")
        self.label("._strn_tot_done")
        self.emitf("mov [rbp-24], rcx")  # copy_len
        self.emitf("mov rax, rcx", "inc rax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-40], rax")  # dst
        self.emitf("mov rsi, [rbp-8]", "mov rdi, [rbp-40]", "mov rcx, [rbp-24]")
        self.label("._strn_cp")
        self.emitf("test rcx, rcx", "jz ._strn_done", "mov al, [rsi]", "mov [rdi], al", "inc rsi", "inc rdi", "dec rcx", "jmp ._strn_cp")
        self.label("._strn_done")
        self.emitf("mov byte [rdi], 0", "mov rax, [rbp-40]", "leave", "ret")

        # ---- _runtime_str_replace --------------------------------------------
        # rax = s, rbx = old, rcx = new -> rax = newly-allocated copy of s with
        # every non-overlapping occurrence of `old` replaced by `new`. Empty
        # `old` returns a duplicate of s (no replacement).
        # Locals span [rbp-8..rbp-96]; reserve 128 = 96 locals + 32 shadow so
        # called functions (memcpy/strstr) can't clobber the match pointer at
        # [rbp-96] via their shadow stores.
        self.label("_runtime_str_replace")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 128",
            "mov [rbp-8], rax",  # s
            "mov [rbp-16], rbx",  # old
            "mov [rbp-24], rcx",
        )  # new
        # Lengths: slen, olen, nlen.
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-40], rax", "test rax, rax", "jz ._srep_dup")
        self.emitf("mov rax, [rbp-24]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-48], rax")
        # Count occurrences.
        self.emitf(
            "mov rax, [rbp-8]",
            "mov rbx, [rbp-16]",
            "call _runtime_str_count",
            "mov [rbp-56], rax",
        )  # cnt
        # outlen = slen + cnt * (nlen - olen)
        self.emitf(
            "mov rax, [rbp-48]",
            "sub rax, [rbp-40]",
            "imul rax, [rbp-56]",
            "add rax, [rbp-32]",
            "mov [rbp-64], rax",  # outlen
            "inc rax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-72], rax")  # out ptr
        # Walk: src cursor in r12, dst cursor in r13. We use rbp-80 / rbp-88
        # since r12/r13 are callee-saved on both ABIs; safer to keep in slots.
        self.emitf("mov rax, [rbp-8]", "mov [rbp-80], rax")  # src
        self.emitf("mov rax, [rbp-72]", "mov [rbp-88], rax")  # dst
        self.label("._srep_loop")
        # Find next occurrence at-or-after src.
        self.emitf("mov rax, [rbp-80]", "mov rbx, [rbp-16]")
        self._emit_libc_strstr()
        self.emitf("test rax, rax", "jz ._srep_tail")
        # Save match ptr; compute chunk_len = match - src; copy chunk to dst.
        self.emitf("mov [rbp-96], rax")  # match ptr
        self.emitf("sub rax, [rbp-80]")
        # Skip the memcpy if the chunk is empty (calling memcpy with size 0
        # is fine but we avoid the call overhead).
        self.emitf("test rax, rax", "jz ._srep_no_chunk")
        # memcpy(dst, src, chunk_len)
        self.emitf(
            "mov rcx, rax",  # rcx = chunk_len
            "mov rbx, [rbp-80]",  # rbx = src
            "mov rax, [rbp-88]",
        )  # rax = dst
        self._emit_libc_memcpy()
        # Advance dst by chunk_len. memcpy clobbered scratch regs, so re-derive
        # chunk_len from (match - src) using the stable slots.
        self.emitf(
            "mov rax, [rbp-96]",
            "sub rax, [rbp-80]",
            "add rax, [rbp-88]",
            "mov [rbp-88], rax",
        )
        self.label("._srep_no_chunk")
        # Append `new` to dst.
        self.emitf("mov rax, [rbp-88]", "mov rbx, [rbp-24]", "mov rcx, [rbp-48]")
        self._emit_libc_memcpy()
        # Advance dst by nlen; advance src to match + olen.
        self.emitf(
            "mov rax, [rbp-88]",
            "add rax, [rbp-48]",
            "mov [rbp-88], rax",
            "mov rax, [rbp-96]",
            "add rax, [rbp-40]",
            "mov [rbp-80], rax",
            "jmp ._srep_loop",
        )
        self.label("._srep_tail")
        # Copy remaining tail bytes from src to dst.
        self.emitf(
            "mov rax, [rbp-72]",
            "add rax, [rbp-64]",  # = out + outlen
            "sub rax, [rbp-88]",
        )  # rax = tail_len
        self.emitf("test rax, rax", "jz ._srep_term")
        self.emitf("mov rcx, rax", "mov rbx, [rbp-80]", "mov rax, [rbp-88]")
        self._emit_libc_memcpy()
        self.label("._srep_term")
        self.emitf(
            "mov rax, [rbp-72]",
            "mov rcx, [rbp-64]",
            "mov byte [rax+rcx], 0",
            "leave",
            "ret",
        )
        self.label("._srep_dup")
        # Empty old: just strdup the input.
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf("leave", "ret")

        self._emit_str_split_helper()
        self._emit_str_split_ws_helper()
        self._emit_str_join_helper()
        self._emit_str_splitlines_helper()
        self._emit_str_partition_helper()
        self._emit_str_rpartition_helper()
        self._emit_str_rsplit_helper()
        self._emit_chr_helper()
        self._emit_str_predicate_helpers()
        self._emit_list_repr_helper()

        self.emit("section .rodata")
        self.emit('_runtime_str_oob_msg: db "string index out of range",0')
        self.emit('_runtime_list_oob_msg: db "list index out of range",0')
        # CPython (3.13+) uses the same message "division by zero" for all
        # of int //, int %, float /, float //, float % and divmod().
        self.emit('_runtime_zerodiv_msg: db "division by zero",0')
        self.emit("_runtime_nl_str: db 10,0")  # "\n" for splitlines
        self.emit("_runtime_empty_str: db 0")  # "" for partition's not-found arms
        self.emit('_runtime_lbrack_str: db "[",0')
        self.emit('_runtime_rbrack_str: db "]",0')
        self.emit('_runtime_comma_str: db ", ",0')
        self.emit("_runtime_quote_str: db 39,0")  # single quote for str elements
        self.emit('_runtime_lbrace_str: db "{",0')
        self.emit('_runtime_rbrace_str: db "}",0')
        self.emit('_runtime_colon_str: db ": ",0')
        self.emit('_runtime_emptyset_str: db "set()",0')
        self.emit('_runtime_lparen_str: db "(",0')
        self.emit('_runtime_rparen_str: db ")",0')
        self.emit('_runtime_comma_rparen_str: db ",)",0')
        self.emit('_runtime_true_str: db "True",0')
        self.emit('_runtime_false_str: db "False",0')
        self.emit('_runtime_none_str: db "None",0')

    def _emit_list_repr_helper(self) -> None:
        """Container repr helpers and the shared per-element formatter.

        Emits:
          _runtime_fmt_elem  - format one value by kind -> repr string
          _runtime_list_repr - `[e0, e1, ...]` for a list/tuple
          _runtime_dict_repr - `{k0: v0, k1: v1, ...}` for a dict
          _runtime_set_repr  - `{e0, e1, ...}` for a set
          _runtime_str_concat_dup - fresh copy of a string

        All loop state lives in stack slots so the int/float/str conversion
        helpers (which clobber registers freely) can be called mid-iteration.
        """
        # ---- _runtime_fmt_elem ------------------------------------------------
        # In:  rax = value, rbx = kind. Low nibble = base kind (0 = int,
        # 1 = str-quoted, 2 = float, 3 = list, 4 = dict); for base kinds 3/4,
        # the high nibble is the element/value kind one level down (see
        # _composite_repr_kind).
        # Out: rax = repr string for that value.
        self.label("_runtime_fmt_elem")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self.emitf(
            "mov rcx, rbx",  # save full kind (incl. inner-kind bits)
            "and rbx, 0xF",  # base kind
            "cmp rbx, 1", "je ._fe_str",
            "cmp rbx, 2", "je ._fe_float",
            "cmp rbx, 3", "je ._fe_list",
            "cmp rbx, 4", "je ._fe_dict",
        )
        self._emit_int_to_str()
        self.emitf("leave", "ret")
        self.label("._fe_str")
        # wrap in single quotes -> "'" + elem + "'"
        self.emitf(
            "mov rbx, rax",
            "lea rax, [_runtime_quote_str]",
            "call _runtime_str_concat",
            "lea rbx, [_runtime_quote_str]",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )
        self.label("._fe_float")
        self.emitf("movq xmm0, rax")
        self._emit_float_to_str()
        self.emitf("leave", "ret")
        self.label("._fe_list")
        # rax = nested list ptr; rcx>>4 = element kind for _runtime_list_repr.
        self.emitf(
            "shr rcx, 4",
            "mov rbx, rcx",
            "call _runtime_list_repr",
            "leave",
            "ret",
        )
        self.label("._fe_dict")
        # rax = nested dict ptr; keys are str (rbx=1), rcx>>4 = value kind.
        self.emitf(
            "shr rcx, 4",
            "mov rbx, 1",
            "call _runtime_dict_repr",
            "leave",
            "ret",
        )

        # ---- _runtime_list_repr ----------------------------------------------
        # In: rax = list/tuple ptr, rbx = element kind. Out: rax = string.
        # [rbp-8]=list ptr [rbp-16]=kind [rbp-24]=len [rbp-32]=i
        # [rbp-40]=accumulator [rbp-48]=buf ptr
        self.label("_runtime_list_repr")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf(
            "lea rax, [_runtime_lbrack_str]",
            "call _runtime_str_concat_dup",
            "mov [rbp-40], rax",
        )
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            "mov [rbp-24], rbx",
            f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
            "mov [rbp-48], rbx",
            "mov qword [rbp-32], 0",
        )
        self.label("._lr_loop")
        self.emitf("mov rax, [rbp-32]", "cmp rax, [rbp-24]", "jge ._lr_done")
        self.emitf("mov rax, [rbp-32]", "test rax, rax", "jz ._lr_no_sep")
        self.emitf(
            "mov rax, [rbp-40]",
            "lea rbx, [_runtime_comma_str]",
            "call _runtime_str_concat",
            "mov [rbp-40], rax",
        )
        self.label("._lr_no_sep")
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rcx, [rbp-32]",
            "mov rax, [rax+rcx*8]",
            "mov rbx, [rbp-16]",
            "call _runtime_fmt_elem",
        )
        self.emitf(
            "mov rbx, rax",
            "mov rax, [rbp-40]",
            "call _runtime_str_concat",
            "mov [rbp-40], rax",
        )
        self.emitf("inc qword [rbp-32]", "jmp ._lr_loop")
        self.label("._lr_done")
        self.emitf(
            "mov rax, [rbp-40]",
            "lea rbx, [_runtime_rbrack_str]",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_dict_repr ----------------------------------------------
        # In: rax = dict ptr, rbx = key kind, rcx = value kind.
        # Out: rax = `{k: v, ...}`. Walks order_buf[0..len) (insertion order,
        # CPython 3.7+ ordering).
        # [rbp-8]=dict [rbp-16]=keykind [rbp-24]=valkind [rbp-32]=i
        # [rbp-40]=acc [rbp-48]=key
        self.label("_runtime_dict_repr")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx", "mov [rbp-24], rcx")
        self.emitf(
            "lea rax, [_runtime_lbrace_str]",
            "call _runtime_str_concat_dup",
            "mov [rbp-40], rax",
            "mov qword [rbp-32], 0",
        )
        self.label("._dr_loop")
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rbx, [rax+{self.DICT_LEN_OFF}]",
            "mov rcx, [rbp-32]",
            "cmp rcx, rbx",
            "jge ._dr_done",
            f"mov rdx, [rax+{self.DICT_ORDER_OFF}]",
            "mov rax, [rdx+rcx*8]",  # key
            "mov [rbp-48], rax",
        )
        # separator if not the first entry
        self.emitf("cmp qword [rbp-32], 0", "jz ._dr_no_sep")
        self.emitf(
            "mov rax, [rbp-40]",
            "lea rbx, [_runtime_comma_str]",
            "call _runtime_str_concat",
            "mov [rbp-40], rax",
        )
        self.label("._dr_no_sep")
        # format key
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rbx, [rbp-16]",
            "call _runtime_fmt_elem",
            "mov rbx, rax",
            "mov rax, [rbp-40]",
            "call _runtime_str_concat",
            "lea rbx, [_runtime_colon_str]",
            "call _runtime_str_concat",
            "mov [rbp-40], rax",
        )
        # fetch and format value via lookup_slot(dict, key)
        self.emitf(
            "mov rax, [rbp-8]",
            "mov rbx, [rbp-48]",
            "call _runtime_dict_lookup_slot",
            "mov rax, [rax+8]",  # value
            "mov rbx, [rbp-24]",
            "call _runtime_fmt_elem",
            "mov rbx, rax",
            "mov rax, [rbp-40]",
            "call _runtime_str_concat",
            "mov [rbp-40], rax",
        )
        self.emitf("inc qword [rbp-32]", "jmp ._dr_loop")
        self.label("._dr_done")
        self.emitf(
            "mov rax, [rbp-40]",
            "lea rbx, [_runtime_rbrace_str]",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_set_repr -----------------------------------------------
        # In: rax = set ptr (dict layout, keys only), rbx = element kind.
        # Out: rax = `{e0, e1, ...}` (or `set()` when empty).
        # [rbp-8]=set [rbp-16]=kind [rbp-24]=i [rbp-32]=acc
        # [rbp-40]=cap [rbp-48]=buf [rbp-56]=count
        self.label("_runtime_set_repr")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        # empty set -> "set()"
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rax, [rax+{self.DICT_LEN_OFF}]",
            "test rax, rax",
            "jnz ._sr_build",
            "lea rax, [_runtime_emptyset_str]",
            "call _runtime_str_concat_dup",
            "leave",
            "ret",
        )
        self.label("._sr_build")
        self.emitf(
            "lea rax, [_runtime_lbrace_str]",
            "call _runtime_str_concat_dup",
            "mov [rbp-32], rax",
        )
        self.emitf(
            "mov rax, [rbp-8]",
            f"mov rbx, [rax+{self.DICT_CAP_OFF}]",
            "mov [rbp-40], rbx",
            f"mov rbx, [rax+{self.DICT_BUF_OFF}]",
            "mov [rbp-48], rbx",
            "mov qword [rbp-24], 0",
            "mov qword [rbp-56], 0",
        )
        self.label("._srp_loop")
        self.emitf("mov rax, [rbp-24]", "cmp rax, [rbp-40]", "jge ._srp_done")
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rcx, [rbp-24]",
            "shl rcx, 4",
            "add rax, rcx",
            "mov rdx, [rax]",
            "cmp rdx, 1",
            "jbe ._srp_next",
        )
        self.emitf("mov rax, [rbp-56]", "test rax, rax", "jz ._srp_no_sep")
        self.emitf(
            "mov rax, [rbp-32]",
            "lea rbx, [_runtime_comma_str]",
            "call _runtime_str_concat",
            "mov [rbp-32], rax",
        )
        self.label("._srp_no_sep")
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rcx, [rbp-24]",
            "shl rcx, 4",
            "add rax, rcx",
            "mov rax, [rax]",
            "mov rbx, [rbp-16]",
            "call _runtime_fmt_elem",
            "mov rbx, rax",
            "mov rax, [rbp-32]",
            "call _runtime_str_concat",
            "mov [rbp-32], rax",
            "inc qword [rbp-56]",
        )
        self.label("._srp_next")
        self.emitf("inc qword [rbp-24]", "jmp ._srp_loop")
        self.label("._srp_done")
        self.emitf(
            "mov rax, [rbp-32]",
            "lea rbx, [_runtime_rbrace_str]",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_range_list ---------------------------------------------
        # In: rax = start, rbx = stop, rcx = step. Out: rax = list[int] header.
        # Materializes range(start, stop, step). Two passes: count elements,
        # malloc exactly (header + count*8) once, then fill — avoids realloc so
        # only the portable malloc helper is needed. step == 0 -> empty list.
        # [rbp-8]=start [rbp-16]=stop [rbp-24]=step [rbp-32]=count
        # [rbp-40]=hdr [rbp-48]=buf [rbp-56]=cur [rbp-64]=i
        self.label("_runtime_range_list")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx", "mov [rbp-24], rcx")
        # Pass 1: count.
        self.emitf("mov qword [rbp-32], 0")
        self.emitf("mov rax, [rbp-8]", "mov [rbp-56], rax")  # cur = start
        self.label("._rl_cloop")
        self.emitf(
            "mov rax, [rbp-24]",
            "test rax, rax",
            "jz ._rl_cdone",  # step 0 -> empty
            "jg ._rl_cpos",
            "mov rax, [rbp-56]",
            "cmp rax, [rbp-16]",
            "jle ._rl_cdone",
            "jmp ._rl_ccount",
        )
        self.label("._rl_cpos")
        self.emitf("mov rax, [rbp-56]", "cmp rax, [rbp-16]", "jge ._rl_cdone")
        self.label("._rl_ccount")
        self.emitf(
            "inc qword [rbp-32]",
            "mov rax, [rbp-56]",
            "add rax, [rbp-24]",
            "mov [rbp-56], rax",
            "jmp ._rl_cloop",
        )
        self.label("._rl_cdone")
        # Allocate header (24) and buffer (count*8, min 8 bytes so malloc(0) is safe).
        self.emitf("mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-40], rax")
        self.emitf(
            "mov rax, [rbp-32]",
            "shl rax, 3",
            "test rax, rax",
            "jnz ._rl_haspos",
            "mov rax, 8",
        )
        self.label("._rl_haspos")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [rbp-48], rax")
        # Fill: cur = start; i = 0.
        self.emitf("mov rax, [rbp-8]", "mov [rbp-56], rax", "mov qword [rbp-64], 0")
        self.label("._rl_floop")
        self.emitf("mov rax, [rbp-64]", "cmp rax, [rbp-32]", "jge ._rl_fdone")
        self.emitf(
            "mov rax, [rbp-48]",
            "mov rcx, [rbp-64]",
            "mov rdx, [rbp-56]",
            "mov [rax+rcx*8], rdx",
            "mov rax, [rbp-56]",
            "add rax, [rbp-24]",
            "mov [rbp-56], rax",
            "inc qword [rbp-64]",
            "jmp ._rl_floop",
        )
        self.label("._rl_fdone")
        # finalize header: cap = len = count; buf.
        self.emitf(
            "mov rax, [rbp-40]",
            "mov rcx, [rbp-32]",
            f"mov [rax+{self.LIST_CAP_OFF}], rcx",
            f"mov [rax+{self.LIST_LEN_OFF}], rcx",
            "mov rcx, [rbp-48]",
            f"mov [rax+{self.LIST_BUF_OFF}], rcx",
            "leave",
            "ret",
        )

        # _runtime_str_concat_dup: rax = src -> rax = "" + src (a fresh copy).
        # Lets callers seed an accumulator without aliasing a .rodata literal.
        self.label("_runtime_str_concat_dup")
        self.emitf(
            "mov rbx, rax",
            "lea rax, [_runtime_empty_str]",
            "jmp _runtime_str_concat",
        )

    def _emit_str_predicate_helpers(self) -> None:
        """Character-class predicates: isdigit/isalpha/isalnum/isspace/
        isupper/islower. Each takes rax = s and returns rax = 0/1.

        Python semantics: the empty string is False for all of these, and the
        result is True only if *every* character satisfies the class (for the
        cased predicates, additionally at least one cased character must be
        present). ASCII-only — matches the rest of the str runtime.
        """
        # The membership predicates (digit/alpha/alnum/space): non-empty and
        # every char passes. Each `checks` entry is a list of inclusive byte
        # ranges; a char passes if it falls in any range.
        membership = {
            "_runtime_str_isdigit": [(48, 57)],  # 0-9
            "_runtime_str_isalpha": [(65, 90), (97, 122)],  # A-Z a-z
            "_runtime_str_isalnum": [(48, 57), (65, 90), (97, 122)],
            "_runtime_str_isspace": [(9, 13), (32, 32)],  # \t\n\v\f\r and space
        }
        for sym, ranges in membership.items():
            tag = sym.rsplit("_", 1)[1]  # e.g. "isdigit"
            loop = f"._{tag}_loop"
            ok = f"._{tag}_char_ok"
            no = f"._{tag}_no"
            yes_empty = f"._{tag}_empty"
            self.label(sym)
            self.emitf("mov rsi, rax", "mov dl, [rsi]")
            # Empty string -> 0.
            self.emitf("test dl, dl", f"jz {yes_empty}")
            self.label(loop)
            self.emitf("mov dl, [rsi]", "test dl, dl", f"jz ._{tag}_yes")
            # char passes if in any range; otherwise -> no.
            for lo, hi in ranges:
                lo_s = str(lo)
                hi_s = str(hi)
                if lo == hi:
                    self.emitf("cmp dl, " + lo_s, "je " + ok)
                else:
                    skip = self.fresh(tag + "_rng")
                    self.emitf(
                        "cmp dl, " + lo_s,
                        "jl " + skip,
                        "cmp dl, " + hi_s,
                        "jle " + ok,
                    )
                    self.label(skip)
            self.emitf(f"jmp {no}")
            self.label(ok)
            self.emitf("inc rsi", f"jmp {loop}")
            self.label(f"._{tag}_yes")
            self.emitf("mov rax, 1", "ret")
            self.label(no)
            self.label(yes_empty)
            self.emitf("xor rax, rax", "ret")

        # Cased predicates. isupper: non-empty, no lowercase char, and at least
        # one uppercase char. islower symmetric. Use r8 as the "saw a cased
        # char of the right case" flag.
        for sym, (good_lo, good_hi, bad_lo, bad_hi) in {
            "_runtime_str_isupper": (65, 90, 97, 122),  # good=upper, bad=lower
            "_runtime_str_islower": (97, 122, 65, 90),  # good=lower, bad=upper
        }.items():
            tag = sym.rsplit("_", 1)[1]
            loop = f"._{tag}_loop"
            chk_bad = f"._{tag}_chk_bad"
            nxt = f"._{tag}_next"
            glo_s = str(good_lo)
            ghi_s = str(good_hi)
            blo_s = str(bad_lo)
            bhi_s = str(bad_hi)
            self.label(sym)
            self.emitf("mov rsi, rax", "xor r8, r8")  # r8 = saw a good cased char
            self.label(loop)
            self.emitf("mov dl, [rsi]", "test dl, dl", f"jz ._{tag}_done")
            # A char in the bad-case range fails immediately.
            self.emitf(
                "cmp dl, " + glo_s,
                "jl " + chk_bad,
                "cmp dl, " + ghi_s,
                "jg " + chk_bad,
                "mov r8, 1",
                "jmp " + nxt,
            )
            self.label(chk_bad)
            self.emitf(
                "cmp dl, " + blo_s,
                "jl " + nxt,
                "cmp dl, " + bhi_s,
                "jg " + nxt,
                "jmp ._" + tag + "_no",
            )
            self.label(nxt)
            self.emitf("inc rsi", f"jmp {loop}")
            self.label(f"._{tag}_done")
            # Result = r8 (true only if we saw >=1 good cased char and no bad).
            self.emitf("mov rax, r8", "ret")
            self.label(f"._{tag}_no")
            self.emitf("xor rax, rax", "ret")

    def _emit_str_partition_helper(self) -> None:
        """`_runtime_str_partition`: `s.partition(sep)` -> 3-tuple.

        In:  rax = s, rbx = sep.
        Out: rax = a 3-slot tuple in the list [cap,len,buf] layout:
             (before, sep, after) at the first occurrence of sep, or
             (s, "", "") when sep doesn't occur (Python semantics).

        Composes existing helpers: index_of locates sep, str_slice carves the
        prefix, strdup copies sep and the suffix. Locals [rbp-8..rbp-56];
        reserve 56 + 32 shadow = 88 -> 96 (16-aligned).
        """
        self.label("_runtime_str_partition")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
        self.emitf(
            "mov [rbp-8], rax",  # s
            "mov [rbp-16], rbx",  # sep
            "call _runtime_str_index_of",
            "mov [rbp-24], rax",  # idx (or -1)
            "cmp rax, -1",
            "jne ._spar_found",
        )
        # Not found: (strdup(s), "", "").
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf(
            "mov [rbp-32], rax",  # before = copy of s
            "lea rax, [rel _runtime_empty_str]",
            "mov [rbp-40], rax",  # mid = ""
            "mov [rbp-48], rax",  # after = ""
            "jmp ._spar_build",
        )
        self.label("._spar_found")
        # before = s[0:idx]
        self.emitf(
            "mov rax, [rbp-8]",
            "xor rbx, rbx",
            "mov rcx, [rbp-24]",
            "call _runtime_str_slice",
            "mov [rbp-32], rax",
        )
        # mid = strdup(sep)
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strdup()
        self.emitf("mov [rbp-40], rax")
        # after = strdup(s + idx + strlen(sep))
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("add rax, [rbp-24]", "add rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf("mov [rbp-48], rax")
        self.label("._spar_build")
        # Tuple header (24 bytes): cap=3, len=3, then a 3-slot buffer.
        self.emitf("mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-56], rax",
            f"mov qword [rax+{self.LIST_CAP_OFF}], 3",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 3",
            "mov rax, 24",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rcx, [rbp-56]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov rdx, [rbp-32]",
            "mov [rax], rdx",
            "mov rdx, [rbp-40]",
            "mov [rax+8], rdx",
            "mov rdx, [rbp-48]",
            "mov [rax+16], rdx",
            "mov rax, [rbp-56]",
            "leave",
            "ret",
        )

    def _emit_str_rpartition_helper(self) -> None:
        """`_runtime_str_rpartition`: `s.rpartition(sep)` -> 3-tuple.

        In:  rax = s, rbx = sep.
        Out: rax = a 3-slot tuple in the list [cap,len,buf] layout:
             (before, sep, after) at the LAST occurrence of sep, or
             ("", "", s) when sep doesn't occur (Python semantics — note this
             is the mirror image of partition's not-found case).

        Finds the last occurrence with a forward strstr scan advancing past
        each hit (same approach as rsplit). Locals [rbp-8..rbp-72]; reserve
        72 + 32 shadow = 104 -> 112 (16-aligned).
        """
        self.label("_runtime_str_rpartition")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf(
            "mov [rbp-8], rax",  # s
            "mov [rbp-16], rbx",  # sep
            "mov qword [rbp-24], -1",  # last index
            "mov [rbp-32], rax",  # scan cursor
        )
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-40], rax", "test rax, rax", "jz ._srp_notfound")
        self.label("._srp_scan")
        self.emitf("mov rax, [rbp-32]", "mov rbx, [rbp-16]")
        self._emit_libc_strstr()
        self.emitf(
            "test rax, rax",
            "jz ._srp_scandone",
            "mov rdx, rax",
            "sub rdx, [rbp-8]",
            "mov [rbp-24], rdx",  # last = hit - s
            "add rax, [rbp-40]",  # cursor = hit + seplen (non-overlapping)
            "mov [rbp-32], rax",
            "jmp ._srp_scan",
        )
        self.label("._srp_scandone")
        self.emitf("cmp qword [rbp-24], -1", "je ._srp_notfound")
        # before = s[0:last]
        self.emitf(
            "mov rax, [rbp-8]",
            "xor rbx, rbx",
            "mov rcx, [rbp-24]",
            "call _runtime_str_slice",
            "mov [rbp-48], rax",
        )
        # mid = strdup(sep)
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strdup()
        self.emitf("mov [rbp-56], rax")
        # after = strdup(s + last + seplen)
        self.emitf("mov rax, [rbp-8]", "add rax, [rbp-24]", "add rax, [rbp-40]")
        self._emit_libc_strdup()
        self.emitf("mov [rbp-64], rax", "jmp ._srp_build")
        self.label("._srp_notfound")
        # ("", "", strdup(s))
        self.emitf(
            "lea rax, [rel _runtime_empty_str]",
            "mov [rbp-48], rax",  # before = ""
            "mov [rbp-56], rax",  # mid = ""
            "mov rax, [rbp-8]",
        )
        self._emit_libc_strdup()
        self.emitf("mov [rbp-64], rax")  # after = copy of s
        self.label("._srp_build")
        # Tuple header (24 bytes): cap=3, len=3, then a 3-slot buffer.
        self.emitf("mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-72], rax",
            f"mov qword [rax+{self.LIST_CAP_OFF}], 3",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 3",
            "mov rax, 24",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rcx, [rbp-72]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov rdx, [rbp-48]",
            "mov [rax], rdx",
            "mov rdx, [rbp-56]",
            "mov [rax+8], rdx",
            "mov rdx, [rbp-64]",
            "mov [rax+16], rdx",
            "mov rax, [rbp-72]",
            "leave",
            "ret",
        )

    def _emit_str_rsplit_helper(self) -> None:
        """`_runtime_str_rsplit`: `s.rsplit(sep, 1)` -> list[str].

        In:  rax = s, rbx = sep, rcx = maxsplit (sema pins it to 1; ignored).
        Out: rax = list header: [before, after] split at the LAST occurrence of
             sep, or [s-copy] when sep doesn't occur / is empty.

        Finds the last occurrence with a forward strstr scan advancing past
        each hit. Locals [rbp-8..rbp-72]; reserve 72 + 32 shadow = 104 -> 112.
        """
        self.label("_runtime_str_rsplit")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf(
            "mov [rbp-8], rax",  # s
            "mov [rbp-16], rbx",  # sep
            "mov qword [rbp-24], -1",  # last index
            "mov [rbp-32], rax",  # scan cursor
        )
        # seplen = strlen(sep); empty sep -> single-element result.
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-40], rax", "test rax, rax", "jz ._srs_one")
        self.label("._srs_scan")
        self.emitf("mov rax, [rbp-32]", "mov rbx, [rbp-16]")
        self._emit_libc_strstr()
        self.emitf(
            "test rax, rax",
            "jz ._srs_scandone",
            "mov rdx, rax",
            "sub rdx, [rbp-8]",
            "mov [rbp-24], rdx",  # last = hit - s
            "add rax, [rbp-40]",  # cursor = hit + seplen (non-overlapping)
            "mov [rbp-32], rax",
            "jmp ._srs_scan",
        )
        self.label("._srs_scandone")
        self.emitf("cmp qword [rbp-24], -1", "je ._srs_one")
        # before = s[0:last]
        self.emitf(
            "mov rax, [rbp-8]",
            "xor rbx, rbx",
            "mov rcx, [rbp-24]",
            "call _runtime_str_slice",
            "mov [rbp-48], rax",
        )
        # after = strdup(s + last + seplen)
        self.emitf("mov rax, [rbp-8]", "add rax, [rbp-24]", "add rax, [rbp-40]")
        self._emit_libc_strdup()
        self.emitf("mov [rbp-56], rax", "mov qword [rbp-64], 2", "jmp ._srs_build")
        self.label("._srs_one")
        # No split: a single-element list holding a copy of s.
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf("mov [rbp-48], rax", "mov qword [rbp-64], 1")
        self.label("._srs_build")
        # header: cap = len = n; buf = n*8.
        self.emitf("mov rax, 24")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [rbp-72], rax",
            "mov rdx, [rbp-64]",
            f"mov [rax+{self.LIST_CAP_OFF}], rdx",
            f"mov [rax+{self.LIST_LEN_OFF}], rdx",
            "mov rax, [rbp-64]",
            "shl rax, 3",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rcx, [rbp-72]",
            f"mov [rcx+{self.LIST_BUF_OFF}], rax",
            "mov rdx, [rbp-48]",
            "mov [rax], rdx",
            "cmp qword [rbp-64], 2",
            "jne ._srs_ret",
            "mov rdx, [rbp-56]",
            "mov [rax+8], rdx",
        )
        self.label("._srs_ret")
        self.emitf("mov rax, [rbp-72]", "leave", "ret")

    def _emit_chr_helper(self) -> None:
        """`_runtime_chr`: chr(n) -> a fresh 1-char string (byte n, NUL).

        In: rax = int (0..255 meaningful). Out: rax = 2-byte heap string.
        """
        self.label("_runtime_chr")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self.emitf("mov [rbp-8], rax", "mov rax, 2")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov rdx, [rbp-8]",
            "mov [rax], dl",
            "mov byte [rax+1], 0",
            "leave",
            "ret",
        )

    def _emit_str_split_helper(self) -> None:
        """`_runtime_str_split`: `s.split(sep[, maxsplit])` -> list[str].

        In:  rax = s, rbx = sep, rcx = maxsplit (0 = no limit).
        Out: rax = list header.

        Locals span [rbp-8..rbp-104]; frame = 144 (104 locals + 32 shadow + 8 align).
        """
        self.label("_runtime_str_split")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 144")
        self.emitf(
            "mov [rbp-8], rax",    # s
            "mov [rbp-16], rbx",   # sep
            "mov [rbp-104], rcx",  # maxsplit (0 = unlimited)
        )
        # sep_len
        self.emitf("mov rax, [rbp-16]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-24], rax")  # sep_len
        # n_parts = (sep_len > 0) ? count(s, sep) + 1 : 1
        empty_sep = self.fresh("ssp_empty")
        count_done = self.fresh("ssp_count_done")
        self.emitf("test rax, rax", f"jz {empty_sep}")
        self.emitf("mov rax, [rbp-8]", "mov rbx, [rbp-16]")
        self.emitf("call _runtime_str_count", "inc rax", f"jmp {count_done}")
        self.label(empty_sep)
        self.emitf("mov rax, 1")
        self.label(count_done)
        # Cap n_parts at maxsplit+1 when maxsplit > 0
        ms_cap_skip = self.fresh("ssp_ms_skip")
        self.emitf("mov rcx, [rbp-104]", "test rcx, rcx", f"jz {ms_cap_skip}",
                   "inc rcx",          # rcx = maxsplit + 1
                   "cmp rax, rcx", f"jle {ms_cap_skip}",
                   "mov rax, rcx")     # n_parts = min(n_parts, maxsplit+1)
        self.label(ms_cap_skip)
        self.emitf("mov [rbp-32], rax")  # n_parts
        # cap = max(n_parts, 4)
        cap_ok = self.fresh("ssp_cap_ok")
        self.emitf("cmp rax, 4", f"jge {cap_ok}", "mov rax, 4")
        self.label(cap_ok)
        self.emitf("mov [rbp-40], rax")  # cap
        # Allocate list header (24)
        self.emitf("mov rcx, 24", "call malloc", "mov [rbp-48], rax")
        # Initialize header: cap, len = n_parts, buf set below.
        self.emitf(
            "mov rdx, [rbp-48]",
            "mov rax, [rbp-40]",
            f"mov [rdx+{self.LIST_CAP_OFF}], rax",
            "mov rax, [rbp-32]",
            f"mov [rdx+{self.LIST_LEN_OFF}], rax",
        )
        # Allocate buffer cap * 8
        self.emitf("mov rcx, [rbp-40]", "shl rcx, 3", "call malloc")
        self.emitf(
            "mov [rbp-56], rax",  # list buf
            "mov rdx, [rbp-48]",
            f"mov [rdx+{self.LIST_BUF_OFF}], rax",
        )
        # Walk: cursor = s, w = 0
        self.emitf(
            "mov rax, [rbp-8]",
            "mov [rbp-64], rax",  # cursor
            "mov qword [rbp-72], 0",  # w
        )
        # If sep is empty: emit the whole string as one element and return.
        empty_branch = self.fresh("ssp_empty_done")
        self.emitf("cmp qword [rbp-24], 0", f"jne {empty_branch}")
        # Single element: strdup(s) and append.
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strdup()
        self.emitf(
            "mov rdx, [rbp-56]",
            "mov [rdx], rax",
            "mov rax, [rbp-48]",
            "leave",
            "ret",
        )
        self.label(empty_branch)

        loop = self.fresh("ssp_loop")
        last = self.fresh("ssp_last")
        end = self.fresh("ssp_end")
        self.label(loop)
        # If maxsplit > 0 and w >= maxsplit, treat rest of string as final segment.
        ms_loop_skip = self.fresh("ssp_ms_loop_skip")
        self.emitf("mov rcx, [rbp-104]", "test rcx, rcx", f"jz {ms_loop_skip}",
                   "cmp [rbp-72], rcx", f"jge {last}")
        self.label(ms_loop_skip)
        # Find next sep occurrence in cursor
        self.emitf("mov rax, [rbp-64]", "mov rbx, [rbp-16]")
        self._emit_libc_strstr()
        self.emitf("test rax, rax", f"jz {last}")
        self.emitf("mov [rbp-80], rax")  # match ptr
        # seg_len = match - cursor
        self.emitf("sub rax, [rbp-64]", "mov [rbp-88], rax")
        # malloc(seg_len + 1)
        self.emitf("inc rax", "mov rcx, rax", "call malloc", "mov [rbp-96], rax")
        # memcpy(new, cursor, seg_len)
        self.emitf(
            "mov rax, [rbp-96]",
            "mov rbx, [rbp-64]",
            "mov rcx, [rbp-88]",
        )
        self._emit_libc_memcpy()
        # nul-terminate
        self.emitf(
            "mov rax, [rbp-96]",
            "mov rcx, [rbp-88]",
            "mov byte [rax+rcx], 0",
        )
        # list_buf[w*8] = new_str
        self.emitf(
            "mov rax, [rbp-56]",
            "mov rcx, [rbp-72]",
            "shl rcx, 3",
            "add rax, rcx",
            "mov rbx, [rbp-96]",
            "mov [rax], rbx",
            "inc qword [rbp-72]",
            # cursor = match + sep_len
            "mov rax, [rbp-80]",
            "add rax, [rbp-24]",
            "mov [rbp-64], rax",
            f"jmp {loop}",
        )
        # Last segment: from cursor to end-of-string.
        self.label(last)
        self.emitf("mov rax, [rbp-64]")
        self._emit_libc_strdup()
        self.emitf(
            "mov rbx, rax",
            "mov rax, [rbp-56]",
            "mov rcx, [rbp-72]",
            "shl rcx, 3",
            "add rax, rcx",
            "mov [rax], rbx",
        )
        self.label(end)
        self.emitf("mov rax, [rbp-48]", "leave", "ret")

    def _emit_str_split_ws_helper(self) -> None:
        """`_runtime_str_split_ws`: `s.split()` — split on runs of whitespace.

        In:  rax = s.  Out: rax = list header.

        Algorithm: single pass, grow list dynamically (initial cap 4).
        For each word: save cur to word_start ([rbp-48]), scan to end,
        temporarily NUL-terminate at end, strdup(word_start), restore byte,
        append ptr to list, grow list buf if needed.

        Frame slots:
          [rbp-8]  = s
          [rbp-16] = list header ptr
          [rbp-24] = list buf ptr
          [rbp-32] = len (# words stored)
          [rbp-40] = cap
          [rbp-48] = cursor (char*)
          [rbp-56] = word_start
          [rbp-64] = saved byte (for restore after NUL)
        Total locals = 64; frame = 64+32+8 = 104 (16-byte aligned after push rbp).
        """
        # whitespace chars: space(0x20) tab(0x09) newline(0x0A) CR(0x0D) FF(0x0C) VT(0x0B)
        def _is_ws_jmp(jmp_if_ws: str, jmp_if_not_ws: str) -> None:
            # rax holds the byte value (zero-extended)
            self.emitf(
                f"cmp rax, 0x20", f"je {jmp_if_ws}",
                f"cmp rax, 0x09", f"je {jmp_if_ws}",
                f"cmp rax, 0x0A", f"je {jmp_if_ws}",
                f"cmp rax, 0x0D", f"je {jmp_if_ws}",
                f"cmp rax, 0x0C", f"je {jmp_if_ws}",
                f"cmp rax, 0x0B", f"je {jmp_if_ws}",
                f"jmp {jmp_if_not_ws}",
            )

        self.label("_runtime_str_split_ws")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 104")
        self.emitf("mov [rbp-8], rax")

        # Allocate initial list: header + buf of 4 slots
        self.emitf(
            "mov rcx, 24", "call malloc", "mov [rbp-16], rax",
            f"mov qword [rax+{self.LIST_CAP_OFF}], 4",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
        )
        self.emitf(
            "mov rcx, 32", "call malloc", "mov [rbp-24], rax",
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_BUF_OFF}], rax",
        )
        self.emitf(
            "mov qword [rbp-32], 0",   # len
            "mov qword [rbp-40], 4",   # cap
            "mov rax, [rbp-8]",
            "mov [rbp-48], rax",       # cursor = s
        )

        top = self.fresh("ssw_top")
        end = self.fresh("ssw_end")
        found_word = self.fresh("ssw_word")
        skip_ws_lbl = self.fresh("ssw_skipws")
        not_ws1 = self.fresh("ssw_nws1")
        scan_word_lbl = self.fresh("ssw_scan")
        not_ws2 = self.fresh("ssw_nws2")
        is_ws2 = self.fresh("ssw_ws2")

        # Loop: skip whitespace, then extract word
        self.label(top)
        self.emitf(
            "mov rax, [rbp-48]",
            "movzx rax, byte [rax]",
            f"test rax, rax",
            f"jz {end}",
        )
        _is_ws_jmp(skip_ws_lbl, not_ws1)
        self.label(skip_ws_lbl)
        self.emitf(
            "mov rax, [rbp-48]",
            "inc rax",
            "mov [rbp-48], rax",
            f"jmp {top}",
        )
        # Not whitespace: start of word
        self.label(not_ws1)
        # word_start = cursor
        self.emitf(
            "mov rax, [rbp-48]",
            "mov [rbp-56], rax",
        )
        # Scan to end of word (stop at ws or NUL)
        self.label(scan_word_lbl)
        self.emitf(
            "mov rax, [rbp-48]",
            "inc rax",
            "mov [rbp-48], rax",
            "movzx rax, byte [rax]",
            f"test rax, rax",
            f"jz {found_word}",
        )
        _is_ws_jmp(is_ws2, not_ws2)
        self.label(is_ws2)
        self.emitf(f"jmp {found_word}")
        self.label(not_ws2)
        self.emitf(f"jmp {scan_word_lbl}")

        self.label(found_word)
        # cursor points to the byte after the last word char (ws or NUL).
        # Allocate word_len+1 bytes and copy — do NOT write into the source
        # string, which may be in read-only .rdata memory.
        # Layout: [rbp-56]=word_start(src), [rbp-48]=cursor(end), [rbp-64]=word_len
        # RDI and RSI are callee-saved on Windows x64.
        copy_lp = self.fresh("ssw_cp")
        copy_end = self.fresh("ssw_cpend")
        self.emitf(
            # word_len = cursor - word_start
            "mov rax, [rbp-48]",       # rax = cursor (one past last word char)
            "sub rax, [rbp-56]",       # rax = word_len
            "mov [rbp-64], rax",       # [rbp-64] = word_len
            # malloc(word_len + 1) for NUL
            "inc rax",
            "mov rcx, rax",
            "call malloc",             # rax = dst buf
            # Save dst; use callee-saved RDI and RSI (preserved across calls)
            "push rdi",
            "push rsi",
            "mov rdi, rax",            # rdi = dst (advancing)
            "mov rsi, [rbp-56]",       # rsi = src = word_start
            "mov rcx, [rbp-64]",       # rcx = word_len
        )
        self.label(copy_lp)
        self.emitf(
            "test rcx, rcx",
            f"jz {copy_end}",
            "movzx rax, byte [rsi]",
            "mov [rdi], al",
            "inc rsi",
            "inc rdi",
            "dec rcx",
            f"jmp {copy_lp}",
        )
        self.label(copy_end)
        self.emitf(
            "mov byte [rdi], 0",       # NUL terminate
            # dst_base = rdi - word_len = &buf[0]
            "sub rdi, [rbp-64]",
            "mov [rbp-56], rdi",       # [rbp-56] = word dup ptr
            "pop rsi",                 # restore rsi
            "pop rdi",                 # restore rdi
        )
        # Grow list if len == cap
        no_grow = self.fresh("ssw_nogrow")
        self.emitf(
            "mov rax, [rbp-32]",
            "cmp rax, [rbp-40]",
            f"jl {no_grow}",
        )
        # Double cap: new_cap = cap*2; realloc buf
        # Windows x64 realloc(ptr, size): rcx=ptr, rdx=new_size
        self.emitf(
            "mov rax, [rbp-40]",
            "shl rax, 1",
            "mov [rbp-40], rax",      # save new cap
            "shl rax, 3",             # bytes = new_cap * 8
            "mov rdx, rax",           # rdx = new size in bytes
            "mov rcx, [rbp-24]",      # rcx = old buf ptr
            "call realloc",
            "mov [rbp-24], rax",      # save new buf
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_BUF_OFF}], rax",
            "mov rax, [rbp-40]",
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_CAP_OFF}], rax",
        )
        self.label(no_grow)
        # buf[len] = word_dup; len++
        self.emitf(
            "mov rax, [rbp-32]",
            "mov rdx, [rbp-24]",
            "mov rcx, [rbp-56]",
            "mov [rdx+rax*8], rcx",
            "inc qword [rbp-32]",
        )
        self.emitf(f"jmp {top}")

        self.label(end)
        # Set list len in header
        self.emitf(
            "mov rax, [rbp-32]",
            "mov rdx, [rbp-16]",
            f"mov [rdx+{self.LIST_LEN_OFF}], rax",
            "mov rax, rdx",
            "leave",
            "ret",
        )

    def _emit_str_splitlines_helper(self) -> None:
        """`_runtime_str_splitlines`: `s.splitlines()` -> list[str].

        In:  rax = s. Out: rax = list header.

        Implemented as split on '\\n' followed by dropping a single trailing
        empty element (so `"a\\nb\\n".splitlines()` -> ["a","b"], matching
        CPython for LF-terminated text — the form the compiler's own source
        uses). Bare CR / CRLF aren't special-cased.
        """
        self.label("_runtime_str_splitlines")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        # split(s, "\n")
        self.emitf(
            "lea rbx, [_runtime_nl_str]",
            "xor rcx, rcx",        # maxsplit = 0 (no limit)
            "call _runtime_str_split",
            "mov [rbp-8], rax",  # list header
        )
        # If len > 0 and the last element is "" (strlen 0), drop it.
        done = self.fresh("splitlines_done")
        self.emitf(
            f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
            "test rcx, rcx",
            f"jz {done}",  # empty list -> nothing to trim
            # last element ptr = buf[len-1]
            f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
            "dec rcx",
            "mov rax, [rdx+rcx*8]",  # rax = last element str ptr
        )
        # strlen(last) -> if 0, decrement the list length.
        self._emit_libc_strlen()  # rax = length of last element
        self.emitf(
            "test rax, rax",
            f"jnz {done}",
            "mov rdx, [rbp-8]",
            f"mov rcx, [rdx+{self.LIST_LEN_OFF}]",
            "dec rcx",
            f"mov [rdx+{self.LIST_LEN_OFF}], rcx",
        )
        self.label(done)
        self.emitf("mov rax, [rbp-8]", "leave", "ret")

    def _emit_str_join_helper(self) -> None:
        """`_runtime_str_join`: `sep.join(parts)` -> str.

        In:  rax = sep, rbx = list[str] header.
        Out: rax = newly-allocated concatenation.

        Two-pass: first sums total length (sep * (n-1) + sum(len(parts[i]))),
        then mallocs and copies each part with the separator between.

        Locals span [rbp-8..rbp-72]; reserve 80 + 32 shadow = 112 -> 112.
        """
        self.label("_runtime_str_join")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 112")
        self.emitf(
            "mov [rbp-8], rax",  # sep
            "mov [rbp-16], rbx",  # list header
        )
        # n = list.len
        self.emitf(f"mov rax, [rbx+{self.LIST_LEN_OFF}]", "mov [rbp-24], rax")
        # sep_len
        self.emitf("mov rax, [rbp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [rbp-32], rax")  # sep_len
        # total = sep_len * max(0, n-1)
        sep_zero = self.fresh("sj_sep_zero")
        self.emitf("mov rcx, [rbp-24]", "test rcx, rcx", f"jz {sep_zero}")
        self.emitf(
            "dec rcx", "mov rax, [rbp-32]", "imul rax, rcx", f"jmp {sep_zero}_done"
        )
        self.label(sep_zero)
        self.emitf("xor rax, rax")
        self.label(f"{sep_zero}_done")
        self.emitf("mov [rbp-40], rax")  # total
        # Add sum(strlen(parts[i]))
        self.emitf(
            "mov qword [rbp-48], 0",  # i = 0
        )
        sum_loop = self.fresh("sj_sum_loop")
        sum_done = self.fresh("sj_sum_done")
        self.label(sum_loop)
        self.emitf(
            "mov rax, [rbp-48]",
            "cmp rax, [rbp-24]",
            f"jge {sum_done}",
            "mov rbx, [rbp-16]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rcx, [rbp-48]",
            "shl rcx, 3",
            "mov rax, [rbx+rcx]",
        )
        self._emit_libc_strlen()
        self.emitf("add [rbp-40], rax", "inc qword [rbp-48]", f"jmp {sum_loop}")
        self.label(sum_done)
        # malloc(total + 1)
        self.emitf("mov rax, [rbp-40]", "inc rax", "mov rcx, rax", "call malloc")
        self.emitf("mov [rbp-56], rax")  # output buffer
        # Walk parts again, copying each. After the first, prepend separator.
        self.emitf(
            "mov qword [rbp-48], 0",  # i
            "mov rax, [rbp-56]",
            "mov [rbp-64], rax",  # write cursor
        )
        cp_loop = self.fresh("sj_cp_loop")
        cp_done = self.fresh("sj_cp_done")
        not_first = self.fresh("sj_not_first")
        self.label(cp_loop)
        self.emitf(
            "mov rax, [rbp-48]",
            "cmp rax, [rbp-24]",
            f"jge {cp_done}",
            "test rax, rax",
            f"jz {not_first}",
        )
        # Copy separator first.
        self.emitf(
            "mov rax, [rbp-64]",
            "mov rbx, [rbp-8]",
            "mov rcx, [rbp-32]",
        )
        self._emit_libc_memcpy()
        self.emitf(
            "mov rax, [rbp-64]",
            "add rax, [rbp-32]",
            "mov [rbp-64], rax",
        )
        self.label(not_first)
        # part = list.buf[i*8]
        self.emitf(
            "mov rbx, [rbp-16]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rcx, [rbp-48]",
            "shl rcx, 3",
            "mov rdx, [rbx+rcx]",
            "mov [rbp-72], rdx",  # part ptr
            "mov rax, rdx",
        )
        self._emit_libc_strlen()
        # memcpy(write, part, plen)
        self.emitf(
            "mov rcx, rax",
            "mov rbx, [rbp-72]",
            "mov rax, [rbp-64]",
            "push rcx",
        )
        self._emit_libc_memcpy()
        self.emitf(
            "pop rcx",
            "mov rax, [rbp-64]",
            "add rax, rcx",
            "mov [rbp-64], rax",
            "inc qword [rbp-48]",
            f"jmp {cp_loop}",
        )
        self.label(cp_done)
        # nul-terminate at write cursor
        self.emitf("mov rax, [rbp-64]", "mov byte [rax], 0")
        self.emitf("mov rax, [rbp-56]", "leave", "ret")

    def emit_exception_runtime(self) -> None:
        """Globals + helpers for setjmp/longjmp exception handling.

        We hand-roll setjmp/longjmp instead of calling into libc because the
        MSVC `setjmp` is a compiler intrinsic with a special buffer layout
        that doesn't work right when invoked from hand-written assembly.

        Buffer layout (64 bytes, all 8 bytes each):
          [0]  saved rbx
          [8]  saved rbp
          [16] saved r12
          [24] saved r13
          [32] saved r14
          [40] saved r15
          [48] saved rsp (caller's rsp at the call site, just after the call
                          pushed the return address)
          [56] saved rip (return address)
        """
        if self.use_runtime_lib:
            for sym in (
                "_runtime_setjmp",
                "_runtime_longjmp",
                "_runtime_raise",
                "_runtime_handler_top",
                "_runtime_exc_msg",
                "_runtime_exc_type",
            ):
                self.emit(f"extern {sym}")
            return
        # BSS globals — zero-initialized, 8 bytes each.
        self.emit("section .bss")
        self.emit("_runtime_handler_top: resq 1")
        self.emit("_runtime_exc_msg:     resq 1")
        self.emit("_runtime_exc_type:    resq 1")

        self.emit("section .rodata")
        self.emit('_runtime_unhandled_prefix: db "Unhandled exception: ",0')

        self.emit("section .text")

        # ---- _runtime_setjmp -------------------------------------------------
        # asmpython's internal calling convention for runtime helpers: rax = primary
        # input/output. Here rax holds the jmp_buf pointer.
        # Output: rax = 0 on initial call; nonzero after longjmp.
        self.label("_runtime_setjmp")
        # Save callee-saved registers and the return frame state.
        self.emitf(
            "mov [rax+0],  rbx",
            "mov [rax+8],  rbp",
            "mov [rax+16], r12",
            "mov [rax+24], r13",
            "mov [rax+32], r14",
            "mov [rax+40], r15",
        )
        # Save rsp at the point just after our caller did `call _runtime_setjmp`.
        # When we entered, the call instruction pushed the return address, so
        # rsp now points at that address. Save rsp+8 (caller's rsp before call).
        self.emitf("lea rcx, [rsp+8]", "mov [rax+48], rcx")
        # Save return address (the instruction after the call in the caller).
        self.emitf("mov rcx, [rsp]", "mov [rax+56], rcx")
        self.emitf("xor rax, rax", "ret")

        # ---- _runtime_longjmp ------------------------------------------------
        # Input: rax = jmp_buf pointer, rbx = return value (nonzero).
        # Restores all saved registers and resumes at the saved rip.
        self.label("_runtime_longjmp")
        # Move buf to a scratch reg first because we're about to overwrite rbx.
        self.emitf(
            "mov rcx, rax",
            "mov rax, rbx",  # return value
            "mov rbx, [rcx+0]",
            "mov rbp, [rcx+8]",
            "mov r12, [rcx+16]",
            "mov r13, [rcx+24]",
            "mov r14, [rcx+32]",
            "mov r15, [rcx+40]",
            "mov rsp, [rcx+48]",
            "jmp [rcx+56]",
        )

        # ---- _runtime_raise --------------------------------------------------
        self.label("_runtime_raise")
        # rax = exception message (string ptr), rbx = exception type id.
        # Stash both in the globals.
        self.emitf(
            "mov [rel _runtime_exc_msg], rax", "mov [rel _runtime_exc_type], rbx"
        )
        # If no handler installed, print and exit.
        self.emitf(
            "mov rax, [rel _runtime_handler_top]", "test rax, rax", "jnz ._rr_jump"
        )
        # Unhandled path.
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 48",
        )
        self._emit_set_error_color()
        self.emitf("lea rax, [rel _runtime_unhandled_prefix]")
        self._emit_print_str_ptr_no_newline()
        self.emitf("mov rax, [rel _runtime_exc_msg]")
        self._emit_print_str_ptr_no_newline()
        self._emit_print_newline()
        self._emit_exit_one()
        # Handler path: hand-rolled longjmp(handler, 1).
        self.label("._rr_jump")
        self.emitf(
            "mov rbx, 1",  # longjmp value
            "call _runtime_longjmp",
        )

    def _emit_panic_message(self) -> None:
        """rax = nul-terminated message ptr -> print and exit(1)."""
        self._emit_print_str_ptr_no_newline()
        self._emit_exit_one()

    def _emit_set_error_color(self) -> None:
        """Hook called just before an unhandled exception's message is
        printed, so text-mode targets can switch to an attention-grabbing
        color. No-op for targets without a text display."""
        pass

    def _emit_exit_one(self) -> None:
        raise NotImplementedError

    def _gen_const_load(self, c: stdlib.Const) -> None:
        value = self._platform_const_value(c)
        if c.ty == "float":
            label = self.intern_float(value)  # type: ignore
            self.emitf(f"movsd xmm0, [{label}]")
        elif c.ty == "int":
            self.emitf(f"mov rax, {value}")
        elif c.ty == "str":
            label, _ = self.intern_string(value)  # type: ignore
            self.emitf(f"lea rax, [{label}]")
        elif c.ty == "list" and value == "__sys_argv__":
            # sys.argv: list[str] built at program startup (see emit_entry).
            self.emitf("mov rax, [rel _sys_argv_list]")
        else:
            raise NotImplementedError(f"const type {c.ty!r}")

    def _platform_const_value(self, c: stdlib.Const):
        """Return the value to use for this target (may differ from c.value)."""
        return c.value

    # ---- list operations ---------------------------------------------------

    # ---- List ABI ----------------------------------------------------------
    #
    # A list value is a pointer to a stable 24-byte HEADER that never moves:
    #   [0..8)   capacity   (number of slots in the data buffer)
    #   [8..16)  length     (number of populated slots)
    #   [16..24) buffer_ptr (pointer to heap-allocated array of int64s)
    #
    # Element i is at  [buffer_ptr + i*8].
    # Append may realloc the buffer, but the header (and therefore the user's
    # list local) keeps pointing at the same address. This makes "xs" a
    # stable handle to a growable list.

    LIST_CAP_OFF = 0
    LIST_LEN_OFF = 8
    LIST_BUF_OFF = 16
    LIST_HEADER = 24

    # ---- Dict ABI ----------------------------------------------------------
    # Open-addressed string-keyed hashtable; values are int64.
    # Header (40 bytes, stable):
    #   [0..8)   capacity (number of slots; always a power of 2)
    #   [8..16)  length (live entries)
    #   [16..24) tombstones (deleted slots not yet reclaimed)
    #   [24..32) slots_ptr (heap buffer of slots)
    #   [32..40) order_ptr (heap buffer of `cap` key_ptrs, insertion order;
    #            the first `length` entries are the live keys in the order
    #            they were first inserted -- gives CPython 3.7+ dict
    #            insertion-order iteration without changing the hashtable
    #            itself)
    # Slot (16 bytes each):
    #   [0..8)   key_ptr  (0 = empty, 1 = tombstone, otherwise nul-term string)
    #   [8..16)  value
    DICT_CAP_OFF = 0
    DICT_LEN_OFF = 8
    DICT_TOMB_OFF = 16
    DICT_BUF_OFF = 24
    DICT_ORDER_OFF = 32
    DICT_HEADER = 40
    DICT_SLOT_SIZE = 16

    def _emit_dict_alloc_order_buf(self, cap: int, slot_off: int) -> None:
        """Allocate the `cap`-entry order buffer (`cap*8` bytes, zero-filled)
        for a freshly-created dict/set/instance header parked at `slot_off`,
        and store it at DICT_ORDER_OFF. Call right after the slot buffer is
        allocated and stored, mirroring that same idiom."""
        self.emitf(f"mov rbx, {cap * 8}", "call _runtime_zalloc")
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]", f"mov [rbx+{self.DICT_ORDER_OFF}], rax"
        )

    def _gen_list_lit(self, e: A.ListLit, info: FuncInfo) -> None:
        # We cannot use push/pop across `call` (it breaks 16-byte stack
        # alignment), so each ListLit gets a pre-reserved frame slot to
        # hold the header pointer between malloc calls.
        slot_off = info.locals_[f"__listlit_{id(e)}"]

        n = len(e.elems)
        cap = max(n, 4)
        self._emit_malloc(self.LIST_HEADER)  # rax = header
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], {n}",
            f"mov [rbp{slot_off:+d}], rax",
        )  # park header in slot
        self._emit_malloc(cap * 8)  # rax = buffer
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]",  # rbx = header
            f"mov [rbx+{self.LIST_BUF_OFF}], rax",
        )
        for i, el in enumerate(e.elems):
            self.gen_expr(el, info)  # rax/xmm0 = value (may call!)
            self.emitf(
                f"mov rbx, [rbp{slot_off:+d}]", f"mov rcx, [rbx+{self.LIST_BUF_OFF}]"
            )
            if e.el_type == "float":
                self.emitf(f"movsd [rcx+{i * 8}], xmm0")
            else:
                self.emitf(f"mov [rcx+{i * 8}], rax")
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    def _gen_comprehension(self, e: A.Comprehension, info: FuncInfo) -> None:
        """[elt for var in iter (if cond)] -> build an empty list, iterate the
        (list-typed) iterable, and append elt for each element that passes the
        filter. Result list pointer left in rax.

        Like _gen_list_lit, this never uses push/pop across a call (it parks
        intermediate pointers in frame slots), keeping rsp 16-byte aligned."""
        # `[elt for i, x in enumerate(xs)]` — special case: iterate xs by index,
        # bind the index counter to the first target and the element to the second.
        if (
            isinstance(e.iter, A.Call)
            and e.iter.func == "enumerate"
            and len(e.iter.args) >= 1
            and e.targets
            and len(e.targets) == 2
        ):
            self._gen_comprehension_enumerate(e, info)
            return
        iter_t = A.expr_type(e.iter)
        if iter_t.startswith("instance:"):
            self._gen_comprehension_instance_iter(e, info)
            return
        if iter_t not in ("list", "tuple", "any", "int", "set", "str"):
            # Tuples share the list layout; an opaque iterable is a list/tuple
            # at runtime. Dict sources would need different element walks.
            raise NotImplementedError("comprehension iterable must be a list for now")
        res = info.locals_[f"__comp_res_{id(e)}"]
        it = info.locals_[f"__comp_iter_{id(e)}"]
        stop = info.locals_[f"__comp_stop_{id(e)}"]
        idx = info.locals_[f"__comp_idx_{id(e)}"]
        val = info.locals_[f"__comp_val_{id(e)}"]
        var_slot_key = getattr(e, "_comp_var_slot", e.var)
        var = info.locals_[var_slot_key] if not e.targets else 0
        # If the loop var shadows a module global, temporarily inject it into
        # info.locals_ so gen_expr(Name(e.var)) resolves the local slot.
        _shadows_global = getattr(e, "_comp_var_shadows_global", False)
        _saved_var_local = info.locals_.get(e.var) if not e.targets else None
        if _shadows_global and not e.targets:
            info.locals_[e.var] = var

        # result = empty list (cap 4)
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")

        top = self.fresh("comp")
        end = self.fresh("endcomp")

        if iter_t == "str":
            # String comprehension: iterate char by char via strlen + char_at.
            self.gen_expr(e.iter, info)
            self.emitf(f"mov [rbp{it:+d}], rax")
            self._emit_libc_strlen()
            self.emitf(
                f"mov [rbp{stop:+d}], rax",
                f"mov qword [rbp{idx:+d}], 0",
            )
            self.label(top)
            self.emitf(
                f"mov rax, [rbp{idx:+d}]",
                f"cmp rax, [rbp{stop:+d}]",
                f"jge {end}",
                f"mov rax, [rbp{it:+d}]",
                f"mov rbx, [rbp{idx:+d}]",
                "call _runtime_str_char_at",
                f"mov [rbp{var:+d}], rax",
            )
        else:
            # List/tuple/set/any: iterate with list header layout.
            self.gen_expr(e.iter, info)
            self.emitf(
                f"mov [rbp{it:+d}], rax",
                f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
                f"mov [rbp{stop:+d}], rbx",
                f"mov qword [rbp{idx:+d}], 0",
            )
            self.label(top)
            self.emitf(f"mov rax, [rbp{idx:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}")
            self.emitf(
                f"mov rbx, [rbp{it:+d}]",
                f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                f"mov rcx, [rbp{idx:+d}]",
                "mov rax, [rbx+rcx*8]",
            )
            self._emit_comp_target_bind(e.targets, var, info)
        # Optional filter on the outer loop.
        skip = None
        if e.cond is not None:
            skip = self.fresh("comp_skip")
            self._gen_truthy_test(e.cond, info, skip)
        # Direct field access, not getattr(e, "...", []): e is an
        # A.Comprehension here, and all four extra_for_* fields are
        # always-present `list`-typed fields on it. See the matching fix
        # in _cl_walk for why getattr() + len() is unsafe here (it makes
        # len() compile as strlen() on an opaque "any"-typed value).
        ef_iters_g = e.extra_for_iters
        if ef_iters_g:
            ef_vars_g = e.extra_for_vars
            ef_tgts_g = e.extra_for_targets
            ef_conds_g = e.extra_for_conds
            # Nested for-clauses: emit inner loops, then append at innermost.
            # We track (top, end, idx_slot, skip) for each inner loop so we can
            # close them in reverse order after the append.
            inner_loops: list = []
            for ef_n in range(len(ef_iters_g)):
                ef_evar = ef_vars_g[ef_n] if ef_n < len(ef_vars_g) else ""
                ef_emulti = ef_tgts_g[ef_n] if ef_n < len(ef_tgts_g) else []
                ef_iter = ef_iters_g[ef_n]
                ef_cond = ef_conds_g[ef_n] if ef_n < len(ef_conds_g) else None
                ef_it = info.locals_[f"__comp_ef_it_{id(e)}_{ef_n}"]
                ef_stop = info.locals_[f"__comp_ef_stop_{id(e)}_{ef_n}"]
                ef_idx_slot = info.locals_[f"__comp_ef_idx_{id(e)}_{ef_n}"]
                self.gen_expr(ef_iter, info)
                self.emitf(
                    f"mov [rbp{ef_it:+d}], rax",
                    f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
                    f"mov [rbp{ef_stop:+d}], rbx",
                    f"mov qword [rbp{ef_idx_slot:+d}], 0",
                )
                ef_top = self.fresh("ef_comp")
                ef_end = self.fresh("ef_endcomp")
                self.label(ef_top)
                self.emitf(
                    f"mov rax, [rbp{ef_idx_slot:+d}]",
                    f"cmp rax, [rbp{ef_stop:+d}]",
                    f"jge {ef_end}",
                    f"mov rbx, [rbp{ef_it:+d}]",
                    f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                    f"mov rcx, [rbp{ef_idx_slot:+d}]",
                    "mov rax, [rbx+rcx*8]",
                )
                if ef_emulti:
                    self._emit_comp_target_bind(ef_emulti, 0, info)
                elif ef_evar:
                    ef_slot = info.locals_.get(ef_evar, 0)
                    if ef_slot:
                        self.emitf(f"mov [rbp{ef_slot:+d}], rax")
                ef_skip = None
                if ef_cond is not None:
                    ef_skip = self.fresh("ef_comp_skip")
                    self._gen_truthy_test(ef_cond, info, ef_skip)
                inner_loops.append((ef_top, ef_end, ef_idx_slot, ef_skip))
            # Innermost body: append elt.
            self.gen_expr(e.elt, info)
            if e.list_el_type == "float":
                self.emitf("movq rax, xmm0")
            self.emitf(
                f"mov [rbp{val:+d}], rax",
                f"mov rax, [rbp{res:+d}]",
                f"mov rbx, [rbp{val:+d}]",
                "call _runtime_list_append",
            )
            # Close inner loops in reverse order.
            for ef_top, ef_end, ef_idx_slot, ef_skip in reversed(inner_loops):
                if ef_skip is not None:
                    self.label(ef_skip)
                self.emitf(f"inc qword [rbp{ef_idx_slot:+d}]", f"jmp {ef_top}")
                self.label(ef_end)
        else:
            self.gen_expr(e.elt, info)
            if e.list_el_type == "float":
                self.emitf("movq rax, xmm0")
            self.emitf(
                f"mov [rbp{val:+d}], rax",
                f"mov rax, [rbp{res:+d}]",
                f"mov rbx, [rbp{val:+d}]",
                "call _runtime_list_append",
            )
        if skip is not None:
            self.label(skip)
        self.emitf(f"inc qword [rbp{idx:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")  # result value
        # Restore info.locals_ after shadowing a global with the loop var.
        if _shadows_global and not e.targets:
            if _saved_var_local is None:
                info.locals_.pop(e.var, None)
            else:
                info.locals_[e.var] = _saved_var_local

    def _gen_comprehension_instance_iter(self, e: A.Comprehension, info: FuncInfo) -> None:
        """`[elt for x in obj]` where obj is a user class with __iter__/__next__.

        Mirrors _gen_for_instance_iter but appends elt to a result list
        instead of running body statements."""
        iter_t = A.expr_type(e.iter)
        cls_name = iter_t.split(":", 1)[1]
        res = info.locals_[f"__comp_res_{id(e)}"]
        it = info.locals_[f"__comp_iter_{id(e)}"]
        buf_off = info.locals_[f"__comp_inst_buf_{id(e)}"]
        parent_off = info.locals_[f"__comp_inst_parent_{id(e)}"]
        prev_exc_off = info.locals_[f"__comp_inst_prev_exc_{id(e)}"]
        prev_exc_type_off = info.locals_[f"__comp_inst_prev_exc_type_{id(e)}"]
        var_slot_key = getattr(e, "_comp_var_slot", e.var)
        var = info.locals_[var_slot_key] if not e.targets else 0
        _shadows_global = getattr(e, "_comp_var_shadows_global", False)
        _saved_var_local = info.locals_.get(e.var) if not e.targets else None
        if _shadows_global and not e.targets:
            info.locals_[e.var] = var

        # Build empty result list.
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")

        # Evaluate iterable and call __iter__.
        self.gen_expr(e.iter, info)
        self.emitf(f"mov [rbp{it:+d}], rax")
        self.emitf(f"mov {self._arg_reg(0)}, [rbp{it:+d}]")
        self.emit_call(self._method_symbol(cls_name, "__iter__"))
        self.emitf(f"mov [rbp{it:+d}], rax")

        top = self.fresh("comp_inst")
        end = self.fresh("endcomp_inst")
        self.label(top)

        # Save exception state and install handler.
        self.emitf(
            "mov rax, [rel _runtime_exc_msg]",
            f"mov [rbp{prev_exc_off:+d}], rax",
            "mov rax, [rel _runtime_exc_type]",
            f"mov [rbp{prev_exc_type_off:+d}], rax",
            "mov rax, [rel _runtime_handler_top]",
            f"mov [rbp{parent_off:+d}], rax",
        )
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "mov [rel _runtime_handler_top], rax")
        self._emit_call_setjmp(buf_off)
        handler_lbl = self.fresh("comp_inst_handler")
        self.emitf("test eax, eax", f"jnz {handler_lbl}")

        # Normal path: call __next__(iterator).
        self.emitf(f"mov {self._arg_reg(0)}, [rbp{it:+d}]")
        self.emit_call(self._method_symbol(cls_name, "__next__"))
        if not e.targets:
            self.emitf(f"mov [rbp{var:+d}], rax")
        else:
            self._emit_comp_target_bind(e.targets, 0, info)

        # Restore handler chain (normal path).
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]",
            "mov [rel _runtime_handler_top], rax",
            f"mov rax, [rbp{prev_exc_off:+d}]",
            "mov [rel _runtime_exc_msg], rax",
            f"mov rax, [rbp{prev_exc_type_off:+d}]",
            "mov [rel _runtime_exc_type], rax",
        )

        # Optional filter.
        skip = None
        if e.cond is not None:
            skip = self.fresh("comp_inst_skip")
            self._gen_truthy_test(e.cond, info, skip)

        # Append elt to result list.
        self.gen_expr(e.elt, info)
        self.emitf(
            "mov rbx, rax",
            f"mov rax, [rbp{res:+d}]",
            "call _runtime_list_append",
        )

        if skip is not None:
            self.label(skip)
        self.emitf(f"jmp {top}")

        # Handler: restore handler chain, check StopIteration.
        self.label(handler_lbl)
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]",
            "mov [rel _runtime_handler_top], rax",
        )
        self.emitf(
            "mov rax, [rel _runtime_exc_type]",
            "cmp rax, 21",
            f"je {end}",
        )
        self.emitf(
            "mov rax, [rel _runtime_exc_msg]",
            "mov rbx, [rel _runtime_exc_type]",
            "call _runtime_raise",
        )

        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")

        if _shadows_global and not e.targets:
            if _saved_var_local is None:
                info.locals_.pop(e.var, None)
            else:
                info.locals_[e.var] = _saved_var_local

    def _gen_comprehension_enumerate(self, e: A.Comprehension, info: FuncInfo) -> None:
        """`[elt for i, x in enumerate(xs)]` — iterate xs by index, bind the
        running counter to targets[0] and the element to targets[1]."""
        inner = e.iter.args[0]  # type: ignore[union-attr]
        res = info.locals_[f"__comp_res_{id(e)}"]
        it = info.locals_[f"__comp_iter_{id(e)}"]
        stop = info.locals_[f"__comp_stop_{id(e)}"]
        idx = info.locals_[f"__comp_idx_{id(e)}"]
        val = info.locals_[f"__comp_val_{id(e)}"]
        ctr = info.locals_[f"__comp_enum_ctr_{id(e)}"]
        idx_name = e.targets[0] if isinstance(e.targets[0], str) else None
        el_name = e.targets[1] if isinstance(e.targets[1], str) else None
        idx_slot = info.locals_.get(idx_name) if idx_name else None
        el_slot = info.locals_.get(el_name) if el_name else None

        # result = empty list
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")

        # Evaluate inner iterable once; cache pointer, length, counter.
        self.gen_expr(inner, info)
        self.emitf(
            f"mov [rbp{it:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop:+d}], rbx",
            f"mov qword [rbp{idx:+d}], 0",
            f"mov qword [rbp{ctr:+d}], 0",
        )

        top = self.fresh("comp_enum")
        end = self.fresh("endcomp_enum")

        self.label(top)
        self.emitf(f"mov rax, [rbp{idx:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}")
        # Load element.
        self.emitf(
            f"mov rbx, [rbp{it:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{idx:+d}]",
            "mov rax, [rbx+rcx*8]",
        )
        if el_slot is not None:
            self.emitf(f"mov [rbp{el_slot:+d}], rax")
        if idx_slot is not None:
            self.emitf(f"mov rax, [rbp{ctr:+d}]", f"mov [rbp{idx_slot:+d}], rax")

        # Optional filter.
        skip = None
        if e.cond is not None:
            skip = self.fresh("comp_enum_skip")
            self._gen_truthy_test(e.cond, info, skip)

        # Append elt.
        self.gen_expr(e.elt, info)
        if e.list_el_type == "float":
            self.emitf("movq rax, xmm0")
        self.emitf(
            f"mov [rbp{val:+d}], rax",
            f"mov rax, [rbp{res:+d}]",
            f"mov rbx, [rbp{val:+d}]",
            "call _runtime_list_append",
        )
        if skip is not None:
            self.label(skip)
        self.emitf(
            f"inc qword [rbp{idx:+d}]",
            f"inc qword [rbp{ctr:+d}]",
            f"jmp {top}",
        )
        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")

    def _gen_dict_comprehension_enumerate(self, e: A.DictComprehension, info: FuncInfo) -> None:
        """{key: value for i, el in enumerate(xs)} — dict comp with enumerate."""
        inner = e.iter.args[0]  # type: ignore[union-attr]
        res = info.locals_[f"__dcomp_res_{id(e)}"]
        it = info.locals_[f"__dcomp_iter_{id(e)}"]
        stop = info.locals_[f"__dcomp_stop_{id(e)}"]
        idx = info.locals_[f"__dcomp_idx_{id(e)}"]
        key_slot = info.locals_[f"__dcomp_key_{id(e)}"]
        ctr = info.locals_[f"__dcomp_enum_ctr_{id(e)}"]
        idx_name = e.targets[0] if isinstance(e.targets[0], str) else None
        el_name = e.targets[1] if isinstance(e.targets[1], str) else None
        idx_slot = info.locals_.get(idx_name) if idx_name else None
        el_slot = info.locals_.get(el_name) if el_name else None

        # result = empty dict (cap 8)
        cap = 8
        self._emit_malloc(self.DICT_HEADER)
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax")
        self._emit_dict_alloc_order_buf(cap, res)

        self.gen_expr(inner, info)
        self.emitf(
            f"mov [rbp{it:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop:+d}], rbx",
            f"mov qword [rbp{idx:+d}], 0",
            f"mov qword [rbp{ctr:+d}], 0",
        )
        top = self.fresh("dcomp_enum")
        end = self.fresh("enddcomp_enum")
        self.label(top)
        self.emitf(f"mov rax, [rbp{idx:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}")
        self.emitf(
            f"mov rbx, [rbp{it:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{idx:+d}]",
            "mov rax, [rbx+rcx*8]",
        )
        if el_slot is not None:
            self.emitf(f"mov [rbp{el_slot:+d}], rax")
        if idx_slot is not None:
            self.emitf(f"mov rax, [rbp{ctr:+d}]", f"mov [rbp{idx_slot:+d}], rax")
        skip = None
        if e.cond is not None:
            skip = self.fresh("dcomp_enum_skip")
            self._gen_truthy_test(e.cond, info, skip)
        self.gen_expr(e.key, info)
        self.emitf(f"mov [rbp{key_slot:+d}], rax")
        self.gen_expr(e.value, info)
        if e.value_type == "float":
            self.emitf("movq rax, xmm0")
        self.emitf(
            "mov rcx, rax",
            f"mov rbx, [rbp{key_slot:+d}]",
            f"mov rax, [rbp{res:+d}]",
            "call _runtime_dict_set",
        )
        if skip is not None:
            self.label(skip)
        self.emitf(
            f"inc qword [rbp{idx:+d}]",
            f"inc qword [rbp{ctr:+d}]",
            f"jmp {top}",
        )
        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")

    def _gen_dict_comprehension_zip(self, e: A.DictComprehension, info: FuncInfo) -> None:
        """{key: val for a, b in zip(A, B)} — walks N lists in lockstep."""
        zip_call = e.iter
        nz = len(zip_call.args)
        res = info.locals_[f"__dcomp_res_{id(e)}"]
        key_slot = info.locals_[f"__dcomp_key_{id(e)}"]
        stop = info.locals_[f"__dcomp_zip_stop_{id(e)}"]
        i_off = info.locals_[f"__dcomp_zip_i_{id(e)}"]
        iter_offs = [info.locals_[f"__dcomp_zip_{k}_{id(e)}"] for k in range(nz)]

        # result = empty dict (cap 8)
        cap = 8
        self._emit_malloc(self.DICT_HEADER)
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax")
        self._emit_dict_alloc_order_buf(cap, res)

        # Cache all iterable pointers and compute min length.
        for k, (ze, it_off) in enumerate(zip(zip_call.args, iter_offs)):
            self.gen_expr(ze, info)
            self.emitf(f"mov [rbp{it_off:+d}], rax")
        self.emitf(
            f"mov rax, [rbp{iter_offs[0]:+d}]",
            f"mov rax, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop:+d}], rax",
        )
        for k in range(1, nz):
            self.emitf(
                f"mov rax, [rbp{stop:+d}]",
                f"mov rbx, [rbp{iter_offs[k]:+d}]",
                f"mov rbx, [rbx+{self.LIST_LEN_OFF}]",
                "cmp rax, rbx",
                "cmovg rax, rbx",
                f"mov [rbp{stop:+d}], rax",
            )
        self.emitf(f"mov qword [rbp{i_off:+d}], 0")

        top = self.fresh("dcomp_zip")
        end = self.fresh("enddcomp_zip")
        self.label(top)
        self.emitf(f"mov rax, [rbp{i_off:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}")

        # Bind each target name to the k-th iterable's current element.
        for k, (it_off, tname) in enumerate(zip(iter_offs, e.targets)):
            if isinstance(tname, str):
                v_off = info.locals_.get(tname, 0)
                if v_off:
                    self.emitf(
                        f"mov rbx, [rbp{it_off:+d}]",
                        f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                        f"mov rcx, [rbp{i_off:+d}]",
                        "mov rax, [rbx+rcx*8]",
                        f"mov [rbp{v_off:+d}], rax",
                    )

        skip = None
        if e.cond is not None:
            skip = self.fresh("dcomp_zip_skip")
            self._gen_truthy_test(e.cond, info, skip)
        self.gen_expr(e.key, info)
        self.emitf(f"mov [rbp{key_slot:+d}], rax")
        self.gen_expr(e.value, info)
        if e.value_type == "float":
            self.emitf("movq rax, xmm0")
        self.emitf(
            "mov rcx, rax",
            f"mov rbx, [rbp{key_slot:+d}]",
            f"mov rax, [rbp{res:+d}]",
            "call _runtime_dict_set",
        )
        if skip is not None:
            self.label(skip)
        self.emitf(f"inc qword [rbp{i_off:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")

    def _gen_dict_comprehension(self, e: A.DictComprehension, info: FuncInfo) -> None:
        """{key: value for var in iter (if cond)} -> build an empty dict, iterate
        the (list-typed) iterable, and insert key->value for each element that
        passes the filter. Result dict pointer left in rax.

        Mirrors `_gen_comprehension` for the loop, and `_gen_dict_lit` for the
        empty-dict allocation and per-pair `_runtime_dict_set` inserts. Parks all
        intermediates in frame slots — no push/pop across calls."""
        # enumerate(xs) special case: {k: i for i, k in enumerate(xs)}
        if (
            isinstance(e.iter, A.Call)
            and getattr(e.iter, "func", None) == "enumerate"
            and len(e.iter.args) >= 1
            and e.targets
            and len(e.targets) == 2
        ):
            self._gen_dict_comprehension_enumerate(e, info)
            return
        # zip(A, B, ...) special case: iterate in lock-step (no temporary list).
        if (
            isinstance(e.iter, A.Call)
            and getattr(e.iter, "func", None) == "zip"
            and e.targets
            and len(e.targets) == len(e.iter.args)
        ):
            self._gen_dict_comprehension_zip(e, info)
            return
        if A.expr_type(e.iter) not in ("list", "tuple", "any"):
            raise NotImplementedError(
                "dict comprehension iterable must be a list for now"
            )
        res = info.locals_[f"__dcomp_res_{id(e)}"]
        it = info.locals_[f"__dcomp_iter_{id(e)}"]
        stop = info.locals_[f"__dcomp_stop_{id(e)}"]
        idx = info.locals_[f"__dcomp_idx_{id(e)}"]
        key_slot = info.locals_[f"__dcomp_key_{id(e)}"]
        var_slot_key = getattr(e, "_comp_var_slot", e.var)
        var = info.locals_[var_slot_key] if not e.targets else 0

        # result = empty dict (cap 8), mirroring _gen_dict_lit's n=0 case.
        cap = 8
        self._emit_malloc(self.DICT_HEADER)
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax")
        self._emit_dict_alloc_order_buf(cap, res)

        # Iterate the source list.
        self.gen_expr(e.iter, info)
        self.emitf(
            f"mov [rbp{it:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop:+d}], rbx",
            f"mov qword [rbp{idx:+d}], 0",
        )
        top = self.fresh("dcomp")
        end = self.fresh("enddcomp")
        self.label(top)
        self.emitf(f"mov rax, [rbp{idx:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}")
        self.emitf(
            f"mov rbx, [rbp{it:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{idx:+d}]",
            "mov rax, [rbx+rcx*8]",
        )
        self._emit_comp_target_bind(e.targets, var, info)
        # Optional filter.
        skip = None
        if e.cond is not None:
            skip = self.fresh("dcomp_skip")
            self._gen_truthy_test(e.cond, info, skip)
        # Evaluate key (a str ptr) into the scratch slot, then value, then insert.
        self.gen_expr(e.key, info)
        self.emitf(f"mov [rbp{key_slot:+d}], rax")
        self.gen_expr(e.value, info)
        if e.value_type == "float":
            self.emitf("movq rax, xmm0")
        self.emitf(
            "mov rcx, rax",  # rcx = value
            f"mov rbx, [rbp{key_slot:+d}]",  # rbx = key ptr
            f"mov rax, [rbp{res:+d}]",  # rax = dict header
            "call _runtime_dict_set",
        )
        if skip is not None:
            self.label(skip)
        self.emitf(f"inc qword [rbp{idx:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")  # result value

    def _gen_tuple_lit(self, e: A.TupleLit, info: FuncInfo) -> None:
        """(a, b, c) -> a fresh heap value in the list layout.

        Identical to `_gen_list_lit` except the per-slot store kind comes from
        the tuple's heterogeneous `elem_types` rather than one shared element
        type. cap == len == n (tuples never grow); an empty tuple still gets a
        1-slot buffer so the malloc size is non-zero.
        """
        slot_off = info.locals_[f"__tuplelit_{id(e)}"]
        n = len(e.elems)
        cap = max(n, 1)
        self._emit_malloc(self.LIST_HEADER)  # rax = header
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], {n}",
            f"mov [rbp{slot_off:+d}], rax",
        )
        self._emit_malloc(cap * 8)  # rax = buffer
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]",
            f"mov [rbx+{self.LIST_BUF_OFF}], rax",
        )
        for i, el in enumerate(e.elems):
            self.gen_expr(el, info)  # rax / xmm0 = value (may call!)
            self.emitf(
                f"mov rbx, [rbp{slot_off:+d}]", f"mov rcx, [rbx+{self.LIST_BUF_OFF}]"
            )
            el_t = e.elem_types[i] if i < len(e.elem_types) else "int"
            if el_t == "float":
                self.emitf(f"movsd [rcx+{i * 8}], xmm0")
            else:
                self.emitf(f"mov [rcx+{i * 8}], rax")
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    def _gen_subscript(self, e: A.Subscript, info: FuncInfo) -> None:
        if isinstance(e.index, A.Slice):
            obj_t_sl = A.expr_type(e.obj)
            if obj_t_sl == "list":
                self._gen_list_slice(e, info)
            else:
                self._gen_str_slice(e, info)
            return
        # Instance `__getitem__`: dispatch to `obj.__getitem__(index)`.
        if getattr(e, "_getitem_class", None) is not None:
            cls_name = e._getitem_class  # type: ignore[attr-defined]
            owner = self._resolve_method_owner(cls_name, "__getitem__")
            if owner is not None:
                recv_slot = info.locals_[f"__gi_self_{id(e)}"]
                arg_slot = info.locals_[f"__gi_arg_{id(e)}"]
                # Evaluate receiver then index into their slots.
                self.gen_expr(e.obj, info)
                self.emitf(f"mov [rbp{recv_slot:+d}], rax")
                self.gen_expr(e.index, info)
                self.emitf(f"mov [rbp{arg_slot:+d}], rax")
                # Load self into arg0, index into arg1.
                self.emitf(
                    f"mov {self._arg_reg(0)}, [rbp{recv_slot:+d}]",
                    f"mov {self._arg_reg(1)}, [rbp{arg_slot:+d}]",
                )
                self.emit_call(self._method_symbol(owner, "__getitem__"))
            else:
                self.emitf("xor rax, rax")
            return
        obj_t = A.expr_type(e.obj)
        # An opaque receiver indexed by a STRING is a dict lookup — a str key
        # can never be a list index, and treating the pointer as an index
        # walks off the buffer. Int-indexed opaque values fall through to the
        # list path (the common case for untracked lists/tuples).
        if obj_t == "any" and A.expr_type(e.index) == "str":
            obj_t = "dict"
        if obj_t == "dict":
            # Evaluate key (rax = str ptr), spill to a frame slot (push/pop
            # would be clobbered by callee shadow-space stores if the dict
            # expression itself calls — e.g. a dict literal), evaluate the
            # dict header, call the runtime helper.
            key_slot = info.locals_[f"__subidx_{id(e)}"]
            self.gen_expr(e.index, info)
            self.emitf(f"mov [rbp{key_slot:+d}], rax")
            self.gen_expr(e.obj, info)  # rax = header
            self.emitf(
                f"mov rbx, [rbp{key_slot:+d}]",  # rbx = key ptr
                "call _runtime_dict_get",
            )  # raises if missing
            if e.inferred_type == "float":
                self.emitf("movq xmm0, rax")
            return
        if obj_t == "str":
            slot_off = info.locals_[f"__stridx_{id(e)}"]
            self.gen_expr(e.obj, info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
            self.gen_expr(e.index, info)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{slot_off:+d}]",
                "call _runtime_str_char_at",
            )
            return
        idx_slot = info.locals_[f"__subidx_{id(e)}"]
        self.gen_expr(e.index, info)
        self.emitf(f"mov [rbp{idx_slot:+d}], rax")
        self.gen_expr(e.obj, info)  # rax = header
        # Negative index: rcx += len. We need the header in rax to read len,
        # so do the wrap BEFORE switching rax to the buffer pointer.
        pos = self.fresh("idx_pos")
        self.emitf(
            f"mov rcx, [rbp{idx_slot:+d}]",  # rcx = index
            "test rcx, rcx",
            f"jns {pos}",
            f"add rcx, [rax+{self.LIST_LEN_OFF}]",
        )
        self.label(pos)
        # Bounds check: 0 <= rcx < len.  rax = header, rcx = normalised index.
        oob = self.fresh("list_oob")
        self.emitf(
            "test rcx, rcx",
            f"js {oob}",
            f"cmp rcx, [rax+{self.LIST_LEN_OFF}]",
            f"jge {oob}",
        )
        self.emitf(f"mov rax, [rax+{self.LIST_BUF_OFF}]")
        # If this list holds floats, drop the 8-byte slot into xmm0; otherwise
        # keep it in rax (int / str-ptr both 8-byte integers).
        if e.inferred_type == "float":
            self.emitf("movsd xmm0, [rax+rcx*8]")
        else:
            self.emitf("mov rax, [rax+rcx*8]")
        after = self.fresh("list_after")
        self.emitf(f"jmp {after}")
        self.label(oob)
        self.emitf(
            "lea rax, [rel _runtime_list_oob_msg]",
            f"mov rbx, {self._exc_type_id('IndexError')}",
            "call _runtime_raise",
        )
        self.label(after)

    def _gen_list_call(self, e: A.Call, info: FuncInfo) -> None:
        """`list(x)` -> a fresh list holding x's elements (shallow copy).

        For a list or tuple source this is exactly the full slice `x[:]`:
        lists and tuples share the same heap layout, so a whole-buffer copy
        produces an independent list. `_runtime_list_slice` with the
        MIN/MAX sentinels does precisely that. Sema has already verified the
        argument is a list/tuple/str/dict/any and stamped `list_el_type`.
        """
        arg0 = e.args[0]
        # list(filter(pred, xs))
        if (
            isinstance(arg0, A.Call)
            and arg0.func == "filter"
            and len(arg0.args) == 2
        ):
            self._gen_list_filter(e, arg0, info)
            return
        # list(map(lambda x: expr, xs))
        if (
            isinstance(arg0, A.Call)
            and arg0.func == "map"
            and len(arg0.args) == 2
            and isinstance(arg0.args[0], A.Lambda)
        ):
            self._gen_list_map(e, arg0, info)
            return
        # list(zip(A, B, ...))
        if isinstance(arg0, A.Call) and arg0.func == "zip":
            self._gen_list_zip(e, arg0, info)
            return
        src_t = A.expr_type(arg0)
        if src_t in ("list", "tuple", "any"):
            SENTINEL_MIN = "0x8000000000000000"
            SENTINEL_MAX = "0x7fffffffffffffff"
            self.gen_expr(arg0, info)  # rax = source list/tuple header
            self.emitf(
                f"mov rbx, {SENTINEL_MIN}",
                f"mov rcx, {SENTINEL_MAX}",
                "call _runtime_list_slice",
            )
            return
        # str -> list[str] of single chars, dict -> list of keys: distinct
        # element-producing walks, implemented when a caller needs them.
        raise NotImplementedError(f"list() from {src_t} source")

    def _gen_list_filter(self, outer: A.Call, filter_call: A.Call, info: FuncInfo) -> None:
        """Lower `list(filter(pred, xs))`.
        pred=None → truthy filter; pred=Lambda → inline lambda body as condition."""
        pred = filter_call.args[0]
        xs_expr = filter_call.args[1]
        is_lambda = isinstance(pred, A.Lambda)
        if not A.is_none_expr(pred) and not is_lambda:
            raise NotImplementedError("filter() with a function predicate is not yet supported")
        res_slot = info.locals_[f"__listcall_res_{id(outer)}"]
        it_slot = info.locals_[f"__listcall_it_{id(outer)}"]
        stop_slot = info.locals_[f"__listcall_stop_{id(outer)}"]
        idx_slot = info.locals_[f"__listcall_idx_{id(outer)}"]
        val_slot = info.locals_[f"__listcall_val_{id(outer)}"]
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res_slot:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{res_slot:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")
        self.gen_expr(xs_expr, info)
        self.emitf(
            f"mov [rbp{it_slot:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop_slot:+d}], rbx",
            f"mov qword [rbp{idx_slot:+d}], 0",
        )
        top = self.fresh("lf_top")
        end = self.fresh("lf_end")
        skip = self.fresh("lf_skip")
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{idx_slot:+d}]",
            f"cmp rax, [rbp{stop_slot:+d}]",
            f"jge {end}",
            f"mov rbx, [rbp{it_slot:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rax, [rbx+rax*8]",
            f"mov [rbp{val_slot:+d}], rax",
        )
        if is_lambda:
            # Bind lambda parameter, eval body as condition.
            lam: A.Lambda = pred  # type: ignore[assignment]
            if lam.params:
                p_slot = info.locals_[lam.params[0]]
                self.emitf(f"mov [rbp{p_slot:+d}], rax")
            self.gen_expr(lam.body, info)
            self.emitf("test rax, rax", f"jz {skip}")
        else:
            self.emitf("test rax, rax", f"jz {skip}")
        self.emitf(
            f"mov rax, [rbp{res_slot:+d}]",
            f"mov rbx, [rbp{val_slot:+d}]",
            "call _runtime_list_append",
            f"mov [rbp{res_slot:+d}], rax",
        )
        self.label(skip)
        self.emitf(f"inc qword [rbp{idx_slot:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res_slot:+d}]")

    def _gen_list_map(self, outer: A.Call, map_call: A.Call, info: FuncInfo) -> None:
        """Lower `list(map(lambda x: expr, xs))` to a map loop."""
        lam: A.Lambda = map_call.args[0]  # type: ignore[assignment]
        xs_expr = map_call.args[1]
        res_slot = info.locals_[f"__listcall_res_{id(outer)}"]
        it_slot = info.locals_[f"__listcall_it_{id(outer)}"]
        stop_slot = info.locals_[f"__listcall_stop_{id(outer)}"]
        idx_slot = info.locals_[f"__listcall_idx_{id(outer)}"]
        val_slot = info.locals_[f"__listcall_val_{id(outer)}"]
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res_slot:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{res_slot:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")
        self.gen_expr(xs_expr, info)
        self.emitf(
            f"mov [rbp{it_slot:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop_slot:+d}], rbx",
            f"mov qword [rbp{idx_slot:+d}], 0",
        )
        top = self.fresh("lm_top")
        end = self.fresh("lm_end")
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{idx_slot:+d}]",
            f"cmp rax, [rbp{stop_slot:+d}]",
            f"jge {end}",
            f"mov rbx, [rbp{it_slot:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rax, [rbx+rax*8]",
        )
        if lam.params:
            p_slot = info.locals_[lam.params[0]]
            self.emitf(f"mov [rbp{p_slot:+d}], rax")
        # Evaluate lambda body → result value.
        self.gen_expr(lam.body, info)
        self.emitf(
            f"mov [rbp{val_slot:+d}], rax",
            f"mov rax, [rbp{res_slot:+d}]",
            f"mov rbx, [rbp{val_slot:+d}]",
            "call _runtime_list_append",
            f"mov [rbp{res_slot:+d}], rax",
        )
        self.emitf(f"inc qword [rbp{idx_slot:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res_slot:+d}]")

    def _gen_list_zip(self, outer: A.Call, zip_call: A.Call, info: FuncInfo) -> None:
        """Lower `list(zip(A, B, ...))` to a tuple-building loop.

        Each iteration allocates a fresh N-element tuple header+buffer, fills
        its slots from the parallel iterables, then appends the tuple pointer
        to the result list.  All volatile state lives in frame slots so that
        inner malloc/call calls cannot clobber it.
        """
        n = len(zip_call.args)
        res_slot = info.locals_[f"__lzip_res_{id(outer)}"]
        stop_slot = info.locals_[f"__lzip_stop_{id(outer)}"]
        idx_slot = info.locals_[f"__lzip_idx_{id(outer)}"]
        tup_slot = info.locals_[f"__lzip_tup_{id(outer)}"]
        it_slots = [info.locals_[f"__lzip_it{k}_{id(outer)}"] for k in range(n)]

        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res_slot:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{res_slot:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")

        # Evaluate each iterable, cache pointer; compute min length.
        for k, ze in enumerate(zip_call.args):
            self.gen_expr(ze, info)
            self.emitf(f"mov [rbp{it_slots[k]:+d}], rax")

        self.emitf(
            f"mov rax, [rbp{it_slots[0]:+d}]",
            f"mov rax, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop_slot:+d}], rax",
        )
        for k in range(1, n):
            self.emitf(
                f"mov rax, [rbp{stop_slot:+d}]",
                f"mov rbx, [rbp{it_slots[k]:+d}]",
                f"mov rbx, [rbx+{self.LIST_LEN_OFF}]",
                "cmp rax, rbx",
                "cmovg rax, rbx",
                f"mov [rbp{stop_slot:+d}], rax",
            )
        self.emitf(f"mov qword [rbp{idx_slot:+d}], 0")

        top = self.fresh("lzip_top")
        end = self.fresh("lzip_end")
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{idx_slot:+d}]",
            f"cmp rax, [rbp{stop_slot:+d}]",
            f"jge {end}",
        )

        # Allocate tuple header; save to frame slot across the buffer malloc.
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {n}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], {n}",
            f"mov [rbp{tup_slot:+d}], rax",
        )
        self._emit_malloc(n * 8)
        # rax = tuple buffer; wire it into the tuple header.
        self.emitf(
            f"mov rbx, [rbp{tup_slot:+d}]",
            f"mov [rbx+{self.LIST_BUF_OFF}], rax",
        )

        # Fill tuple buffer: for each k, load it_k.buf[idx] into buf[k].
        # rax = tuple buffer.
        for k in range(n):
            self.emitf(
                f"mov rcx, [rbp{it_slots[k]:+d}]",
                f"mov rcx, [rcx+{self.LIST_BUF_OFF}]",
                f"mov rdx, [rbp{idx_slot:+d}]",
                "mov rdx, [rcx+rdx*8]",
                f"mov [rax+{k*8}], rdx",
            )
        # Append tuple header to result list.
        self.emitf(
            f"mov rbx, [rbp{tup_slot:+d}]",
            f"mov rax, [rbp{res_slot:+d}]",
            "call _runtime_list_append",
            f"mov [rbp{res_slot:+d}], rax",
        )
        self.emitf(f"inc qword [rbp{idx_slot:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res_slot:+d}]")

    def _gen_set_setop(
        self, obj_expr, other_expr, method: str, slot_id: int, info: FuncInfo
    ) -> None:
        """`a.union(b)` / `a.intersection(b)` / `a.difference(b)`, and the
        `|` / `&` / `-` set operators that lower to the same thing. Builds a
        fresh set (dict) header + slot buffer and either merges both operands
        in (union) or filters `obj`'s members by membership in `other`
        (intersection/difference). `slot_id` namespaces the scratch frame
        slots so BinOp and MethodCall call sites don't collide.
        """
        other_slot = info.locals_[f"__sm_other_{slot_id}"]
        new_slot = info.locals_[f"__sm_new_{slot_id}"]
        keys_slot = info.locals_[f"__sm_keys_{slot_id}"]
        idx_slot = info.locals_[f"__sm_idx_{slot_id}"]
        key_slot = info.locals_[f"__sm_key_{slot_id}"]
        NEW_CAP = 8
        # Evaluate other and self, save both to frame slots.
        self.gen_expr(other_expr, info)
        self.emitf(f"mov [rbp{other_slot:+d}], rax")
        self.gen_expr(obj_expr, info)  # rax = self
        self.emitf(f"mov [rbp{keys_slot:+d}], rax")  # keys_slot = self (temporarily)
        # Allocate a new empty set (dict) header + slot buffer.
        self.emitf(f"mov rbx, {self.DICT_HEADER}", "call _runtime_zalloc")
        self.emitf(f"mov [rbp{new_slot:+d}], rax")
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {NEW_CAP}",
            f"mov rbx, {NEW_CAP * self.DICT_SLOT_SIZE}",
            "call _runtime_zalloc",
            f"mov rcx, [rbp{new_slot:+d}]",
            f"mov [rcx+{self.DICT_BUF_OFF}], rax",
        )
        self._emit_dict_alloc_order_buf(NEW_CAP, new_slot)
        if method == "union":
            # new.update(self); new.update(other)
            self.emitf(
                f"mov rax, [rbp{new_slot:+d}]",
                f"mov rbx, [rbp{keys_slot:+d}]",
                "call _runtime_dict_update",
                f"mov rax, [rbp{new_slot:+d}]",
                f"mov rbx, [rbp{other_slot:+d}]",
                "call _runtime_dict_update",
                f"mov rax, [rbp{new_slot:+d}]",
            )
            return
        # intersection / difference: iterate self keys.
        self.emitf(
            f"mov rax, [rbp{keys_slot:+d}]",
            "call _runtime_dict_keys",
            f"mov [rbp{keys_slot:+d}], rax",  # now keys_slot = key list
            f"mov qword [rbp{idx_slot:+d}], 0",
        )
        loop = self.fresh("setop_loop")
        skip = self.fresh("setop_skip")
        end = self.fresh("setop_end")
        self.label(loop)
        self.emitf(
            f"mov rax, [rbp{keys_slot:+d}]",
            f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
            f"cmp [rbp{idx_slot:+d}], rcx",
            f"jge {end}",
            f"mov rax, [rax+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{idx_slot:+d}]",
            "mov rax, [rax+rcx*8]",
            f"mov [rbp{key_slot:+d}], rax",
            "mov rbx, rax",
            f"mov rax, [rbp{other_slot:+d}]",
            "call _runtime_dict_contains",
        )
        if method == "intersection":
            self.emitf("test rax, rax", f"jz {skip}")
        else:
            self.emitf("test rax, rax", f"jnz {skip}")
        self.emitf(
            f"mov rax, [rbp{new_slot:+d}]",
            f"mov rbx, [rbp{key_slot:+d}]",
            "mov rcx, 1",
            "call _runtime_dict_set",
        )
        self.label(skip)
        self.emitf(f"inc qword [rbp{idx_slot:+d}]", f"jmp {loop}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{new_slot:+d}]")

    def _emit_empty_set(self, slot_off: int) -> None:
        """Allocate an empty set (= empty dict, cap 8) into rax, parking the
        header in `slot_off`. Shared by set()/frozenset() and set literals."""
        cap = 8
        self._emit_malloc(self.DICT_HEADER)  # rax = header
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{slot_off:+d}], rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax"
        )
        self._emit_dict_alloc_order_buf(cap, slot_off)

    def _gen_set_call(self, e: A.Call, info: FuncInfo) -> None:
        """`set(x)` / `frozenset(x)` -> a set (dict keyed by members).

        - No argument: an empty set.
        - A set/dict argument: returned as-is (sets are dicts; membership is
          all that's modelled, so sharing the backing store is fine here).
        - A list/tuple argument: build a fresh set by inserting each element
          as a key (dummy value 1), like `_gen_set_lit` but over a runtime
          iterable.
        """
        if not e.args:
            res = info.locals_[f"__setcall_res_{id(e)}"]
            self._emit_empty_set(res)
            self.emitf(f"mov rax, [rbp{res:+d}]")
            return
        at = A.expr_type(e.args[0])
        if at in ("set", "dict", "any"):
            # Already a dict-backed value — hand it straight back.
            self.gen_expr(e.args[0], info)
            return
        # Build from a list/tuple: iterate and insert each element.
        res = info.locals_[f"__setcall_res_{id(e)}"]
        it = info.locals_[f"__setcall_it_{id(e)}"]
        stop = info.locals_[f"__setcall_stop_{id(e)}"]
        idx = info.locals_[f"__setcall_idx_{id(e)}"]
        key_slot = info.locals_[f"__setcall_key_{id(e)}"]

        self._emit_empty_set(res)
        self.gen_expr(e.args[0], info)  # rax = source list/tuple header
        self.emitf(
            f"mov [rbp{it:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop:+d}], rbx",
            f"mov qword [rbp{idx:+d}], 0",
        )
        src_arg = e.args[0]
        if A.expr_type(src_arg) == "tuple":
            ets = [t for t in A.tuple_element_types(src_arg) if t != "any"]
            src_el_t = ets[0] if ets else "int"
        elif isinstance(src_arg, A.ListLit):
            src_el_t = src_arg.el_type
        elif isinstance(src_arg, (A.Comprehension, A.Name)):
            src_el_t = getattr(src_arg, "list_el_type", "int")
        else:
            src_el_t = "int"
        top = self.fresh("setcall")
        end = self.fresh("endsetcall")
        self.label(top)
        self.emitf(f"mov rax, [rbp{idx:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}")
        self.emitf(
            f"mov rbx, [rbp{it:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{idx:+d}]",
            "mov rax, [rbx+rcx*8]",  # element (key ptr)
        )
        if src_el_t == "int":
            self._emit_int_to_str()
            self.emitf("call _runtime_str_concat_dup")
        self.emitf(
            f"mov [rbp{key_slot:+d}], rax",
            "mov rcx, 1",  # dummy value
            f"mov rbx, [rbp{key_slot:+d}]",
            f"mov rax, [rbp{res:+d}]",
            "call _runtime_dict_set",
        )
        self.emitf(f"inc qword [rbp{idx:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{res:+d}]")

    def _gen_list_slice(self, e: A.Subscript, info: FuncInfo) -> None:
        """`xs[start:stop[:step]]` -> _runtime_list_slice or _runtime_list_slice_step."""
        sl: A.Slice = e.index  # type: ignore[assignment]
        obj_slot = info.locals_[f"__lstsl_obj_{id(e)}"]
        start_slot = info.locals_[f"__lstsl_start_{id(e)}"]
        SENTINEL_MIN = "0x8000000000000000"
        SENTINEL_MAX = "0x7fffffffffffffff"

        self.gen_expr(e.obj, info)
        self.emitf(f"mov [rbp{obj_slot:+d}], rax")
        if sl.start is None:
            self.emitf(f"mov rax, {SENTINEL_MIN}", f"mov [rbp{start_slot:+d}], rax")
        else:
            self.gen_expr(sl.start, info)
            self.emitf(f"mov [rbp{start_slot:+d}], rax")
        if sl.step is not None:
            step_slot = info.locals_[f"__lstsl_step_{id(e)}"]
            if sl.stop is None:
                self.emitf(f"mov rax, {SENTINEL_MAX}", f"mov [rbp{step_slot:+d}], rax")
            else:
                self.gen_expr(sl.stop, info)
                self.emitf(f"mov [rbp{step_slot:+d}], rax")
            self.gen_expr(sl.step, info)
            self.emitf(
                f"mov rdx, rax",
                f"mov rax, [rbp{obj_slot:+d}]",
                f"mov rbx, [rbp{start_slot:+d}]",
                f"mov rcx, [rbp{step_slot:+d}]",
                "call _runtime_list_slice_step",
            )
        else:
            if sl.stop is None:
                self.emitf(f"mov rcx, {SENTINEL_MAX}")
            else:
                self.gen_expr(sl.stop, info)
                self.emitf("mov rcx, rax")
            self.emitf(
                f"mov rax, [rbp{obj_slot:+d}]",
                f"mov rbx, [rbp{start_slot:+d}]",
                "call _runtime_list_slice",
            )

    def _gen_str_slice(self, e: A.Subscript, info: FuncInfo) -> None:
        """s[start:stop[:step]] dispatch.

        Without step we use the simpler `_runtime_str_slice`. With step, we
        evaluate s/start/stop/step into spill slots and call the general
        `_runtime_str_slice_step` helper. Missing endpoints are passed as
        sentinels (INT64_MIN for start, INT64_MIN/MAX for stop depending on
        step sign) and the runtime fills in defaults.
        """
        sl: A.Slice = e.index  # type: ignore[assignment]
        if sl.step is not None:
            self._gen_str_slice_step(e, sl, info)
            return
        obj_slot = info.locals_[f"__strsl_obj_{id(e)}"]
        start_slot = info.locals_[f"__strsl_start_{id(e)}"]
        # 1. obj into its slot
        self.gen_expr(e.obj, info)
        self.emitf(f"mov [rbp{obj_slot:+d}], rax")
        # 2. start (default 0) into its slot
        if sl.start is None:
            self.emitf(f"mov qword [rbp{start_slot:+d}], 0")
        else:
            self.gen_expr(sl.start, info)
            self.emitf(f"mov [rbp{start_slot:+d}], rax")
        # 3. stop (default INT64_MAX) -> rcx, but we evaluate after spilling
        # so the call sees the right registers.
        if sl.stop is None:
            self.emitf("mov rcx, 0x7fffffffffffffff")
        else:
            self.gen_expr(sl.stop, info)
            self.emitf("mov rcx, rax")
        self.emitf(
            f"mov rax, [rbp{obj_slot:+d}]",
            f"mov rbx, [rbp{start_slot:+d}]",
            "call _runtime_str_slice",
        )

    def _gen_str_slice_step(self, e: A.Subscript, sl: A.Slice, info: FuncInfo) -> None:
        """`s[start:stop:step]` with step explicitly given.

        Spills s, start, stop, step into pre-reserved frame slots; the
        runtime sees the actual values (or sentinels for missing endpoints).
        """
        obj_slot = info.locals_[f"__strsl_obj_{id(e)}"]
        start_slot = info.locals_[f"__strsl_start_{id(e)}"]
        stop_slot = info.locals_[f"__strsl_stop_{id(e)}"]
        step_slot = info.locals_[f"__strsl_step_{id(e)}"]
        SENTINEL_MIN = "0x8000000000000000"
        SENTINEL_MAX = "0x7fffffffffffffff"  # noqa: F841

        # Eval obj.
        self.gen_expr(e.obj, info)
        self.emitf(f"mov [rbp{obj_slot:+d}], rax")
        # Eval step first because endpoint defaults depend on its sign.
        # But we don't know its value at compile time; let the runtime
        # decide. Just spill values (sentinel when missing).
        if sl.step is None:
            self.emitf(f"mov qword [rbp{step_slot:+d}], 1")
        else:
            self.gen_expr(sl.step, info)
            self.emitf(f"mov [rbp{step_slot:+d}], rax")
        if sl.start is None:
            self.emitf(f"mov rax, {SENTINEL_MIN}", f"mov [rbp{start_slot:+d}], rax")
        else:
            self.gen_expr(sl.start, info)
            self.emitf(f"mov [rbp{start_slot:+d}], rax")
        if sl.stop is None:
            # Use INT64_MIN as the universal sentinel; the runtime sees the
            # step's sign and picks the right default. Actually positive step
            # could use either MIN or MAX safely if normalized to "out of
            # range -> default". We pass MIN here and the helper interprets
            # it as "missing".
            self.emitf(f"mov rax, {SENTINEL_MIN}", f"mov [rbp{stop_slot:+d}], rax")
        else:
            self.gen_expr(sl.stop, info)
            self.emitf(f"mov [rbp{stop_slot:+d}], rax")
        # Load registers and call.
        self.emitf(
            f"mov rax, [rbp{obj_slot:+d}]",
            f"mov rbx, [rbp{start_slot:+d}]",
            f"mov rcx, [rbp{stop_slot:+d}]",
            f"mov r8, [rbp{step_slot:+d}]",
            "call _runtime_str_slice_step",
        )

    def _gen_dict_lit(self, e: A.DictLit, info: FuncInfo) -> None:
        # Same pattern as list literal: stable header lives in a frame slot
        # across the per-pair inserts so growth can replace the slot buffer.
        slot_off = info.locals_[f"__dictlit_{id(e)}"]
        # Initial slot count: smallest power of 2 >= max(8, 2*len).
        n = len(e.keys)
        cap = 8
        while cap < n * 2:
            cap *= 2
        # malloc(header) then malloc(cap * SLOT_SIZE) then zero the slots.
        self._emit_malloc(self.DICT_HEADER)  # rax = header
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{slot_off:+d}], rax",
        )
        # Allocate zero-filled slot buffer via calloc-style: malloc + memset.
        # Easiest: malloc the buffer, store the pointer, then clear it with
        # a small loop in the runtime helper. We just call a runtime helper.
        self.emitf(
            f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc"
        )  # rax = zero-filled ptr
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax"
        )
        self._emit_dict_alloc_order_buf(cap, slot_off)
        # Insert each (key, value) pair via the runtime set helper. We can't
        # use `push rax / pop rbx` to stash the key across the value-eval:
        # if v_expr calls anything (e.g. a constructor), the callee's MS x64
        # shadow-space store at [rsp..rsp+31] would clobber the pushed key.
        # Use the pre-reserved frame slot instead.
        key_slot = info.locals_[f"__dictlit_key_{id(e)}"]
        for k_expr, v_expr in zip(e.keys, e.values):
            if isinstance(k_expr, A.Name) and k_expr.name == "**":
                # `**other` (PEP 448): merge other's entries in, in source
                # order, so later entries (spreads or explicit keys) win on
                # key conflicts.
                self.gen_expr(v_expr, info)  # rax = other dict header
                self.emitf(
                    "mov rbx, rax",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "call _runtime_dict_update",
                )
                continue
            self.gen_expr(k_expr, info)  # rax = key ptr
            self.emitf(f"mov [rbp{key_slot:+d}], rax")
            self.gen_expr(v_expr, info)  # rax/xmm0 = value
            if A.expr_type(v_expr) == "float":
                self.emitf("movq rax, xmm0")  # slot stores the raw bit pattern
            self.emitf(
                "mov rcx, rax",  # rcx = value
                f"mov rbx, [rbp{key_slot:+d}]",  # rbx = key ptr
                f"mov rax, [rbp{slot_off:+d}]",
                "call _runtime_dict_set",
            )
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    def _gen_set_lit(self, e: A.SetLit, info: FuncInfo) -> None:
        """`{a, b, c}` -> a dict keyed by the members (dummy value 1).

        A set reuses the dict layout: members become string keys, the value
        slot is an unused sentinel (1). Membership (`x in s`) is then the same
        `_runtime_dict_contains` lookup a dict uses. Mirrors `_gen_dict_lit`'s
        header/buffer allocation and per-element `_runtime_dict_set` insert.
        Elements are str-typed (set membership is str-keyed in v1).
        """
        slot_off = info.locals_[f"__setlit_{id(e)}"]
        n = len(e.elems)
        cap = 8
        while cap < n * 2:
            cap *= 2
        self._emit_malloc(self.DICT_HEADER)  # rax = header
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{slot_off:+d}], rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax"
        )
        self._emit_dict_alloc_order_buf(cap, slot_off)
        key_slot = info.locals_[f"__setlit_key_{id(e)}"]
        for el in e.elems:
            self.gen_expr(el, info)  # rax = member key
            if A.expr_type(el) == "int":
                # Int elements: convert to their decimal string so they share
                # the str-keyed dict backend. _emit_int_to_str uses a shared
                # scratch buffer — dup it so the key is independently stable.
                self._emit_int_to_str()
                self.emitf("call _runtime_str_concat_dup")
            self.emitf(
                f"mov [rbp{key_slot:+d}], rax",
                "mov rcx, 1",  # dummy value — only membership matters
                f"mov rbx, [rbp{key_slot:+d}]",
                f"mov rax, [rbp{slot_off:+d}]",
                "call _runtime_dict_set",
            )
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    # Each entry maps str method name -> runtime symbol it dispatches to.
    # The arg count (and order) follows the sema.STR_METHODS table.
    STR_METHOD_RUNTIME = {
        "upper": "_runtime_str_upper",
        "lower": "_runtime_str_lower",
        "casefold": "_runtime_str_lower",
        "capitalize": "_runtime_str_capitalize",
        "swapcase": "_runtime_str_swapcase",
        "title": "_runtime_str_title",
        "strip": "_runtime_str_strip",
        "lstrip": "_runtime_str_lstrip",
        "rstrip": "_runtime_str_rstrip",
        "zfill": "_runtime_str_zfill",
        "ljust": "_runtime_str_ljust",
        "rjust": "_runtime_str_rjust",
        "center": "_runtime_str_center",
        "startswith": "_runtime_str_starts_with",
        "endswith": "_runtime_str_ends_with",
        "removeprefix": "_runtime_str_removeprefix",
        "removesuffix": "_runtime_str_removesuffix",
        "find": "_runtime_str_index_of",
        "index": "_runtime_str_index_of",
        "rfind": "_runtime_str_rindex_of",
        "rindex": "_runtime_str_rindex_of",
        "expandtabs": "_runtime_str_expandtabs",
        "count": "_runtime_str_count",
        "replace": "_runtime_str_replace",
        "split": "_runtime_str_split",
        "splitlines": "_runtime_str_splitlines",
        "join": "_runtime_str_join",
        "partition": "_runtime_str_partition",
        "rpartition": "_runtime_str_rpartition",
        "rsplit": "_runtime_str_rsplit",
        "isdigit": "_runtime_str_isdigit",
        "isalpha": "_runtime_str_isalpha",
        "isalnum": "_runtime_str_isalnum",
        "isspace": "_runtime_str_isspace",
        "isupper": "_runtime_str_isupper",
        "islower": "_runtime_str_islower",
    }

    def _gen_str_method(self, e: A.MethodCall, info: FuncInfo) -> None:
        if e.method == "format" and isinstance(e.obj, A.StrLit):
            self._gen_str_format(e, info)
            return
        if e.method not in self.STR_METHOD_RUNTIME:
            # Unknown string method: evaluate receiver and args for side
            # effects, return 0.
            self.gen_expr(e.obj, info)
            for a in e.args:
                self.gen_expr(a, info)
            self.emitf("xor rax, rax")
            return
        sym = self.STR_METHOD_RUNTIME[e.method]
        # Reusable slot stash so we can evaluate arguments (which may call)
        # without push/pop across the call.
        obj_slot = info.locals_[f"__strm_obj_{id(e)}"]
        if len(e.args) == 0:
            self.gen_expr(e.obj, info)
            if e.method == "split":
                self.emitf("call _runtime_str_split_ws")
            elif e.method == "expandtabs":
                self.emitf("mov rbx, 8", f"call {sym}")
            else:
                self.emitf(f"call {sym}")
            return
        if len(e.args) == 1:
            self.gen_expr(e.obj, info)
            self.emitf(f"mov [rbp{obj_slot:+d}], rax")
            self.gen_expr(e.args[0], info)
            # split/rsplit with no maxsplit arg: pass rcx=0 so the runtime
            # treats it as "no limit" rather than reading a stale register.
            if e.method in ("split", "rsplit"):
                self.emitf("mov rbx, rax", f"mov rax, [rbp{obj_slot:+d}]",
                           "xor rcx, rcx", f"call {sym}")
            # ljust/rjust/center with no fillchar: default to a space.
            elif e.method in ("ljust", "rjust", "center"):
                self.emitf("mov rbx, rax", f"mov rax, [rbp{obj_slot:+d}]",
                           "mov rcx, 0x20", f"call {sym}")
            else:
                self.emitf("mov rbx, rax", f"mov rax, [rbp{obj_slot:+d}]", f"call {sym}")
            # str.index / str.rindex raise ValueError when the substring is not found.
            if e.method in ("index", "rindex"):
                _notfound = self.fresh("strm_idx_notfound")
                self.emitf("cmp rax, -1", f"jne {_notfound}")
                _notfound_msg, _ = self.intern_string("substring not found")
                self.emitf(
                    f"lea rax, [rel {_notfound_msg}]",
                    f"mov rbx, {self._exc_type_id('ValueError')}",
                    "call _runtime_raise",
                )
                self.label(_notfound)
            return
        if len(e.args) == 2:
            # replace(old, new), split(sep, maxsplit), ljust/rjust/center(width,
            # fillchar), or find/rfind(sub, start). Two scratch slots.
            a1_slot = info.locals_[f"__strm_a1_{id(e)}"]
            self.gen_expr(e.obj, info)
            self.emitf(f"mov [rbp{obj_slot:+d}], rax")
            self.gen_expr(e.args[0], info)
            self.emitf(f"mov [rbp{a1_slot:+d}], rax")
            self.gen_expr(e.args[1], info)
            if e.method in ("ljust", "rjust", "center"):
                # fillchar is a 1-char str: pass its first byte as rcx.
                self.emitf(
                    "movzx rcx, byte [rax]",
                    f"mov rbx, [rbp{a1_slot:+d}]",
                    f"mov rax, [rbp{obj_slot:+d}]",
                    f"call {sym}",
                )
            elif e.method in ("find", "index"):
                # rax=start, rbx=sub, rax=haystack -> call _runtime_str_index_of_start
                self.emitf(
                    "mov rcx, rax",
                    f"mov rbx, [rbp{a1_slot:+d}]",
                    f"mov rax, [rbp{obj_slot:+d}]",
                    "call _runtime_str_index_of_start",
                )
            elif e.method in ("rfind", "rindex"):
                self.emitf(
                    "mov rcx, rax",
                    f"mov rbx, [rbp{a1_slot:+d}]",
                    f"mov rax, [rbp{obj_slot:+d}]",
                    f"call {sym}",
                )
            else:
                self.emitf(
                    "mov rcx, rax",
                    f"mov rbx, [rbp{a1_slot:+d}]",
                    f"mov rax, [rbp{obj_slot:+d}]",
                    f"call {sym}",
                )
            # index/rindex with start: raise ValueError on not-found.
            if e.method in ("index", "rindex"):
                _nf2 = self.fresh("strm_idx2_nf")
                self.emitf("cmp rax, -1", f"jne {_nf2}")
                _nf2_msg, _ = self.intern_string("substring not found")
                self.emitf(
                    f"lea rax, [rel {_nf2_msg}]",
                    f"mov rbx, {self._exc_type_id('ValueError')}",
                    "call _runtime_raise",
                )
                self.label(_nf2)
            return
        # Too many args for this str method (e.g. os.path.join routed here):
        # evaluate all for side effects, return 0.
        self.gen_expr(e.obj, info)
        for a in e.args:
            self.gen_expr(a, info)
        self.emitf("xor rax, rax")

    def _gen_str_format(self, e: A.MethodCall, info: FuncInfo) -> None:
        """Lower `"...".format(args)` with a literal format string.

        Supports positional fields: `{}` (auto-numbered) and `{0}`/`{1}`
        (explicit index), an optional `!r`/`!s`/`!a` conversion, and an
        optional `:format-spec` using the same `[[fill]align]width.precision`
        mini-language as f-strings (reusing `_gen_fstring_segment` by
        stamping `fmt_spec`/`conv_flag` onto the referenced arg expression
        for each field). `{{`/`}}` are literal braces. The result is a chain
        of _runtime_str_concat over literal segments and stringified
        arguments — the same machinery as f-strings.

        The caller only dispatches here when `e.obj` is a StrLit.
        """
        assert isinstance(e.obj, A.StrLit)
        fmt = e.obj.value
        # Parse into a flat list of ("lit", text, "", "") and
        # ("arg", index_or_name, spec, conv) pieces (shared with sema's
        # validation pass, so the two stay in sync).
        pieces = A.parse_format_fields(fmt)

        acc_slot = info.locals_[f"__fmt_acc_{id(e)}"]

        def emit_piece(kind: object, val: object, spec: str, conv: str) -> None:
            if kind == "lit":
                label, _ = self.intern_string(val)  # type: ignore[arg-type]
                self.emitf(f"lea rax, [{label}]")
            else:
                if isinstance(val, str):
                    arg = None
                    for _kw_name, _kw_arg in e.kwargs:
                        if _kw_name == val:
                            arg = _kw_arg
                            break
                else:
                    arg = e.args[val]  # type: ignore[index]
                arg.fmt_spec = spec  # type: ignore[attr-defined]
                arg.conv_flag = conv  # type: ignore[attr-defined]
                self._gen_fstring_segment(arg, info)

        if not pieces:
            label, _ = self.intern_string("")
            self.emitf(f"lea rax, [{label}]")
            return
        _p0 = pieces[0]
        emit_piece(_p0[0], _p0[1], _p0[2], _p0[3])
        self.emitf(f"mov [rbp{acc_slot:+d}], rax")
        for kind, val, spec, conv in pieces[1:]:
            emit_piece(kind, val, spec, conv)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{acc_slot:+d}]",
                "call _runtime_str_concat",
                f"mov [rbp{acc_slot:+d}], rax",
            )
        self.emitf(f"mov rax, [rbp{acc_slot:+d}]")

    def _gen_method_call(self, e: A.MethodCall, info: FuncInfo) -> None:
        # os.environ.get(key) / os.environ.get(key, default): `os.environ`
        # itself has no real binding (it falls through to the generic
        # "unknown module attribute" stub, which evaluates to a null
        # pointer) — special-case the one supported access pattern here and
        # lower straight to libc getenv(), substituting an empty string (the
        # codebase's normal falsy-string sentinel — see _gen_truthy_test)
        # when the variable is unset, since getenv can return NULL and
        # nothing downstream expects a literal null string pointer.
        if (
            isinstance(e.obj, A.Attr)
            and isinstance(e.obj.obj, A.Name)
            and e.obj.obj.name == "os"
            and e.obj.name == "environ"
            and e.method == "get"
        ):
            self._gen_ffi_call(stdlib.os.BINDINGS["getenv"], e.args[:1], info)
            end = self.fresh("environ_get_end")
            self.emitf("test rax, rax", f"jnz {end}")
            if len(e.args) > 1:
                self.gen_expr(e.args[1], info)
            else:
                label, _ = self.intern_string("")
                self.emitf(f"lea rax, [{label}]")
            self.label(end)
            return
        # os.getcwd() / os.listdir(path) — inline helpers that need static buffers.
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
            if e.obj.name == "os" and e.method == "getcwd":
                self._needs_cwd_buf = True
                self._emit_os_getcwd()
                return
            if e.obj.name == "os" and e.method == "listdir":
                path_arg = e.args[0] if e.args else None
                self._emit_os_listdir(path_arg, info)
                return
        # math.sqrt(x), math.pow(a, b) etc.
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
            bindings = self.imported_modules[e.obj.name]
            b = bindings.get(e.method)
            if b is not None and hasattr(b, "arg_types"):
                self._gen_ffi_call(b, e.args, info)
                return
        # `handle.func(args)` where `handle = import_binary(path)` and `func`
        # is `@handle.imported`-decorated: indirect call through the function
        # pointer GetProcAddress/dlsym already resolved into the handle dict
        # at the import_binary() call site (see _gen_import_binary). Marshal
        # args exactly like a normal FFI call, but call through a register
        # holding the looked-up pointer instead of a static `extern` symbol —
        # the same shape as a closure call (see the lambda/sorted-key call
        # sites elsewhere in this file).
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_funcs:
            funcdef = None
            for fname, fdef in self.imported_funcs[e.obj.name]:
                if fname == e.method:
                    funcdef = fdef
                    break
            if funcdef is not None:
                self._gen_dynamic_call(e, funcdef, info)
                return
        # `some_instance.glClearColor(args)` where glClearColor is a
        # `@<handle>.imported`-decorated *method* (e.g. GLRenderer3D
        # wrapping its own GL bindings instead of forcing every caller to
        # hand-declare top-level @glfns.imported stubs) -- cross-reference
        # the receiver's static class against imported_method_handle to
        # find which handle to dispatch through, then reuse the exact same
        # _gen_dynamic_call as the direct `glfns.glClearColor(...)` case
        # above, just with `self` excluded from the argument list (it's a
        # real asmpython instance pointer, not part of the GL signature).
        recv_ty = A.expr_type(e.obj)
        if recv_ty.startswith("instance:"):
            cls_name = recv_ty[len("instance:"):]
            handle_name = self.imported_method_handle.get((cls_name, e.method))
            if handle_name is not None:
                funcdef = None
                for fname, fdef in self.imported_funcs[handle_name]:
                    if fname == e.method:
                        funcdef = fdef
                        break
                if funcdef is not None:
                    self._gen_dynamic_call(e, funcdef, info, handle_name=handle_name, skip_self=True)
                    return
        # `ClassName.method(args)`: @staticmethod / @classmethod called on the
        # class itself. Static methods take args verbatim; class methods get an
        # implicit leading `cls` (passed as null — asmpython has no class
        # objects, and class-method bodies that only touch class vars / call
        # other statics don't dereference it).
        if isinstance(e.obj, A.Name) and e.obj.name in self.class_ids:
            cls_name = e.obj.name
            mdef = self._find_method_def(cls_name, e.method)
            if mdef is not None:
                deco: list = getattr(mdef, "decorators", [])
                if "classmethod" in deco:
                    cleanup = self._emit_positional_args(e, e.args, info, start_reg=1)
                    self.emitf(f"xor {self._arg_reg(0)}, {self._arg_reg(0)}")  # cls = null
                    self.emit_call(self._method_symbol(cls_name, e.method))
                    if cleanup:
                        self.emitf(f"add rsp, {cleanup}")
                    return
                if "staticmethod" in deco:
                    cleanup = self._emit_positional_args(e, e.args, info, start_reg=0)
                    self.emit_call(self._method_symbol(cls_name, e.method))
                    if cleanup:
                        self.emitf(f"add rsp, {cleanup}")
                    return
        obj_t = A.expr_type(e.obj)
        if obj_t == "module" and e.method in self.funcs:
            # `A.expr_type(x)` where `A` is a project module imported via
            # `from . import ast_nodes as A`. Whole-program compilation merged
            # that module's top-level functions into this unit, so the call is
            # a plain function call to the merged symbol — the module qualifier
            # is just a namespace that no longer exists at the asm level.
            cleanup = self._emit_positional_args(e, e.args, info, start_reg=0)
            self.emit_call(self._user_symbol(e.method))
            if cleanup:
                self.emitf(f"add rsp, {cleanup}")
            return
        if obj_t.startswith("super:"):
            # super().method(args): dispatch to the base class's method, but
            # with the *current* instance (`self`) as the receiver.
            parent = obj_t.split(":", 1)[1]
            owner = self._resolve_method_owner(parent, e.method)
            if owner is None:
                # The base isn't a user class asmpython can dispatch to — it's a
                # builtin/external base (e.g. `class CompileError(Exception)`
                # calling `super().__init__(msg)`). asmpython's exception payload
                # is the message string, threaded through `raise`, and instances
                # carry no extra base state, so a `super().<base>()` call is a
                # no-op. Evaluate the args (for side effects) and return.
                for a in e.args:
                    self.gen_expr(a, info)
                return
            # Receiver is the enclosing method's own `self`, already in its slot.
            cleanup = self._emit_positional_args(
                e,
                e.args,
                info,
                start_reg=1,
                receiver_slot=info.locals_["self"],
            )
            self.emit_call(self._method_symbol(owner, e.method))
            if cleanup:
                self.emitf(f"add rsp, {cleanup}")
            return
        if obj_t.startswith("instance:"):
            class_name = obj_t.split(":", 1)[1]
            owner = self._resolve_method_owner(class_name, e.method)
            if owner is None:
                # Unknown method on an instance type (e.g. pathlib.Path.resolve()).
                # Evaluate args for side effects and return 0 as a stub.
                self.gen_expr(e.obj, info)
                for a in e.args:
                    self.gen_expr(a, info)
                self.emitf("xor rax, rax")
                return
            rows = self._virtual_dispatch_rows(class_name, e.method)
            owners: list = []
            for _cid, ow in rows:
                if ow not in owners:
                    owners.append(ow)
            recv_slot = info.locals_[f"__callself_{id(e)}"]
            if len(owners) <= 1:
                # No subclass overrides — bind statically. Sema normalized
                # e.args to a complete positional list; evaluate the receiver
                # (e.obj) and args into slots, then load reg0=self, reg1..
                cleanup = self._emit_positional_args(
                    e,
                    e.args,
                    info,
                    start_reg=1,
                    receiver_expr=e.obj,
                    receiver_slot=recv_slot,
                )
                self.emit_call(self._method_symbol(owner, e.method))
                if cleanup:
                    self.emitf(f"add rsp, {cleanup}")
                return
            # Subclasses override this method: dispatch on the receiver's
            # runtime `__class__` id. Evaluate receiver+args into slots, read
            # the class id (a runtime call — must precede the register loads),
            # park it in r10 (scratch on both ABIs, untouched by the slot->reg
            # moves), load the argument registers once, then branch per owner.
            offs = self._eval_call_operands(
                e, e.args, info, receiver_expr=e.obj, receiver_slot=recv_slot
            )
            key_label, _ = self.intern_string("__class__")
            self.emitf(
                f"mov rax, [rbp{recv_slot:+d}]",
                f"lea rbx, [{key_label}]",
                "mov rcx, -1",  # untagged -> no row matches -> static fallback
                "call _runtime_dict_get_default",
                "mov r10, rax",
            )
            cleanup = self._load_call_operands(
                e,
                offs,
                info,
                start_reg=1,
                receiver_slot=recv_slot,
                arg_types=[A.expr_type(a) for a in e.args],
            )
            end_lbl = self.fresh("vdisp_end")
            owner_labels: dict = {}
            for ow in owners:
                if ow != owner:
                    owner_labels[ow] = self.fresh(f"vdisp_{ow}")
            for cid, ow in rows:
                if ow != owner:
                    self.emitf(f"cmp r10, {cid}", f"je {owner_labels[ow]}")
            # Default: the statically-resolved owner (also covers untagged).
            self.emit_call(self._method_symbol(owner, e.method))
            self.emitf(f"jmp {end_lbl}")
            for ow in owners:
                if ow == owner:
                    continue
                self.label(owner_labels[ow])
                self.emit_call(self._method_symbol(ow, e.method))
                self.emitf(f"jmp {end_lbl}")
            self.label(end_lbl)
            if cleanup:
                self.emitf(f"add rsp, {cleanup}")
            return
        if obj_t == "str":
            self._gen_str_method(e, info)
            return
        if obj_t == "any" and e.method in self.STR_METHOD_RUNTIME and len(e.args) <= 2:
            # A str method on an opaque value (`base.split(".")` where `base`
            # came from an opaque unpack but is a string at runtime). Dispatch
            # to the str runtime — the value is a char pointer like any str.
            # Guard with len(e.args) <= 2 so os.path.join(a,b,c,d) (4 args)
            # doesn't accidentally land here.
            self._gen_str_method(e, info)
            return
        if obj_t == "dict" or (
            obj_t == "any"
            and e.method in ("get", "contains", "keys", "values", "items", "update")
            and not (e.method == "get" and len(e.args) == 0)
        ):
            # Known dict methods on an opaque receiver dispatch to the dict
            # runtime too — the value is dict-backed at runtime.
            if e.method == "get":
                # get(key[, default]): _runtime_dict_get_default(header=rax,
                # key=rbx, default=rcx). With no explicit default, missing keys
                # yield 0 (asmpython's None/unset sentinel).
                self.gen_expr(e.args[0], info)
                self.emitf("push rax")  # key
                if len(e.args) >= 2:
                    self.gen_expr(e.args[1], info)
                    if A.expr_type(e.args[1]) == "float":
                        self.emitf("movq rax, xmm0")
                    self.emitf("push rax")  # default
                else:
                    self.emitf("push 0")  # default = 0
                self.gen_expr(e.obj, info)  # rax = header
                self.emitf(
                    "pop rcx",  # rcx = default
                    "pop rbx",  # rbx = key
                    "call _runtime_dict_get_default",
                )
                if e.inferred_type == "float":
                    self.emitf("movq xmm0, rax")
                return
            if e.method == "contains":
                self.gen_expr(e.args[0], info)
                self.emitf("push rax")
                self.gen_expr(e.obj, info)
                self.emitf("pop rbx", "call _runtime_dict_contains")
                return
            if e.method == "keys":
                self.gen_expr(e.obj, info)  # rax = dict header
                self.emitf("call _runtime_dict_keys")
                return
            if e.method == "values":
                self.gen_expr(e.obj, info)
                self.emitf("call _runtime_dict_values")
                return
            if e.method == "items":
                self.gen_expr(e.obj, info)
                self.emitf("call _runtime_dict_items")
                return
            if e.method == "update":
                # dst.update(src): merge src's entries into dst. Park src across
                # dst's evaluation (which may call), then call the runtime
                # helper with rax=dst, rbx=src.
                key_slot = info.locals_[f"__dictupd_{id(e)}"]
                self.gen_expr(e.args[0], info)  # rax = src
                self.emitf(f"mov [rbp{key_slot:+d}], rax")
                self.gen_expr(e.obj, info)  # rax = dst
                self.emitf(
                    f"mov rbx, [rbp{key_slot:+d}]",
                    "call _runtime_dict_update",
                )
                return
            if e.method == "pop":
                arg_slot = info.locals_[f"__dm_arg_{id(e)}"]
                default_given = len(e.args) >= 2
                arg2_slot = info.locals_.get(f"__dm_arg2_{id(e)}")
                self.gen_expr(e.args[0], info)  # key
                self.emitf(f"mov [rbp{arg_slot:+d}], rax")
                if default_given:
                    self.gen_expr(e.args[1], info)  # default
                    self.emitf(f"mov [rbp{arg2_slot:+d}], rax")
                    # Check containment; if missing, return default.
                    lbl_found = self.fresh("dpop_found")
                    lbl_end = self.fresh("dpop_end")
                    self.gen_expr(e.obj, info)
                    self.emitf(
                        f"mov rbx, [rbp{arg_slot:+d}]",
                        "call _runtime_dict_contains",
                        "test rax, rax",
                        f"jnz {lbl_found}",
                        f"mov rax, [rbp{arg2_slot:+d}]",
                        f"jmp {lbl_end}",
                    )
                    self.label(lbl_found)
                    self.gen_expr(e.obj, info)
                    self.emitf(
                        f"mov rbx, [rbp{arg_slot:+d}]",
                        "call _runtime_dict_pop",
                    )
                    self.label(lbl_end)
                else:
                    self.gen_expr(e.obj, info)
                    self.emitf(
                        f"mov rbx, [rbp{arg_slot:+d}]",
                        "call _runtime_dict_pop",
                    )
                return
            if e.method == "clear":
                self.gen_expr(e.obj, info)
                self.emitf("call _runtime_dict_clear", "xor rax, rax")
                return
            if e.method == "copy":
                src_slot = info.locals_[f"__dm_src_{id(e)}"]
                new_slot = info.locals_[f"__dm_new_{id(e)}"]
                NEW_CAP = 8
                self.gen_expr(e.obj, info)  # rax = src
                self.emitf(f"mov [rbp{src_slot:+d}], rax")
                self.emitf(f"mov rbx, {self.DICT_HEADER}", "call _runtime_zalloc")
                self.emitf(f"mov [rbp{new_slot:+d}], rax")
                self.emitf(
                    f"mov qword [rax+{self.DICT_CAP_OFF}], {NEW_CAP}",
                    f"mov rbx, {NEW_CAP * self.DICT_SLOT_SIZE}",
                    "call _runtime_zalloc",
                    f"mov rcx, [rbp{new_slot:+d}]",
                    f"mov [rcx+{self.DICT_BUF_OFF}], rax",
                )
                self._emit_dict_alloc_order_buf(NEW_CAP, new_slot)
                self.emitf(
                    f"mov rax, [rbp{new_slot:+d}]",
                    f"mov rbx, [rbp{src_slot:+d}]",
                    "call _runtime_dict_update",
                    f"mov rax, [rbp{new_slot:+d}]",
                )
                return
            if e.method == "setdefault":
                # d.setdefault(key[, default]): if key not in dict, insert key->default.
                # Always returns d[key] after the potential insert.
                arg_slot = info.locals_[f"__dm_arg_{id(e)}"]
                default_given = len(e.args) >= 2
                arg2_slot = info.locals_.get(f"__dm_arg2_{id(e)}")
                self.gen_expr(e.args[0], info)  # key
                self.emitf(f"mov [rbp{arg_slot:+d}], rax")
                if default_given:
                    self.gen_expr(e.args[1], info)
                    self.emitf(f"mov [rbp{arg2_slot:+d}], rax")
                self.gen_expr(e.obj, info)  # rax = header
                self.emitf(f"mov rbx, [rbp{arg_slot:+d}]", "call _runtime_dict_contains")
                already = self.fresh("sdef_already")
                self.emitf("test rax, rax", f"jnz {already}")
                # Not present: insert key -> default.
                self.gen_expr(e.obj, info)
                self.emitf(f"mov rbx, [rbp{arg_slot:+d}]")
                if default_given:
                    self.emitf(f"mov rcx, [rbp{arg2_slot:+d}]")
                else:
                    self.emitf("xor rcx, rcx")
                self.emitf("call _runtime_dict_set")
                self.label(already)
                # Return current value.
                self.gen_expr(e.obj, info)
                self.emitf(f"mov rbx, [rbp{arg_slot:+d}]", "call _runtime_dict_get")
                return
        if obj_t == "set":
            # Sets are dicts keyed by their members (dummy value 1). Mutators
            # map onto the dict runtime.
            if e.method == "update":
                arg_t = A.expr_type(e.args[0])
                if arg_t in ("list", "tuple"):
                    # s.update(some_list): real Python's set.update() accepts
                    # any iterable, not just another set/dict -- but
                    # _runtime_dict_update assumes its source is dict-shaped
                    # (it reads order_buf/buf at dict offsets). Feeding it a
                    # LIST_HEADER (different layout, no order_buf at all)
                    # reads garbage/freed memory past the list's allocation
                    # and corrupts the program -- confirmed via gdb on a
                    # selfhost rebuild crashing inside _collect_frame_bound's
                    # `acc.update(s.targets)` (s.targets is a list[str]).
                    # Walk the list and .add() each element instead.
                    src_slot = info.locals_[f"__dictupd_{id(e)}"]
                    idx_slot = info.locals_[f"__dictupd_idx_{id(e)}"]
                    set_slot = info.locals_[f"__dictupd_set_{id(e)}"]
                    self.gen_expr(e.args[0], info)
                    self.emitf(f"mov [rbp{src_slot:+d}], rax")
                    self.gen_expr(e.obj, info)
                    self.emitf(f"mov [rbp{set_slot:+d}], rax")
                    self.emitf(f"mov qword [rbp{idx_slot:+d}], 0")
                    loop = self.fresh("setupd_loop")
                    done = self.fresh("setupd_done")
                    el_t = getattr(e.args[0], "list_el_type", None) or "any"
                    self.label(loop)
                    self.emitf(
                        f"mov rax, [rbp{src_slot:+d}]",
                        f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
                        f"mov rcx, [rbp{idx_slot:+d}]",
                        "cmp rcx, rbx",
                        f"jge {done}",
                        f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                        "mov rax, [rdx+rcx*8]",
                    )
                    if el_t == "int":
                        self._emit_int_to_str()
                        self.emitf("call _runtime_str_concat_dup")
                    self.emitf(
                        "mov rbx, rax",
                        "mov rcx, 1",
                        f"mov rax, [rbp{set_slot:+d}]",
                        "call _runtime_dict_set",
                        f"inc qword [rbp{idx_slot:+d}]",
                        f"jmp {loop}",
                    )
                    self.label(done)
                    self.emitf(f"mov rax, [rbp{set_slot:+d}]")
                    return
                # s.update(other_set_or_dict): sets are dicts keyed by
                # members, so this is exactly the dict merge.
                key_slot = info.locals_[f"__dictupd_{id(e)}"]
                self.gen_expr(e.args[0], info)
                self.emitf(f"mov [rbp{key_slot:+d}], rax")
                self.gen_expr(e.obj, info)
                self.emitf(
                    f"mov rbx, [rbp{key_slot:+d}]",
                    "call _runtime_dict_update",
                )
                return
            if e.method == "add":
                key_slot = info.locals_[f"__setadd_key_{id(e)}"]
                self.gen_expr(e.args[0], info)  # rax = member key
                if A.expr_type(e.args[0]) == "int":
                    self._emit_int_to_str()
                    self.emitf("call _runtime_str_concat_dup")
                self.emitf(f"mov [rbp{key_slot:+d}], rax")
                self.gen_expr(e.obj, info)  # rax = set (dict) header
                self.emitf(
                    "mov rcx, 1",  # dummy value
                    f"mov rbx, [rbp{key_slot:+d}]",
                    "call _runtime_dict_set",
                )
                return
            if e.method == "clear":
                self.gen_expr(e.obj, info)
                self.emitf("call _runtime_dict_clear", "xor rax, rax")
                return
            if e.method in ("union", "intersection", "difference"):
                self._gen_set_setop(e.obj, e.args[0], e.method, id(e), info)
                return
            if e.method in ("discard", "remove"):
                # s.discard(x): remove x if present, else no-op.
                # s.remove(x): remove x, raising KeyError if absent (which
                # _runtime_dict_pop already does for us).
                key_slot = info.locals_[f"__setrm_key_{id(e)}"]
                self.gen_expr(e.args[0], info)  # rax = member key
                if A.expr_type(e.args[0]) == "int":
                    self._emit_int_to_str()
                    self.emitf("call _runtime_str_concat_dup")
                self.emitf(f"mov [rbp{key_slot:+d}], rax")
                if e.method == "discard":
                    self.gen_expr(e.obj, info)
                    self.emitf(f"mov rbx, [rbp{key_slot:+d}]", "call _runtime_dict_contains")
                    done = self.fresh("sdisc_done")
                    self.emitf("test rax, rax", f"jz {done}")
                    self.gen_expr(e.obj, info)
                    self.emitf(f"mov rbx, [rbp{key_slot:+d}]", "call _runtime_dict_pop")
                    self.label(done)
                else:
                    self.gen_expr(e.obj, info)
                    self.emitf(f"mov rbx, [rbp{key_slot:+d}]", "call _runtime_dict_pop")
                self.emitf("xor rax, rax")
                return
            if e.method == "copy":
                # s.copy(): a shallow copy, same as dict.copy (sets are dicts
                # keyed by their members).
                src_slot = info.locals_[f"__dm_src_{id(e)}"]
                new_slot = info.locals_[f"__dm_new_{id(e)}"]
                NEW_CAP = 8
                self.gen_expr(e.obj, info)  # rax = src
                self.emitf(f"mov [rbp{src_slot:+d}], rax")
                self.emitf(f"mov rbx, {self.DICT_HEADER}", "call _runtime_zalloc")
                self.emitf(f"mov [rbp{new_slot:+d}], rax")
                self.emitf(
                    f"mov qword [rax+{self.DICT_CAP_OFF}], {NEW_CAP}",
                    f"mov rbx, {NEW_CAP * self.DICT_SLOT_SIZE}",
                    "call _runtime_zalloc",
                    f"mov rcx, [rbp{new_slot:+d}]",
                    f"mov [rcx+{self.DICT_BUF_OFF}], rax",
                )
                self._emit_dict_alloc_order_buf(NEW_CAP, new_slot)
                self.emitf(
                    f"mov rax, [rbp{new_slot:+d}]",
                    f"mov rbx, [rbp{src_slot:+d}]",
                    "call _runtime_dict_update",
                    f"mov rax, [rbp{new_slot:+d}]",
                )
                return
            if e.method == "pop":
                # s.pop(): remove and return an arbitrary member (the first
                # live key), raising KeyError if the set is empty.
                key_slot = info.locals_[f"__dm_arg_{id(e)}"]
                self.gen_expr(e.obj, info)  # rax = header
                self.emitf("call _runtime_dict_keys")  # rax = list[str] of keys
                empty_msg, _ = self.intern_string("KeyError: 'pop from an empty set'")
                nonempty = self.fresh("spop_nonempty")
                self.emitf(
                    f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
                    "test rcx, rcx",
                    f"jnz {nonempty}",
                    f"lea rax, [{empty_msg}]",
                    f"mov rbx, {self._exc_type_id('KeyError')}",
                    "call _runtime_raise",
                )
                self.label(nonempty)
                self.emitf(
                    f"mov rax, [rax+{self.LIST_BUF_OFF}]",
                    "mov rax, [rax]",  # first key ptr
                    f"mov [rbp{key_slot:+d}], rax",
                )
                self.gen_expr(e.obj, info)  # rax = header again
                self.emitf(f"mov rbx, [rbp{key_slot:+d}]", "call _runtime_dict_pop")
                self.emitf(f"mov rax, [rbp{key_slot:+d}]")
                return
            raise NotImplementedError(f"set.{e.method}() not implemented yet")
        if e.method == "index" and A.expr_type(e.obj) in ("list", "tuple", "any"):
            # xs.index(v): linear scan returning the first matching index;
            # raises (ValueError-style) when absent, like CPython.
            if isinstance(e.obj, A.Name):
                el_t = e.obj.list_el_type
            elif isinstance(e.obj, A.ListLit):
                el_t = e.obj.el_type
            else:
                el_t = "int"
            slot_off = info.locals_[f"__listidx_{id(e)}"]
            self.gen_expr(e.args[0], info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
            self.gen_expr(e.obj, info)  # rax = header
            loop = self.fresh("lidx_loop")
            found = self.fresh("lidx_found")
            miss = self.fresh("lidx_miss")
            end = self.fresh("lidx_end")
            self.emitf(
                f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
                f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
                "xor r8, r8",
            )
            self.label(loop)
            self.emitf("cmp r8, rcx", f"jge {miss}")
            if el_t == "str":
                self.emitf(
                    "mov r9, [rdx+r8*8]",
                    "push rcx",
                    "push rdx",
                    "push r8",
                    "sub rsp, 8",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "mov rbx, r9",
                    "call _runtime_str_eq",
                    "add rsp, 8",
                    "pop r8",
                    "pop rdx",
                    "pop rcx",
                    "test rax, rax",
                    f"jnz {found}",
                )
            else:
                self.emitf(
                    "mov r9, [rdx+r8*8]",
                    f"cmp r9, [rbp{slot_off:+d}]",
                    f"je {found}",
                )
            self.emitf("inc r8", f"jmp {loop}")
            self.label(miss)
            msg_label, _ = self.intern_string("ValueError: value not in list")
            self.emitf(
                f"lea rax, [{msg_label}]",
                f"mov rbx, {self._exc_type_id('ValueError')}",
                "call _runtime_raise",
            )
            self.label(found)
            self.emitf("mov rax, r8")
            self.label(end)
            return
        if e.method == "extend":
            # dst.extend(src): append src's elements onto dst (shared layout).
            self.gen_expr(e.args[0], info)
            self.emitf("push rax")  # src
            self.gen_expr(e.obj, info)  # rax = dst header
            self.emitf("pop rbx", "call _runtime_list_extend")
            return
        if e.method == "append":
            arg_t = A.expr_type(e.args[0])
            self.gen_expr(e.args[0], info)
            if arg_t == "float":
                # Move the double's bit pattern into rax so the runtime helper
                # (which writes 8 raw bytes into the buffer) doesn't care.
                self.emitf("movq rax, xmm0")
            self.emitf("push rax")
            self.gen_expr(e.obj, info)  # rax = header
            self.emitf(
                "pop rbx",  # rbx = value (raw 8 bytes)
                "call _runtime_list_append",
            )
            return
        if e.method == "pop":
            self.gen_expr(e.obj, info)  # rax = header
            self.emitf("call _runtime_list_pop")
            if e.inferred_type == "float":
                self.emitf("movq xmm0, rax")
            return
        if e.method == "sort":
            obj_t = A.expr_type(e.obj)
            el_kind = getattr(e.obj, "list_el_type", "int") if isinstance(e.obj, A.Name) else "int"
            self.gen_expr(e.obj, info)  # rax = header
            sort_key = getattr(e, "sort_key", None)
            if sort_key is not None:
                elems = info.locals_[f"__sortkey_elems_{id(e)}"]
                self.emitf(f"mov [rbp{elems:+d}], rax")
                self._emit_sort_keys_list(e, info)
                sort_fn = (
                    "_runtime_sort_pairs_str"
                    if e.sort_key_ret == "str"
                    else "_runtime_sort_pairs_int"
                )
                keys = info.locals_[f"__sortkey_keys_{id(e)}"]
                self.emitf(
                    f"mov rax, [rbp{elems:+d}]",
                    f"mov rbx, [rbp{keys:+d}]",
                    f"call {sort_fn}",
                )
            elif el_kind == "str":
                self.emitf("call _runtime_sort_str")
            else:
                self.emitf("call _runtime_sort_int")
            if getattr(e, "sort_reverse", None) is not None:
                self._emit_conditional_list_reverse(e, info)
            self.emitf("xor rax, rax")  # returns None ~ 0
            return
        if e.method == "reverse":
            self.gen_expr(e.obj, info)
            self.emitf("call _runtime_list_reverse", "xor rax, rax")
            return
        if e.method == "clear":
            obj_t2 = A.expr_type(e.obj)
            if obj_t2 in ("list", "tuple"):
                self.gen_expr(e.obj, info)
                self.emitf(f"mov qword [rax+{self.LIST_LEN_OFF}], 0", "xor rax, rax")
            else:
                # dict/set clear
                self.gen_expr(e.obj, info)
                self.emitf("call _runtime_dict_clear", "xor rax, rax")
            return
        if e.method == "copy":
            obj_t3 = A.expr_type(e.obj)
            if obj_t3 in ("list", "tuple"):
                SENTINEL_MIN = "0x8000000000000000"
                SENTINEL_MAX = "0x7fffffffffffffff"
                self.gen_expr(e.obj, info)
                self.emitf(
                    f"mov rbx, {SENTINEL_MIN}",
                    f"mov rcx, {SENTINEL_MAX}",
                    "call _runtime_list_slice",
                )
            else:
                # dict/set copy: allocate empty dict then update from src.
                src_slot = info.locals_[f"__dm_src_{id(e)}"]
                new_slot = info.locals_[f"__dm_new_{id(e)}"]
                NEW_CAP = 8
                self.gen_expr(e.obj, info)  # rax = src
                self.emitf(f"mov [rbp{src_slot:+d}], rax")
                # Allocate new empty dict header.
                self.emitf(f"mov rbx, {self.DICT_HEADER}", "call _runtime_zalloc")
                self.emitf(f"mov [rbp{new_slot:+d}], rax")
                # Allocate slot buffer.
                self.emitf(
                    f"mov qword [rax+{self.DICT_CAP_OFF}], {NEW_CAP}",
                    f"mov rbx, {NEW_CAP * self.DICT_SLOT_SIZE}",
                    "call _runtime_zalloc",
                )
                self.emitf(
                    f"mov rcx, [rbp{new_slot:+d}]",
                    f"mov [rcx+{self.DICT_BUF_OFF}], rax",
                )
                self._emit_dict_alloc_order_buf(NEW_CAP, new_slot)
                self.emitf(
                    # update: _runtime_dict_update(new_dict, src)
                    f"mov rax, [rbp{new_slot:+d}]",
                    f"mov rbx, [rbp{src_slot:+d}]",
                    "call _runtime_dict_update",
                    f"mov rax, [rbp{new_slot:+d}]",
                )
            return
        if e.method == "count":
            val_slot = info.locals_[f"__lm_val_{id(e)}"]
            hdr_slot = info.locals_[f"__lm_hdr_{id(e)}"]
            el_t = getattr(e.obj, "list_el_type", "int") if isinstance(e.obj, A.Name) else "int"
            self.gen_expr(e.args[0], info)
            self.emitf(f"mov [rbp{val_slot:+d}], rax")
            self.gen_expr(e.obj, info)  # rax = header
            self.emitf(f"mov [rbp{hdr_slot:+d}], rax")
            loop = self.fresh("lcnt_loop")
            no_match = self.fresh("lcnt_no")
            end = self.fresh("lcnt_end")
            self.emitf(
                f"mov rdx, [rax+{self.LIST_LEN_OFF}]",
                f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
                "xor rcx, rcx",  # index
                "xor r8, r8",    # count
            )
            self.label(loop)
            self.emitf("cmp rcx, rdx", f"jge {end}")
            if el_t == "str":
                self.emitf(
                    "push rcx", "push rdx", "push r8", "sub rsp, 8",
                    "mov rax, [rbx+rcx*8]",
                    f"mov rbx, [rbp{val_slot:+d}]",
                    "call _runtime_str_eq",
                    "add rsp, 8", "pop r8", "pop rdx", "pop rcx",
                    f"mov rbx, [rbp{hdr_slot:+d}]",
                    f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                    "test rax, rax",
                    f"jz {no_match}",
                    "inc r8",
                )
            else:
                self.emitf(
                    "mov r9, [rbx+rcx*8]",
                    f"cmp r9, [rbp{val_slot:+d}]",
                    f"jne {no_match}",
                    "inc r8",
                )
            self.label(no_match)
            self.emitf("inc rcx", f"jmp {loop}")
            self.label(end)
            self.emitf("mov rax, r8")
            return
        if e.method == "insert":
            idx_slot = info.locals_[f"__lm_idx_{id(e)}"]
            val_slot = info.locals_[f"__lm_val_{id(e)}"]
            self.gen_expr(e.args[0], info)   # index
            self.emitf(f"mov [rbp{idx_slot:+d}], rax")
            self.gen_expr(e.args[1], info)   # value
            self.emitf(f"mov [rbp{val_slot:+d}], rax")
            self.gen_expr(e.obj, info)        # rax = header
            self.emitf(
                f"mov rbx, [rbp{idx_slot:+d}]",
                f"mov rcx, [rbp{val_slot:+d}]",
                "call _runtime_list_insert",
                "xor rax, rax",
            )
            return
        if e.method == "remove":
            val_slot = info.locals_[f"__lm_val_{id(e)}"]
            hdr_slot = info.locals_[f"__lm_hdr_{id(e)}"]
            el_t = getattr(e.obj, "list_el_type", "int") if isinstance(e.obj, A.Name) else "int"
            self.gen_expr(e.args[0], info)
            self.emitf(f"mov [rbp{val_slot:+d}], rax")
            self.gen_expr(e.obj, info)  # rax = header
            self.emitf(f"mov [rbp{hdr_slot:+d}], rax")
            loop = self.fresh("lrem_loop")
            found = self.fresh("lrem_found")
            miss = self.fresh("lrem_miss")
            shift = self.fresh("lrem_shift")
            end = self.fresh("lrem_end")
            self.emitf(
                f"mov rdx, [rax+{self.LIST_LEN_OFF}]",
                f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
                "xor rcx, rcx",
            )
            self.label(loop)
            self.emitf("cmp rcx, rdx", f"jge {miss}")
            if el_t == "str":
                self.emitf(
                    "push rcx", "push rdx", "sub rsp, 16",
                    "mov rax, [rbx+rcx*8]",
                    f"mov rbx, [rbp{val_slot:+d}]",
                    "call _runtime_str_eq",
                    "add rsp, 16", "pop rdx", "pop rcx",
                    f"mov rbx, [rbp{hdr_slot:+d}]",
                    f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                    "test rax, rax", f"jnz {found}",
                )
            else:
                self.emitf(
                    "mov r9, [rbx+rcx*8]",
                    f"cmp r9, [rbp{val_slot:+d}]",
                    f"je {found}",
                )
            self.emitf("inc rcx", f"jmp {loop}")
            self.label(miss)
            err_lbl, _ = self.intern_string("ValueError: value not in list")
            self.emitf(
                f"lea rax, [{err_lbl}]",
                f"mov rbx, {self._exc_type_id('ValueError')}",
                "call _runtime_raise",
            )
            self.label(found)
            # Shift elements left: buf[i] = buf[i+1], ..., buf[len-2] = buf[len-1]
            self.label(shift)
            self.emitf(
                "mov r9, rcx", "inc r9",
                "cmp r9, rdx", f"jge {end}",
                "mov r8, [rbx+r9*8]",
                "mov [rbx+rcx*8], r8",
                "inc rcx",
                f"jmp {shift}",
            )
            self.label(end)
            self.emitf(
                f"mov rax, [rbp{hdr_slot:+d}]",
                f"dec qword [rax+{self.LIST_LEN_OFF}]",
                "xor rax, rax",
            )
            return
        # Unknown method on an opaque/instance receiver: evaluate receiver and
        # args for side effects, return 0. Keeps the self-hosting build alive;
        # real implementations replace these stubs in stdlib.
        self.gen_expr(e.obj, info)
        for a in e.args:
            self.gen_expr(a, info)
        self.emitf("xor rax, rax")

    def _gen_binop(self, e: A.BinOp, info: FuncInfo) -> None:
        lt, rt = A.expr_type(e.left), A.expr_type(e.right)
        # Set algebra: `a | b` / `a & b` / `a - b` lower to the same
        # build-a-fresh-set logic as `.union`/`.intersection`/`.difference`.
        if lt == "set" and rt == "set" and e.op in ("|", "&", "-"):
            method = {"|": "union", "&": "intersection", "-": "difference"}[e.op]
            self._gen_set_setop(e.left, e.right, method, id(e), info)
            return
        # Dict union (`d1 | d2`, PEP 584) builds a fresh dict the same way as
        # set union: new.update(left); new.update(right) so right's entries
        # win on key conflicts.
        if lt == "dict" and rt == "dict" and e.op == "|":
            self._gen_set_setop(e.left, e.right, "union", id(e), info)
            return
        # An instance operand that overloads this operator via a dunder
        # (`Path("a") / "b"` -> `Path.__truediv__`, resolved by sema and
        # stamped onto the node as dunder_owner/dunder_method) dispatches to
        # that method: self in arg reg 0, the other operand in arg reg 1.
        if lt.startswith("instance:") or rt.startswith("instance:"):
            owner = getattr(e, "dunder_owner", None)
            if owner is not None:
                method = e.dunder_method  # type: ignore[attr-defined]
                reflected = getattr(e, "dunder_reflected", False)
                self_expr = e.right if reflected else e.left
                other_expr = e.left if reflected else e.right
                slot = info.locals_[f"__binop_lhs_{id(e)}"]
                self.gen_expr(self_expr, info)
                self.emitf(f"mov [rbp{slot:+d}], rax")
                self.gen_expr(other_expr, info)
                self.emitf(
                    f"mov {self._arg_reg(1)}, rax",
                    f"mov {self._arg_reg(0)}, [rbp{slot:+d}]",
                )
                self.emit_call(self._method_symbol(owner, method))
                return
            # No dunder resolved (opaque receiver): emit both subexpressions
            # for side effects and produce 0 as the result. This beats
            # crashing at compile time for unmodeled external instance types.
            slot = info.locals_.get(f"__binop_lhs_{id(e)}")
            self.gen_expr(e.left, info)
            if slot is not None:
                self.emitf(f"mov [rbp{slot:+d}], rax")
            self.gen_expr(e.right, info)
            self.emitf("xor rax, rax")
            return
        # String ops dispatch to runtime helpers.
        if "str" in (lt, rt):
            self._gen_binop_str(e, info, lt, rt)
            return
        # True division always returns a float (Python semantics) so route
        # through the float path even when both operands are ints.
        if "float" in (lt, rt) or e.op == "/":
            self._gen_binop_float(e, info, lt, rt)
            return
        # List concatenation: left + right -> copy(left).extend(right).
        if e.op == "+" and "list" in (lt, rt):
            SENTINEL_MIN = "0x8000000000000000"
            SENTINEL_MAX = "0x7fffffffffffffff"
            slot = info.locals_[f"__listcat_{id(e)}"]
            self.gen_expr(e.left, info)
            self.emitf(
                f"mov rbx, {SENTINEL_MIN}",
                f"mov rcx, {SENTINEL_MAX}",
                "call _runtime_list_slice",   # rax = shallow copy of left
                f"mov [rbp{slot:+d}], rax",
            )
            self.gen_expr(e.right, info)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{slot:+d}]",
                "call _runtime_list_extend",  # rax = copy with right appended
            )
            return
        # List repetition: list * int  or  int * list  -> _runtime_list_repeat.
        if e.op == "*" and "list" in (lt, rt):
            slot = info.locals_[f"__listrep_{id(e)}"]
            list_expr = e.left if lt == "list" else e.right
            count_expr = e.right if lt == "list" else e.left
            self.gen_expr(list_expr, info)
            self.emitf(f"mov [rbp{slot:+d}], rax")
            self.gen_expr(count_expr, info)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{slot:+d}]",
                "call _runtime_list_repeat",
            )
            return
        self.gen_expr(e.left, info)
        self.emitf("push rax")
        self.gen_expr(e.right, info)
        self.emitf("mov rbx, rax", "pop rax")
        self._emit_binop_inline(e.op)

    def _gen_binop_str(self, e: A.BinOp, info: FuncInfo, lt: str, rt: str) -> None:
        """Lower string + str (concat) and str * int (repeat) to runtime calls.

        Uses a pre-reserved scratch slot rather than push/pop because the
        right operand may contain a call (e.g. `"x = " + str(n)`) and
        push/pop across a call breaks 16-byte stack alignment.
        """
        slot_off = info.locals_[f"__binstr_{id(e)}"]
        if e.op == "%" and lt == "str":
            self._gen_str_pct_format(e, info, slot_off)
            return
        if e.op == "+":
            self.gen_expr(e.left, info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
            self.gen_expr(e.right, info)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{slot_off:+d}]",
                "call _runtime_str_concat",
            )
            return
        if e.op == "*":
            if lt == "str":
                self.gen_expr(e.left, info)
                self.emitf(f"mov [rbp{slot_off:+d}], rax")
                self.gen_expr(e.right, info)
                self.emitf(
                    "mov rbx, rax",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "call _runtime_str_repeat",
                )
            else:
                self.gen_expr(e.right, info)
                self.emitf(f"mov [rbp{slot_off:+d}], rax")
                self.gen_expr(e.left, info)
                self.emitf(
                    "mov rbx, rax",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "call _runtime_str_repeat",
                )
            return
        # Unknown string binop (e.g. Path / "sub" where one side is opaque):
        # evaluate both operands for side effects and return 0.
        self.gen_expr(e.left, info)
        self.gen_expr(e.right, info)
        self.emitf("xor rax, rax")

    def _gen_str_pct_format(self, e: A.BinOp, info: FuncInfo, acc_slot: int) -> None:
        """Lower `"...%s/%d/%f..." % (args)` (literal format string, sema-
        validated) to a concat chain over literal segments and per-arg
        conversions — the same `_runtime_str_concat` chaining machinery as
        f-strings and `.format()`.

        `%s` reuses `_gen_fstring_segment` (str() of any type); `%d/%i/%u/
        %o/%x/%X` and `%e/%E/%f/%F/%g/%G` go through `_emit_int_fmt`/
        `_emit_float_fmt` with a printf format string built from the
        flags/width/precision (translating Python's int conversions to the
        `ll`-sized C equivalents). A `%s` with a width pads via
        `_runtime_str_ljust`/`_runtime_str_rjust` (space fill).
        """
        assert isinstance(e.left, A.StrLit)
        pieces, _ = A.parse_pct_format(e.left.value)
        args: list = e.right.elems if isinstance(e.right, A.TupleLit) else [e.right]
        # A mutable single-element list standing in for a nonlocal counter:
        # this closure needs shared, advancing state across calls, and
        # asmpython has no codegen for iter()/next() (this file is itself
        # self-compiled), so an explicit index replaces the iterator.
        arg_pos = [0]

        def emit_pct_piece(piece: tuple) -> None:
            if piece[0] == "lit":
                label, _ = self.intern_string(piece[1])
                self.emitf(f"lea rax, [{label}]")
                return
            _, flags, width, precision, conv = piece
            arg = args[arg_pos[0]]
            arg_pos[0] += 1
            if conv in "sr":
                if conv == "r":
                    arg.conv_flag = "r"  # type: ignore[attr-defined]
                self._gen_fstring_segment(arg, info)
                if width:
                    self.emitf(f"mov rbx, {width}", "mov rcx, 0x20")
                    if "-" in flags:
                        self.emitf("call _runtime_str_ljust")
                    else:
                        self.emitf("call _runtime_str_rjust")
                return
            if conv in "diouxX":
                cconv = {"i": "d", "u": "d", "d": "d", "o": "o", "x": "x", "X": "X"}[conv]
                cfmt = "%" + flags + width + precision + "ll" + cconv
                label, _ = self.intern_string(cfmt)
                self.gen_expr(arg, info)
                self._emit_int_fmt(label)
                return
            # eEfFgG: Python defaults precision to 6, same as C, when omitted.
            prec = precision if precision else ".6"
            cfmt = "%" + flags + width + prec + conv
            label, _ = self.intern_string(cfmt)
            self._gen_expr_as_float(arg, info, A.expr_type(arg))
            self._emit_float_fmt(label)

        if not pieces:
            label, _ = self.intern_string("")
            self.emitf(f"lea rax, [{label}]")
            return
        emit_pct_piece(pieces[0])
        self.emitf(f"mov [rbp{acc_slot:+d}], rax")
        for piece in pieces[1:]:
            emit_pct_piece(piece)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{acc_slot:+d}]",
                "call _runtime_str_concat",
                f"mov [rbp{acc_slot:+d}], rax",
            )
        self.emitf(f"mov rax, [rbp{acc_slot:+d}]")

    def _gen_binop_float(self, e: A.BinOp, info: FuncInfo, lt: str, rt: str) -> None:
        # Evaluate left (promote to float if int), spill to a stable
        # rbp-relative scratch slot, evaluate right (promote), then load
        # into xmm1, restore left to xmm0.
        #
        # The spill slot must be rbp-relative, not a raw `sub rsp, 8` /
        # `[rsp]` push: if the right operand is or contains an FFI call
        # (e.g. `(1.0 - f0) * math.pow(x, 5.0)`), that call adjusts rsp
        # itself for shadow space / stack-passed args, so `[rsp]` no longer
        # points at the spilled value once control returns -- silently
        # reading garbage instead (confirmed bug, see _cl_walk_expr's
        # __binfloat_ comment for the exact failing example).
        self._gen_expr_as_float(e.left, info, lt)
        slot = info.locals_[f"__binfloat_{id(e)}"]
        self.emitf(f"movsd [rbp{slot:+d}], xmm0")
        self._gen_expr_as_float(e.right, info, rt)
        self.emitf(f"movsd xmm1, xmm0", f"movsd xmm0, [rbp{slot:+d}]")
        self._emit_binop_inline_float(e.op)

    def _gen_expr_as_float(self, expr, info: FuncInfo, ty: str) -> None:
        """Evaluate expr; ensure result is in xmm0 as a float, promoting if needed."""
        self.gen_expr(expr, info)
        if ty != "float":
            # int (or an opaque "any" element from an unannotated `list`,
            # which holds plain ints at runtime) → float via cvtsi2sd.
            # Only a "float"-typed expr already lands its result in xmm0.
            self.emitf("cvtsi2sd xmm0, rax")

    def _gen_truthy_test(self, expr, info: FuncInfo, false_target: str) -> None:
        """Evaluate expr; jump to false_target if value is falsy."""
        if isinstance(expr, A.BoolOp):
            # `A.expr_type` of a BoolOp falls back to "int" when its operands
            # have different static types (e.g. `x is None and some_list`),
            # even though the runtime value can be the list. Don't materialize
            # that value and re-test it as a generic int/pointer (the same bug
            # _gen_boolop itself had) — flatten the same-operator chain and
            # truthy-test each real leaf with its own type instead.
            operands: list = []

            def flatten_truthy_chain(node) -> None:
                if isinstance(node, A.BoolOp) and node.op == expr.op:
                    flatten_truthy_chain(node.left)
                    flatten_truthy_chain(node.right)
                else:
                    operands.append(node)

            flatten_truthy_chain(expr)
            if expr.op == "and":
                # All must be truthy; any falsy one fails the whole test.
                for operand in operands:
                    self._gen_truthy_test(operand, info, false_target)
            else:
                # `or`: short-circuit to "overall truthy" on the first truthy
                # operand; only fail if every operand is falsy.
                pass_lbl = self.fresh("boolop_or_pass")
                for operand in operands[:-1]:
                    next_lbl = self.fresh("boolop_or_next")
                    self._gen_truthy_test(operand, info, next_lbl)
                    self.emitf(f"jmp {pass_lbl}")
                    self.label(next_lbl)
                self._gen_truthy_test(operands[-1], info, false_target)
                self.label(pass_lbl)
            return
        t = A.expr_type(expr)
        if t == "float":
            self.gen_expr(expr, info)  # xmm0 = value
            zero_lbl = self.intern_float(0.0)
            past_nan = self.fresh("not_nan")
            self.emitf(
                f"movsd xmm1, [{zero_lbl}]",
                "ucomisd xmm0, xmm1",
                # ucomisd sets PF on unordered (NaN). We treat NaN as
                # truthy (Python semantics), so skip the zero-check.
                f"jp {past_nan}",
                f"je {false_target}",
            )
            self.label(past_nan)
            return
        if t.startswith("instance:"):
            cls_name = t.split(":", 1)[1]
            # __bool__ takes precedence over __len__ (matches CPython).
            for mname in ("__bool__", "__len__"):
                owner = self._resolve_method_owner(cls_name, mname)
                if owner is not None:
                    self.gen_expr(expr, info)
                    reg0 = self._arg_reg(0)
                    self.emitf(f"mov {reg0}, rax")
                    self.emit_call(self._method_symbol(owner, mname))
                    self.emitf("test rax, rax", f"jz {false_target}")
                    return
            # No __bool__ / __len__: any non-null instance pointer is truthy.
            # A live instance is always non-null, so no branch needed.
            return
        if t in ("list", "tuple", "dict", "set"):
            # Container truthiness is its LENGTH (an empty list is a valid,
            # non-NULL pointer — testing the pointer would make `if []` true).
            # Lists/tuples and dicts/sets all keep their length at +8. An
            # Optional container can be a NULL pointer (None is falsy too),
            # so check for NULL before dereferencing it for the length.
            self.gen_expr(expr, info)
            self.emitf(
                "test rax, rax", f"jz {false_target}",
                "mov rax, [rax+8]", "test rax, rax", f"jz {false_target}",
            )
            return
        if t == "str":
            # An empty string is falsy: test the first byte, not the pointer.
            # A `str | None` value can be a NULL pointer (e.g. an unset
            # Optional[str] field) — None is also falsy, so check for NULL
            # before dereferencing to read the first byte.
            self.gen_expr(expr, info)
            self.emitf(
                "test rax, rax", f"jz {false_target}",
                "movzx rax, byte [rax]", "test rax, rax", f"jz {false_target}",
            )
            return
        # Default: int or pointer; non-zero is truthy.
        self.gen_expr(expr, info)
        self.emitf("test rax, rax", f"jz {false_target}")

    def _emit_check_float_nonzero_divisor(self) -> None:
        """Raise ZeroDivisionError("division by zero") if xmm1 == 0.0.

        SSE division by zero is masked by default (it produces inf/nan, not
        a fault), but Python raises ZeroDivisionError for `/`, `//` and `%`
        with a zero float RHS. NaN divisors are left alone (ucomisd reports
        them as unordered, which we treat as "not zero").
        """
        ok = self.fresh("fdiv_nonzero")
        zero_lbl = self.intern_float(0.0)
        self.emitf(
            f"movsd xmm2, [{zero_lbl}]",
            "ucomisd xmm1, xmm2",
            f"jp {ok}",  # unordered (NaN divisor): not zero, proceed
            f"jne {ok}",
            "lea rax, [rel _runtime_zerodiv_msg]",
            f"mov rbx, {self._exc_type_id('ZeroDivisionError')}",
            "call _runtime_raise",
        )
        self.label(ok)

    def _emit_binop_inline_float(self, op: str) -> None:
        """xmm0 (LHS), xmm1 (RHS) -> xmm0 = LHS op RHS."""
        if op == "+":
            self.emitf("addsd xmm0, xmm1")
        elif op == "-":
            self.emitf("subsd xmm0, xmm1")
        elif op == "*":
            self.emitf("mulsd xmm0, xmm1")
        elif op == "/":
            self._emit_check_float_nonzero_divisor()
            self.emitf("divsd xmm0, xmm1")
        elif op == "//":
            self._emit_check_float_nonzero_divisor()
            self.emitf("divsd xmm0, xmm1")
            self.emitf("roundsd xmm0, xmm0, 1")
        elif op == "%":
            self._emit_check_float_nonzero_divisor()
            self._emit_call_libc_double_double("fmod")
        elif op == "**":
            self._emit_call_libc_double_double("pow")
        else:
            raise NotImplementedError(f"float op {op!r}")

    def _emit_binop_inline(self, op: str) -> None:
        """Emit the body of a binop assuming LHS in RAX, RHS in RBX. Result -> RAX."""
        if op == "+":
            self.emitf("add rax, rbx")
        elif op == "-":
            self.emitf("sub rax, rbx")
        elif op == "*":
            self.emitf("imul rax, rbx")
        elif op in ("//", "%"):
            # A zero RHS would fault IDIV (#DE), which on freestanding has no
            # handler and triple-faults. Raise ZeroDivisionError instead,
            # matching CPython's "division by zero".
            nonzero = self.fresh("idiv_nonzero")
            self.emitf(
                "test rbx, rbx",
                f"jnz {nonzero}",
                "lea rax, [rel _runtime_zerodiv_msg]",
                f"mov rbx, {self._exc_type_id('ZeroDivisionError')}",
                "call _runtime_raise",
            )
            self.label(nonzero)
            # IDIV uses RDX:RAX / RBX -> RAX (quot), RDX (rem). CQO sign-extends.
            # IDIV truncates toward zero, but Python's // and % floor toward
            # -inf, so when the (nonzero) remainder's sign differs from the
            # divisor's, adjust: quotient -= 1, remainder += divisor.
            done = self.fresh("floordiv_done")
            self.emitf("cqo", "idiv rbx")
            self.emitf(
                "test rdx, rdx",
                f"jz {done}",
                "mov rcx, rdx",
                "xor rcx, rbx",
                "test rcx, rcx",
                f"jns {done}",
                "dec rax",
                "add rdx, rbx",
            )
            self.label(done)
            if op == "%":
                self.emitf("mov rax, rdx")
        elif op == "&":
            self.emitf("and rax, rbx")
        elif op == "|":
            self.emitf("or rax, rbx")
        elif op == "^":
            self.emitf("xor rax, rbx")
        elif op == "<<":
            # x86 shifts read count from CL; mask to 6 bits like the CPU does
            # on 64-bit shifts (Python's semantics would raise on negatives,
            # but we accept x86 behavior for now since sema is dynamic).
            self.emitf("mov rcx, rbx", "shl rax, cl")
        elif op == ">>":
            self.emitf("mov rcx, rbx", "sar rax, cl")
        elif op == "**":
            # Integer exponentiation: base in RAX, exp in RBX -> result in RAX.
            pow_loop = self.fresh("pow_loop")
            pow_end = self.fresh("pow_end")
            self.emitf(
                "mov rcx, rbx",   # exp counter
                "mov rbx, rax",   # base
                "mov rax, 1",     # result starts at 1
            )
            self.label(pow_loop)
            self.emitf(
                "test rcx, rcx",
                f"jle {pow_end}",
                "imul rax, rbx",
                "dec rcx",
                f"jmp {pow_loop}",
            )
            self.label(pow_end)
        else:
            raise NotImplementedError(op)

    SETCC = {
        "==": "sete",
        "!=": "setne",
        "<": "setl",
        "<=": "setle",
        ">": "setg",
        ">=": "setge",
        # `is` / `is not` lower to identity-as-bit-equality. With asmpython's
        # uniform 8-byte runtime representation that's the same as ==/!= on
        # the two raw 64-bit slots.
        "is": "sete",
        "is not": "setne",
    }
    # Inverse jump for short-circuit on chained compares.
    JCC_INV = {
        "==": "jne",
        "!=": "je",
        "<": "jge",
        "<=": "jg",
        ">": "jle",
        ">=": "jl",
        "is": "jne",
        "is not": "je",
    }
    # For ucomisd: signed-comparison setcc/jcc don't work because the result
    # flags are unordered/equal/below/above. Use the unsigned variants.
    SETCC_FLOAT = {
        "==": "sete",
        "!=": "setne",
        "<": "setb",
        "<=": "setbe",
        ">": "seta",
        ">=": "setae",
    }
    JCC_INV_FLOAT = {
        "==": "jne",
        "!=": "je",
        "<": "jae",
        "<=": "ja",
        ">": "jbe",
        ">=": "jb",
    }

    def _gen_compare(self, e: A.Compare, info: FuncInfo) -> None:
        # `needle in haystack` where haystack is a list or dict.
        if len(e.ops) == 1 and e.ops[0] in ("in", "not in"):
            # User instance with __contains__: dispatch to the method.
            if getattr(e, "dunder_contains_owner", None) is not None:
                owner = e.dunder_contains_owner  # type: ignore[attr-defined]
                negate = getattr(e, "dunder_contains_negate", False)
                needle_off = info.locals_[f"__contains_needle_{id(e)}"]
                self.gen_expr(e.operands[0], info)            # needle -> rax
                self.emitf(f"mov [rbp{needle_off:+d}], rax")
                self.gen_expr(e.operands[1], info)            # container -> rax
                self.emitf(
                    f"mov {self._arg_reg(1)}, [rbp{needle_off:+d}]",
                    f"mov {self._arg_reg(0)}, rax",
                )
                self.emit_call(self._method_symbol(owner, "__contains__"))
                if negate:
                    self.emitf("xor rax, 1")
                return
            rt = A.expr_type(e.operands[1])
            if rt in ("list", "tuple"):
                # Tuples share the list [cap,len,buf] layout, so the linear
                # scan is identical once we know the element kind.
                self._gen_list_in(e, info)
                return
            if rt in ("dict", "set"):
                # A set is a dict keyed by its members, so membership is the
                # same str-key lookup.
                self._gen_dict_in(e, info)
                return
            if (rt in ("any", "int") or rt.startswith("instance:")) and A.expr_type(
                e.operands[0]
            ) in ("str", "any", "int"):
                # Membership against an opaque value (`any`) or the unknown-`int`
                # sentinel: dict-backed lowering.
                self._gen_dict_in(e, info)
                return
        # String compare: ==/!= and in/not in dispatch to runtime helpers.
        # One side may be opaque (`any`) — a str at runtime that sema couldn't
        # pin (e.g. `tok.value == "("`): content equality is the only correct
        # lowering, since heap-built strings never share pointers with interned
        # literals.
        lt0 = A.expr_type(e.operands[0])
        rt0 = A.expr_type(e.operands[1])
        # `instance == instance` (or `!=`) dispatched by sema to a
        # user-defined `__eq__` (dunder_owner stamped on the node): park lhs
        # across rhs evaluation like a binop dunder, call __eq__, and negate
        # for `!=` (CPython's default `__ne__` is `not __eq__`).
        if (
            len(e.ops) == 1
            and e.ops[0] in ("==", "!=")
            and getattr(e, "dunder_owner", None) is not None
        ):
            owner = e.dunder_owner  # type: ignore[attr-defined]
            method = e.dunder_method  # type: ignore[attr-defined]
            slot_off = info.locals_[f"__cmpeq_lhs_{id(e)}"]
            self.gen_expr(e.operands[0], info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
            self.gen_expr(e.operands[1], info)
            self.emitf(
                f"mov {self._arg_reg(1)}, rax",
                f"mov {self._arg_reg(0)}, [rbp{slot_off:+d}]",
            )
            self.emit_call(self._method_symbol(owner, method))
            if getattr(e, "dunder_negate", False):
                self.emitf("xor rax, 1")
            return
        if (
            len(e.ops) == 1
            and e.ops[0] in ("<", "<=", ">", ">=")
            and getattr(e, "dunder_owner", None) is not None
        ):
            # Ordering compare dispatched to __lt__/__le__/__gt__/__ge__.
            owner = e.dunder_owner  # type: ignore[attr-defined]
            method = e.dunder_method  # type: ignore[attr-defined]
            reflected = getattr(e, "dunder_reflected", False)
            slot_off = info.locals_[f"__cmpord_lhs_{id(e)}"]
            self_expr = e.operands[1] if reflected else e.operands[0]
            other_expr = e.operands[0] if reflected else e.operands[1]
            self.gen_expr(self_expr, info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
            self.gen_expr(other_expr, info)
            self.emitf(
                f"mov {self._arg_reg(1)}, rax",
                f"mov {self._arg_reg(0)}, [rbp{slot_off:+d}]",
            )
            self.emit_call(self._method_symbol(owner, method))
            return
        if len(e.ops) == 1 and lt0 == "set" and rt0 == "set" and e.ops[0] in ("<=", ">=", "<", ">"):
            # Set subset/superset comparisons (PEP-3119-style: `a <= b` is
            # `a.issubset(b)`, `a < b` is a proper subset i.e. subset AND
            # a != b, and `>=`/`>` are the mirror via swapped operands).
            # Previously unhandled here, so these fell through to the
            # generic `cmp rax, rbx` integer path below — silently doing a
            # raw pointer-VALUE comparison of the two set headers instead
            # of any subset logic, a real correctness bug (not a crash by
            # itself, but capable of flipping which branch downstream code
            # takes wherever it's used, e.g. `if not free <= available:`
            # in program.py's whole-program module merging).
            op = e.ops[0]
            swap = op in (">=", ">")
            sub_expr, sup_expr = (e.operands[1], e.operands[0]) if swap else (e.operands[0], e.operands[1])
            slot_off = info.locals_[f"__setcmp_{id(e)}"]
            self.gen_expr(sub_expr, info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
            self.gen_expr(sup_expr, info)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{slot_off:+d}]",
                "call _runtime_set_subset",
            )
            if op in ("<", ">"):
                # Proper subset/superset: subset holds AND the two sets
                # aren't equal (same length is sufficient given subset
                # already held - a subset of equal length must be the
                # same set). Re-evaluating sub/sup here would duplicate
                # any side effects in their source expressions, so park
                # the subset-check 0/1 result and compare lengths via the
                # already-evaluated header pointers instead.
                eq_slot = info.locals_[f"__setcmp_eq_{id(e)}"]
                self.emitf(f"mov [rbp{eq_slot:+d}], rax")  # subset result
                self.gen_expr(sub_expr, info)
                self.emitf(f"mov [rbp{slot_off:+d}], rax")
                self.gen_expr(sup_expr, info)
                self.emitf(
                    f"mov rcx, [rbp{slot_off:+d}]",
                    f"mov rdx, [rax+{self.DICT_LEN_OFF}]",
                    f"mov rax, [rcx+{self.DICT_LEN_OFF}]",
                    "cmp rax, rdx",
                    "sete al",
                    "movzx rax, al",
                    "xor rax, 1",  # 1 if lengths differ (not equal)
                    f"and rax, [rbp{eq_slot:+d}]",
                )
            return
        if (
            len(e.ops) == 1
            and (
                (lt0 in ("str", "any") and rt0 in ("str", "any") and "str" in (lt0, rt0))
                or getattr(e, "_map_val_str_cmp", False)
            )
        ):
            op = e.ops[0]
            if op in ("==", "!="):
                slot_off = info.locals_[f"__strcmp_{id(e)}"]
                self.gen_expr(e.operands[0], info)
                self.emitf(f"mov [rbp{slot_off:+d}], rax")
                self.gen_expr(e.operands[1], info)
                self.emitf(
                    "mov rbx, rax",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "call _runtime_str_eq",
                )
                if op == "!=":
                    self.emitf("xor rax, 1")
                return
            if op in ("<", "<=", ">", ">="):
                # Lexicographic compare via _runtime_str_cmp -> -1/0/+1, then
                # cmp against 0 with the corresponding setcc.
                slot_off = info.locals_[f"__strcmp_{id(e)}"]
                self.gen_expr(e.operands[0], info)
                self.emitf(f"mov [rbp{slot_off:+d}], rax")
                self.gen_expr(e.operands[1], info)
                self.emitf(
                    "mov rbx, rax",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "call _runtime_str_cmp",
                )
                setcc = self.SETCC[op]
                self.emitf("cmp rax, 0", f"{setcc} al", "movzx rax, al")
                return
            if op in ("in", "not in"):
                # 'needle in haystack': lhs is the needle, rhs is the
                # haystack — but _runtime_str_contains takes (haystack, needle).
                slot_off = info.locals_[f"__strin_{id(e)}"]
                self.gen_expr(e.operands[0], info)  # rax = needle
                self.emitf(f"mov [rbp{slot_off:+d}], rax")
                self.gen_expr(e.operands[1], info)  # rax = haystack
                self.emitf(
                    f"mov rbx, [rbp{slot_off:+d}]",  # rbx = needle
                    "call _runtime_str_contains",
                )
                if op == "not in":
                    self.emitf("xor rax, 1")
                return
        # Determine if any operand is a float — if so, all comparisons in the
        # chain are float (with int promotion). For simplicity we treat the
        # whole chain as float if any one operand is float; otherwise int.
        # `is`/`is not` are identity (pointer) comparisons and must always use
        # the integer path regardless of operand type.
        is_float = False
        if not any(op in ("is", "is not") for op in e.ops):
            for o in e.operands:
                if A.expr_type(o) == "float":
                    is_float = True

        if len(e.ops) == 1:
            # Unhandled in/not in (e.g. list-in-list, or unusual LHS types):
            # fall back to dict-in, which works for any dict-backed collection.
            if e.ops[0] in ("in", "not in"):
                self._gen_dict_in(e, info)
                return
            if is_float:
                # rbp-relative spill, not a raw `sub rsp, 8` / `[rsp]` push
                # -- see _cl_walk_expr's __cmpfloat_ comment: an FFI call
                # (e.g. `x < math.pow(y, 2.0)`) evaluated for operands[1]
                # would otherwise corrupt the spilled LHS by adjusting rsp
                # itself for shadow space / stack-passed args.
                slot = info.locals_[f"__cmpfloat_{id(e)}_0"]
                self._gen_expr_as_float(e.operands[0], info, A.expr_type(e.operands[0]))
                self.emitf(f"movsd [rbp{slot:+d}], xmm0")
                self._gen_expr_as_float(e.operands[1], info, A.expr_type(e.operands[1]))
                self.emitf(f"movsd xmm1, xmm0", f"movsd xmm0, [rbp{slot:+d}]")
                setcc = self.SETCC_FLOAT[e.ops[0]]
                self.emitf("ucomisd xmm0, xmm1", f"{setcc} al", "movzx rax, al")
            else:
                self.gen_expr(e.operands[0], info)
                self.emitf("push rax")
                self.gen_expr(e.operands[1], info)
                self.emitf("mov rbx, rax", "pop rax")
                setcc = self.SETCC[e.ops[0]]
                self.emitf("cmp rax, rbx", f"{setcc} al", "movzx rax, al")
            return

        # Chained compares.
        false_lbl = self.fresh("cmp_false")
        end_lbl = self.fresh("cmp_end")

        if is_float:
            self._gen_expr_as_float(e.operands[0], info, A.expr_type(e.operands[0]))
            for i, op in enumerate(e.ops):
                # xmm0 = current LHS. rbp-relative spill (see the single-
                # compare branch above for why a raw rsp push isn't safe).
                slot = info.locals_[f"__cmpfloat_{id(e)}_{i}"]
                self.emitf(f"movsd [rbp{slot:+d}], xmm0")
                self._gen_expr_as_float(
                    e.operands[i + 1], info, A.expr_type(e.operands[i + 1])
                )
                self.emitf(f"movsd xmm1, xmm0", f"movsd xmm0, [rbp{slot:+d}]")
                jcc = self.JCC_INV_FLOAT[op]
                self.emitf("ucomisd xmm0, xmm1", f"{jcc} {false_lbl}")
                # Reuse xmm1 as next LHS.
                self.emitf("movsd xmm0, xmm1")
        else:
            self.gen_expr(e.operands[0], info)
            for i, op in enumerate(e.ops):
                self.emitf("push rax")
                self.gen_expr(e.operands[i + 1], info)
                self.emitf("mov rbx, rax", "pop rax")
                jcc = self.JCC_INV[op]
                self.emitf("cmp rax, rbx", f"{jcc} {false_lbl}")
                self.emitf("mov rax, rbx")
        # All passed: result = 1 (in RAX).
        self.emitf("mov rax, 1", f"jmp {end_lbl}")
        self.label(false_lbl)
        self.emitf("xor rax, rax")
        self.label(end_lbl)

    def _gen_list_in(self, e: A.Compare, info: FuncInfo) -> None:
        """`needle in list[T]` / `needle not in list[T]`.

        Linear scan over the list buffer comparing each element. For int
        lists the compare is a raw 8-byte equality; for str lists each
        element is strcmp'd against the needle; float uses ucomisd with
        NaN treated as never-equal.
        """
        op = e.ops[0]
        rhs = e.operands[1]
        if A.expr_type(rhs) == "tuple":
            # Sema guarantees a homogeneous tuple here, so any element kind
            # describes them all; default to int when the kinds are unknown.
            ets = [t for t in A.tuple_element_types(rhs) if t != "any"]
            el_t = ets[0] if ets else "int"
        elif isinstance(rhs, A.ListLit):
            el_t = rhs.el_type
        elif isinstance(rhs, A.Name):
            el_t = rhs.list_el_type
        else:
            el_t = "int"

        slot_off = info.locals_[f"__listin_{id(e)}"]
        if el_t == "float":
            self._gen_expr_as_float(e.operands[0], info, A.expr_type(e.operands[0]))
            self.emitf(f"movsd [rbp{slot_off:+d}], xmm0")
        else:
            self.gen_expr(e.operands[0], info)
            self.emitf(f"mov [rbp{slot_off:+d}], rax")
        self.gen_expr(rhs, info)  # rax = header

        loop = self.fresh("listin_loop")
        found = self.fresh("listin_found")
        miss = self.fresh("listin_miss")
        end = self.fresh("listin_end")
        self.emitf(
            f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
            f"mov rdx, [rax+{self.LIST_BUF_OFF}]",
            "xor r8, r8",
        )
        self.label(loop)
        self.emitf("cmp r8, rcx", f"jge {miss}")
        if el_t == "str":
            # _runtime_str_eq clobbers caller-saved regs; spill rcx/rdx/r8.
            self.emitf(
                "mov r9, [rdx+r8*8]",
                "push rcx",
                "push rdx",
                "push r8",
                "sub rsp, 8",
                f"mov rax, [rbp{slot_off:+d}]",
                "mov rbx, r9",
                "call _runtime_str_eq",
                "add rsp, 8",
                "pop r8",
                "pop rdx",
                "pop rcx",
                "test rax, rax",
                f"jnz {found}",
            )
        elif el_t == "float":
            nan = self.fresh("listin_nan")
            self.emitf(
                "movsd xmm0, [rdx+r8*8]",
                f"movsd xmm1, [rbp{slot_off:+d}]",
                "ucomisd xmm0, xmm1",
                f"jp {nan}",
                f"je {found}",
            )
            self.label(nan)
        else:
            self.emitf(
                "mov r9, [rdx+r8*8]",
                f"cmp r9, [rbp{slot_off:+d}]",
                f"je {found}",
            )
        self.emitf("inc r8", f"jmp {loop}")

        self.label(found)
        self.emitf("mov rax, 1", f"jmp {end}")
        self.label(miss)
        self.emitf("xor rax, rax")
        self.label(end)
        if op == "not in":
            self.emitf("xor rax, 1")

    def _gen_dict_in(self, e: A.Compare, info: FuncInfo) -> None:
        """`key in dict` / `key not in dict`.

        Wraps the existing `_runtime_dict_contains` helper. The needle
        must be a str (sema enforces) — except for int-keyed sets, whose
        members are stored as their decimal string form (see `_gen_set_lit`),
        so an int needle is converted the same way before lookup.
        """
        op = e.ops[0]
        slot_off = info.locals_[f"__dictin_{id(e)}"]
        self.gen_expr(e.operands[0], info)  # rax = key ptr
        if A.expr_type(e.operands[1]) == "set" and A.expr_type(e.operands[0]) == "int":
            self._emit_int_to_str()
            self.emitf("call _runtime_str_concat_dup")
        self.emitf(f"mov [rbp{slot_off:+d}], rax")
        self.gen_expr(e.operands[1], info)  # rax = dict header
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]",
            "call _runtime_dict_contains",
        )
        if op == "not in":
            self.emitf("xor rax, 1")

    def _gen_boolop(self, e: A.BoolOp, info: FuncInfo) -> None:
        # Short-circuit, Python value semantics: `a and b and c` yields the
        # first falsy operand's VALUE, or the last operand if all are truthy
        # (symmetric for `or`/truthy). No 0/1 normalization — `x or []` must
        # produce the operand pointer, not a boolean.
        #
        # `A.expr_type` of a *nested* BoolOp node falls back to "int" when its
        # two arms have different static types (e.g. `(d is None) and
        # defaults`: Compare is "int", defaults is "list") even though the
        # value it can actually produce at runtime is the list. Trusting that
        # fallback for the truthiness check would test a list pointer for
        # nonzero instead of emptiness. So: flatten the run of same-operator
        # BoolOps into its real leaf operands first and type-check each leaf
        # individually — every leaf's own expr_type is trustworthy, only the
        # synthetic intermediate BoolOp nodes' types are not.
        operands: list = []

        def flatten_boolop_chain(node) -> None:
            if isinstance(node, A.BoolOp) and node.op == e.op:
                flatten_boolop_chain(node.left)
                flatten_boolop_chain(node.right)
            else:
                operands.append(node)

        flatten_boolop_chain(e)

        end = self.fresh("bool_end")
        slot = info.locals_[f"__boolop_{id(e)}"]
        stop_truthy = e.op == "or"  # `or` stops (and returns) on a truthy value
        for idx, operand in enumerate(operands[:-1]):
            t = A.expr_type(operand)
            stop_lbl = self.fresh("boolop_stop")
            cont_lbl = self.fresh("boolop_cont")
            if t == "float":
                self._gen_expr_as_float(operand, info, t)
                self.emitf(f"movsd [rbp{slot:+d}], xmm0")
                zero_lbl = self.intern_float(0.0)
                past_nan = self.fresh("not_nan")
                # NaN is truthy in Python: route it to whichever label
                # "truthy" means for this operator before the zero-compare.
                truthy_lbl = stop_lbl if stop_truthy else cont_lbl
                self.emitf(
                    f"movsd xmm1, [{zero_lbl}]",
                    "ucomisd xmm0, xmm1",
                    f"jp {truthy_lbl}",
                    f"je {cont_lbl if stop_truthy else stop_lbl}",
                )
                self.label(past_nan)
                self.emitf(f"jmp {truthy_lbl}")
            else:
                self.gen_expr(operand, info)
                self.emitf(f"mov [rbp{slot:+d}], rax")
                truthy_lbl = stop_lbl if stop_truthy else cont_lbl
                falsy_lbl = cont_lbl if stop_truthy else stop_lbl
                if t.startswith("instance:"):
                    cls_name = t.split(":", 1)[1]
                    tested = False
                    for mname in ("__bool__", "__len__"):
                        owner = self._resolve_method_owner(cls_name, mname)
                        if owner is not None:
                            self.emitf(f"mov {self._arg_reg(0)}, rax")
                            self.emit_call(self._method_symbol(owner, mname))
                            self.emitf("test rax, rax", f"jz {falsy_lbl}")
                            tested = True
                            break
                    if not tested:
                        pass  # always truthy: fall straight through
                elif t in ("list", "tuple", "dict", "set"):
                    self.emitf(
                        "test rax, rax", f"jz {falsy_lbl}",
                        "mov rax, [rax+8]", "test rax, rax", f"jz {falsy_lbl}",
                    )
                elif t == "str":
                    self.emitf(
                        "test rax, rax", f"jz {falsy_lbl}",
                        "movzx rax, byte [rax]", "test rax, rax", f"jz {falsy_lbl}",
                    )
                else:
                    self.emitf("test rax, rax", f"jz {falsy_lbl}")
                self.emitf(f"jmp {truthy_lbl}")
            self.label(stop_lbl)
            if t == "float":
                self.emitf(f"movsd xmm0, [rbp{slot:+d}]")
            else:
                self.emitf(f"mov rax, [rbp{slot:+d}]")
            self.emitf(f"jmp {end}")
            self.label(cont_lbl)
        self.gen_expr(operands[-1], info)
        self.label(end)

    def _gen_ifexp(self, e: A.IfExp, info: FuncInfo) -> None:
        """Conditional expression `body if test else orelse`.

        Result type (set by sema) decides the register class: floats land in
        xmm0, everything else in rax. Each arm is promoted to that class so
        the join point sees a uniform value.
        """
        ty = e.inferred_type
        else_lbl = self.fresh("condelse")
        end_lbl = self.fresh("condend")
        self._gen_truthy_test(e.test, info, else_lbl)
        if ty == "float":
            self._gen_expr_as_float(e.body, info, A.expr_type(e.body))
        else:
            self.gen_expr(e.body, info)
        self.emitf(f"jmp {end_lbl}")
        self.label(else_lbl)
        if ty == "float":
            self._gen_expr_as_float(e.orelse, info, A.expr_type(e.orelse))
        else:
            self.gen_expr(e.orelse, info)
        self.label(end_lbl)

    def _emit_positional_args(
        self,
        e: "A.Call | A.MethodCall",
        args: list,
        info: FuncInfo,
        *,
        start_reg,
        receiver_expr=None,
        receiver_slot=None,
    ) -> int:
        """Evaluate a call's receiver (if any) and `args` into pre-reserved
        frame slots, then load them into the ABI argument registers; arguments
        beyond the register count are placed on the stack per the ABI.

        Returns the number of bytes the caller must `add rsp` after the call to
        undo any stack-argument area (0 when everything fit in registers). The
        caller emits that cleanup right after `emit_call`.

        Using frame slots for evaluation (instead of push/pop) keeps rsp 16-byte
        aligned throughout — essential because an argument's own evaluation may
        emit a `call` (malloc for a literal, string concat, a nested call), and
        a stray push would misalign the stack for that inner call. The stack
        arguments are written only at the very end, after all evaluation, in a
        single rsp adjustment that preserves 16-byte alignment.
        """
        offs = self._eval_call_operands(
            e, args, info, receiver_expr=receiver_expr, receiver_slot=receiver_slot
        )
        return self._load_call_operands(
            e,
            offs,
            info,
            start_reg=start_reg,
            receiver_slot=receiver_slot,
            arg_types=[A.expr_type(a) for a in args],
        )

    def _eval_call_operands(
        self,
        e,
        args: list,
        info: FuncInfo,
        *,
        receiver_expr=None,
        receiver_slot=None,
    ) -> list:
        """Evaluate a call's receiver (if any) and args into their pre-reserved
        frame slots. Returns the arg slot offsets. Split from the register-load
        phase so virtual dispatch can read the receiver's runtime class id (a
        runtime call that clobbers argument registers) in between."""
        if receiver_expr is not None:
            self.gen_expr(receiver_expr, info)
            self.emitf(f"mov [rbp{receiver_slot:+d}], rax")
        offs: list[int] = []
        for i, a in enumerate(args):
            self.gen_expr(a, info)
            if A.expr_type(a) == "float":
                self.emitf("movq rax, xmm0")
            off = info.locals_[f"__callarg_{id(e)}_{i}"]
            self.emitf(f"mov [rbp{off:+d}], rax")
            offs.append(off)
        return offs

    def _load_call_operands(
        self,
        e,
        offs: list,
        info: FuncInfo,
        *,
        start_reg,
        receiver_slot=None,
        arg_types: Optional[list] = None,
    ) -> int:
        # All evaluation done — no more `call`s until the real one, so it's safe
        # to adjust rsp and load the argument registers.
        if arg_types is None:
            arg_types = []
            _at_i = 0
            while _at_i < len(offs):
                arg_types.append("int")
                _at_i = _at_i + 1
        # Per-position types: positions [0, start_reg) are the receiver/cls
        # slot (always a pointer, "int"), followed by the real arguments.
        # _assign_arg_regs maps each position to its ABI register (or None
        # for stack-passed), mirroring how _spill_incoming_args places the
        # callee's params.
        _reg_types: list[str] = []
        _rr = 0
        while _rr < start_reg:
            _reg_types.append("int")
            _rr = _rr + 1
        assigns = self._assign_arg_regs(_reg_types + list(arg_types))

        # Parallel lists, not a list of (reg, is_xmm, off) tuples: sema can't
        # infer a per-slot tuple shape for a list that starts empty and gets
        # tuples appended later (only a tuple-element *literal* list gets
        # that treatment) -- the unpacked `off` in `for reg, is_xmm, off in
        # reg_loads:` then defaulted to "any", which skipped _gen_fstring_
        # segment's int->str conversion entirely and fed the raw frame-slot
        # offset integer straight into _runtime_str_concat as if it were
        # already a string pointer (strlen() on a small negative integer).
        # Confirmed via gdb on a selfhost rebuild crashing on every call to
        # a user function taking >=1 argument.
        stack_offs: list[int] = []
        reg_loads_reg: list[str] = []
        reg_loads_is_xmm: list = []
        reg_loads_off: list[int] = []
        for pos, off in enumerate(offs, start=start_reg):
            assign = assigns[pos]
            if assign is None:
                stack_offs.append(off)
            else:
                reg, is_xmm = assign
                reg_loads_reg.append(reg)
                reg_loads_is_xmm.append(is_xmm)
                reg_loads_off.append(off)

        # Stack-passed arguments first (they use rax as scratch): reserve a
        # 16-aligned area (Win64 also needs 32 bytes of shadow space below the
        # args) and write them low-to-high, so the k-th stack arg lands where
        # the callee's prologue reads it. Then load the register args — done
        # last so the rsp adjustment / rax scratch can't clobber them.
        cleanup = 0
        if stack_offs:
            shadow = self._caller_shadow_space()
            area = shadow + 8 * len(stack_offs)
            if area % 16:
                area += 16 - (area % 16)
            cleanup = area
            self.emitf(f"sub rsp, {area}")
            for k, off in enumerate(stack_offs):
                self.emitf(
                    f"mov rax, [rbp{off:+d}]",
                    f"mov [rsp+{shadow + 8 * k}], rax",
                )

        if receiver_slot is not None:
            reg, _is_xmm = assigns[0]  # receiver is always a pointer
            self.emitf(f"mov {reg}, [rbp{receiver_slot:+d}]")
        for i in range(len(reg_loads_reg)):
            reg = reg_loads_reg[i]
            is_xmm = reg_loads_is_xmm[i]
            off = reg_loads_off[i]
            if is_xmm:
                self.emitf(f"movsd {reg}, [rbp{off:+d}]")
            else:
                self.emitf(f"mov {reg}, [rbp{off:+d}]")
        return cleanup

    def _caller_shadow_space(self) -> int:
        """Bytes of shadow ("home") space the caller must reserve below the
        stack arguments. 32 on Win64, 0 on SysV. Overridden per target."""
        return 0

    def _gen_call(self, e: A.Call, info: FuncInfo) -> None:
        # FFI: bare-imported foreign function. The args may need int->float
        # promotion to match the declared signature.
        if e.func in self.ffi_funcs:
            self._gen_ffi_call(self.ffi_funcs[e.func], e.args, info)
            return
        if e.func == "print":
            self._gen_print(e, info)
            return
        if e.func == "id":
            # id(x): the object's identity. Every asmpython value is an 8-byte
            # slot (a pointer for heap objects), so the value itself in rax is a
            # stable, unique-per-object integer — exactly id() semantics.
            self.gen_expr(e.args[0], info)
            return
        if e.func == "range":
            # range(...) used as a value: materialize a list[int]. (In a `for`
            # header range is lowered specially and never reaches here.) Spill
            # start/stop into frame slots across the arg evals, then call the
            # runtime materializer with start=rax, stop=rbx, step=rcx.
            a_slot = info.locals_[f"__range_a_{id(e)}"]
            b_slot = info.locals_[f"__range_b_{id(e)}"]
            if len(e.args) == 1:
                self.emitf("xor rax, rax", f"mov [rbp{a_slot:+d}], rax")  # start = 0
                self.gen_expr(e.args[0], info)
                self.emitf(f"mov [rbp{b_slot:+d}], rax")  # stop
                self.emitf("mov rcx, 1")  # step
            elif len(e.args) == 2:
                self.gen_expr(e.args[0], info)
                self.emitf(f"mov [rbp{a_slot:+d}], rax")
                self.gen_expr(e.args[1], info)
                self.emitf(f"mov [rbp{b_slot:+d}], rax")
                self.emitf("mov rcx, 1")
            else:
                self.gen_expr(e.args[0], info)
                self.emitf(f"mov [rbp{a_slot:+d}], rax")
                self.gen_expr(e.args[1], info)
                self.emitf(f"mov [rbp{b_slot:+d}], rax")
                self.gen_expr(e.args[2], info)
                self.emitf("mov rcx, rax")
            self.emitf(
                f"mov rax, [rbp{a_slot:+d}]",
                f"mov rbx, [rbp{b_slot:+d}]",
                "call _runtime_range_list",
            )
            return
        if e.func == "len":
            arg = e.args[0]
            self.gen_expr(arg, info)  # rax = ptr
            t = A.expr_type(arg)
            if t in ("list", "tuple"):
                # Tuples reuse the list layout, so len lives at LIST_LEN_OFF.
                self.emitf(f"mov rax, [rax+{self.LIST_LEN_OFF}]")
            elif t in ("dict", "set"):
                # Sets are dict-backed; len lives at DICT_LEN_OFF.
                self.emitf(f"mov rax, [rax+{self.DICT_LEN_OFF}]")
            elif t.startswith("instance:"):
                cls_name = t.split(":", 1)[1]
                owner = self._resolve_method_owner(cls_name, "__len__")
                if owner is not None:
                    self.emitf(f"mov {self._arg_reg(0)}, rax")
                    self.emit_call(self._method_symbol(owner, "__len__"))
                else:
                    self.emitf("xor rax, rax")
            else:
                self._emit_strlen()  # rax = length (string)
            return
        if e.func == "str":
            arg_t = A.expr_type(e.args[0])
            self.gen_expr(e.args[0], info)
            # int/float conversions land in the shared static itoa buffer; str()
            # results are commonly stored (e.g. `[str(x) for x in xs]`), so copy
            # out to a fresh allocation to avoid every result aliasing the buffer.
            if arg_t in ("list", "tuple", "dict", "set"):
                # str(container) == repr(container) for these built-ins.
                self._emit_container_repr(e.args[0], arg_t)
            elif arg_t == "float":
                # xmm0 has the value; print into our int_to_str buffer via sprintf.
                self._emit_float_to_str()  # rax = ptr (shared buffer)
                self.emitf("call _runtime_str_concat_dup")
            elif arg_t == "str":
                pass  # already a str ptr in rax
            elif arg_t.startswith("instance:"):
                resolved = self._resolve_str_dunder(arg_t.split(":", 1)[1])
                if resolved is not None:
                    owner, method = resolved
                    self.emitf(f"mov {self._arg_reg(0)}, rax")
                    self.emit_call(self._method_symbol(owner, method))
                else:
                    self._emit_int_to_str()  # rax = ptr to ASCII
                    self.emitf("call _runtime_str_concat_dup")
            elif arg_t == "int" and A.is_bool_expr(e.args[0]):
                self._emit_bool_to_str()
                self.emitf("call _runtime_str_concat_dup")
            elif arg_t == "int" and A.is_none_expr(e.args[0]):
                self.emitf("lea rax, [_runtime_none_str]", "call _runtime_str_concat_dup")
            else:
                self._emit_int_to_str()  # rax = ptr to ASCII (shared buffer)
                self.emitf("call _runtime_str_concat_dup")
            return
        if e.func == "int":
            arg_t = A.expr_type(e.args[0])
            if len(e.args) == 2:
                # int(s, base): parse the string in the given radix via strtoll.
                # base 0 auto-detects 0x / 0o / 0b prefixes (matches CPython).
                base_slot = info.locals_[f"__int_base_{id(e)}"]
                self.gen_expr(e.args[1], info)  # rax = base (int)
                self.emitf(f"mov [rbp{base_slot:+d}], rax")
                self.gen_expr(e.args[0], info)  # rax = string ptr
                self.emitf(f"mov rbx, [rbp{base_slot:+d}]")
                self._emit_str_to_int_base()  # rax = parsed int
                return
            self.gen_expr(e.args[0], info)
            if arg_t == "str":
                self._emit_str_to_int()
            elif arg_t == "float":
                # Truncate toward zero (Python's int(float) semantics).
                self.emitf("cvttsd2si rax, xmm0")
            # int -> int: nothing to do.
            return
        if e.func == "float":
            arg_t = A.expr_type(e.args[0])
            # float("nan") / float("inf") / float("-inf"): emit bit patterns
            # directly so the result is correct regardless of libc strtod quirks.
            if isinstance(e.args[0], A.StrLit):
                s = e.args[0].value.strip().lower()
                if s == "nan":
                    self.emitf(
                        "mov rax, 0x7FF8000000000000", "movq xmm0, rax"
                    )
                    return
                if s in ("inf", "+inf", "infinity", "+infinity"):
                    self.emitf(
                        "mov rax, 0x7FF0000000000000", "movq xmm0, rax"
                    )
                    return
                if s in ("-inf", "-infinity"):
                    self.emitf(
                        "mov rax, 0xFFF0000000000000", "movq xmm0, rax"
                    )
                    return
            self.gen_expr(e.args[0], info)
            if arg_t == "str":
                self._emit_str_to_float()  # xmm0 = parsed
            elif arg_t == "int":
                self.emitf("cvtsi2sd xmm0, rax")
            # float -> float: nothing to do.
            return
        if e.func == "input":
            if e.args:
                # Print the prompt first; no trailing newline.
                self.gen_expr(e.args[0], info)
                self._emit_print_str_ptr_no_newline()
            self._emit_input_line()  # rax = ptr to read buffer
            return
        if e.func == "getattr":
            # getattr(obj, name[, default]) -> dict_get_default(obj, name,
            # default-or-0). Instances are dicts keyed by field name, so this is
            # the same helper that plain `obj.name` uses, just with a caller-
            # supplied default. A literal name is interned like `obj.name`; a
            # dynamic (runtime string) name is hashed the same way by the dict
            # backend, so it works identically — just evaluated, not interned.
            name_slot = info.locals_[f"__getattr_name_{id(e)}"]
            if isinstance(e.args[1], A.StrLit):
                key_label, _ = self.intern_string(e.args[1].value)
                self.emitf(f"lea rax, [{key_label}]", f"mov [rbp{name_slot:+d}], rax")
            else:
                self.gen_expr(e.args[1], info)
                self.emitf(f"mov [rbp{name_slot:+d}], rax")
            if len(e.args) == 3:
                dslot = info.locals_[f"__getattr_def_{id(e)}"]
                self.gen_expr(e.args[2], info)
                if A.expr_type(e.args[2]) == "float":
                    self.emitf("movq rax, xmm0")
                self.emitf(f"mov [rbp{dslot:+d}], rax")
                self.gen_expr(e.args[0], info)  # rax = instance dict
                self.emitf(
                    f"mov rbx, [rbp{name_slot:+d}]",
                    f"mov rcx, [rbp{dslot:+d}]",
                    "call _runtime_dict_get_default",
                )
            else:
                self.gen_expr(e.args[0], info)  # rax = instance dict
                self.emitf(
                    f"mov rbx, [rbp{name_slot:+d}]",
                    "xor rcx, rcx",
                    "call _runtime_dict_get_default",
                )
            return
        if e.func == "gl_resolve":
            # gl_resolve(handle, "funcName") -> int: force the lazy resolve-
            # and-cache _gen_dynamic_call normally does on a function's
            # first real call (see gl_import()'s docstring for why
            # resolution is lazy at all), without calling through the
            # pointer -- for functions whose stub exists only to register
            # a pointer for a hand-marshalled helper to use directly (e.g.
            # glShaderSource, called through gl_shader_source_1 instead of
            # any @handle.imported dispatch, since its real char**
            # signature doesn't fit that marshalling).
            if not isinstance(e.args[1], A.StrLit):
                raise NotImplementedError(
                    "gl_resolve()'s second argument must be a string literal"
                )
            name_label, _ = self.intern_string(e.args[1].value)
            dict_slot = info.locals_[f"__glresolve_dict_{id(e)}"]
            ptr_slot = info.locals_[f"__glresolve_ptr_{id(e)}"]
            self.gen_expr(e.args[0], info)  # rax = handle dict
            self.emitf(f"mov [rbp{dict_slot:+d}], rax")
            self.emitf(f"lea rbx, [{name_label}]", "xor rcx, rcx", "call _runtime_dict_get_default")
            self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
            not_null = self.fresh("glresolve_cached")
            self.emitf("test rax, rax", f"jnz {not_null}")
            self.emitf(f"lea rax, [{name_label}]")
            self._emit_get_gl_proc_addr()  # rax = resolved function ptr, or NULL
            self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
            self.emitf(
                "mov rcx, rax",
                f"lea rbx, [{name_label}]",
                f"mov rax, [rbp{dict_slot:+d}]",
                "call _runtime_dict_set",
            )
            self.label(not_null)
            self.emitf(f"mov rax, [rbp{ptr_slot:+d}]")
            return
        if e.func == "hasattr":
            # hasattr(obj, name) -> dict_contains(obj, name) (0/1). Name may be
            # a literal or a runtime string, same as getattr above.
            name_slot = info.locals_[f"__hasattr_name_{id(e)}"]
            if isinstance(e.args[1], A.StrLit):
                key_label, _ = self.intern_string(e.args[1].value)
                self.emitf(f"lea rax, [{key_label}]", f"mov [rbp{name_slot:+d}], rax")
            else:
                self.gen_expr(e.args[1], info)
                self.emitf(f"mov [rbp{name_slot:+d}], rax")
            self.gen_expr(e.args[0], info)  # rax = instance dict
            self.emitf(
                f"mov rbx, [rbp{name_slot:+d}]",
                "call _runtime_dict_contains",
            )
            return
        if e.func == "setattr":
            # setattr(obj, name, value) -> dict_set(obj, name, value); result
            # (Python's None) is the int 0. Name may be a literal or a runtime
            # string; the dict backend hashes either the same way `obj.name = v`
            # would for a literal field name.
            name_slot = info.locals_[f"__setattr_name_{id(e)}"]
            val_slot = info.locals_[f"__setattr_val_{id(e)}"]
            if isinstance(e.args[1], A.StrLit):
                key_label, _ = self.intern_string(e.args[1].value)
                self.emitf(f"lea rax, [{key_label}]", f"mov [rbp{name_slot:+d}], rax")
            else:
                self.gen_expr(e.args[1], info)
                self.emitf(f"mov [rbp{name_slot:+d}], rax")
            self.gen_expr(e.args[2], info)
            if A.expr_type(e.args[2]) == "float":
                self.emitf("movq rax, xmm0")
            self.emitf(f"mov [rbp{val_slot:+d}], rax")
            self.gen_expr(e.args[0], info)  # rax = instance dict
            self.emitf(
                f"mov rbx, [rbp{name_slot:+d}]",
                f"mov rcx, [rbp{val_slot:+d}]",
                "call _runtime_dict_set",
            )
            self.emitf("xor rax, rax")
            return
        if e.func == "isinstance":
            self._gen_isinstance(e, info)
            return
        if e.func in ("bytes", "bytearray"):
            # bytes()/bytearray() -> list[int] of character codes.
            # bytes()            -> empty list
            # bytes(lst: list)   -> shallow copy (passthrough via list slice)
            # bytes(s: str)      -> list of ord values (inline loop)
            # bytes(n: int)      -> n zeros (inline loop)
            if not e.args:
                # bytes() -> empty list
                self._emit_malloc(self.LIST_HEADER)
                self.emitf(
                    "mov qword [rax], 0",
                    "mov qword [rax+8], 0",
                    "mov qword [rax+16], 0",
                )
            else:
                arg = e.args[0]
                at = A.expr_type(arg)
                if at == "list":
                    # Already a list[int] - copy it via full slice
                    SENTINEL_MIN = "0x8000000000000000"
                    SENTINEL_MAX = "0x7fffffffffffffff"
                    self.gen_expr(arg, info)
                    self.emitf(
                        f"mov rbx, {SENTINEL_MIN}",
                        f"mov rcx, {SENTINEL_MAX}",
                        "call _runtime_list_slice",
                    )
                elif at == "str":
                    # bytes(str) -> list of ord values via inline loop.
                    # Strings are plain C strings (null-terminated, no header),
                    # so use strlen to get the count, then walk byte-by-byte.
                    list_slot = info.locals_[f"__bytes_list_{id(e)}"]
                    str_slot = info.locals_[f"__bytes_str_{id(e)}"]
                    idx_slot = info.locals_[f"__bytes_idx_{id(e)}"]
                    lbl_loop = self.fresh("bytes_str_loop")
                    lbl_done = self.fresh("bytes_str_done")
                    # Allocate empty list header
                    self._emit_malloc(self.LIST_HEADER)
                    self.emitf(
                        "mov qword [rax], 0",
                        "mov qword [rax+8], 0",
                        "mov qword [rax+16], 0",
                        f"mov [rbp{list_slot:+d}], rax",
                    )
                    # Evaluate string arg (a plain C-string pointer), save it
                    self.gen_expr(arg, info)
                    self.emitf(f"mov [rbp{str_slot:+d}], rax")
                    # Get string length; _emit_libc_strlen expects rax = ptr,
                    # returns length in rax.
                    self._emit_libc_strlen()         # rax = strlen(str)
                    # Store remaining count in idx_slot (counts down to 0)
                    self.emitf(f"mov [rbp{idx_slot:+d}], rax")
                    # Walk: advance str_slot pointer, appending byte values
                    self.emitf(f"{lbl_loop}:")
                    self.emitf(
                        f"cmp qword [rbp{idx_slot:+d}], 0",
                        f"jle {lbl_done}",
                        f"mov rdx, [rbp{str_slot:+d}]",
                        "movzx rbx, byte [rdx]",    # rbx = next byte
                        "inc rdx",
                        f"mov [rbp{str_slot:+d}], rdx",
                        f"dec qword [rbp{idx_slot:+d}]",
                        f"mov rax, [rbp{list_slot:+d}]",
                        "call _runtime_list_append",
                        f"jmp {lbl_loop}",
                    )
                    self.emitf(f"{lbl_done}:")
                    self.emitf(f"mov rax, [rbp{list_slot:+d}]")
                else:
                    # bytes(n: int) -> list of n zeros
                    list_slot = info.locals_[f"__bytes_list_{id(e)}"]
                    cnt_slot = info.locals_[f"__bytes_cnt_{id(e)}"]
                    lbl_loop = self.fresh("bytes_int_loop")
                    lbl_done = self.fresh("bytes_int_done")
                    # Allocate empty list
                    self._emit_malloc(self.LIST_HEADER)
                    self.emitf(
                        "mov qword [rax], 0",
                        "mov qword [rax+8], 0",
                        "mov qword [rax+16], 0",
                        f"mov [rbp{list_slot:+d}], rax",
                    )
                    # Evaluate n and save
                    self.gen_expr(arg, info)
                    self.emitf(f"mov [rbp{cnt_slot:+d}], rax")
                    # Loop n times appending 0
                    self.emitf(f"{lbl_loop}:")
                    self.emitf(
                        f"cmp qword [rbp{cnt_slot:+d}], 0",
                        f"jle {lbl_done}",
                        f"mov rax, [rbp{list_slot:+d}]",
                        "mov rbx, 0",
                        "call _runtime_list_append",
                        f"dec qword [rbp{cnt_slot:+d}]",
                        f"jmp {lbl_loop}",
                    )
                    self.emitf(f"{lbl_done}:")
                    self.emitf(f"mov rax, [rbp{list_slot:+d}]")
            return
        if e.func in ("list", "tuple"):
            # tuple(x) and list(x) share the copy: same heap layout.
            self._gen_list_call(e, info)
            return
        if e.func in ("set", "frozenset"):
            self._gen_set_call(e, info)
            return
        if e.func in BUILTIN_EXCEPTIONS:
            # `ValueError("msg")` etc. as a value: asmpython exceptions are
            # message strings, so the constructed exception IS its message
            # (or the exception's name when constructed without one).
            if e.args:
                self.gen_expr(e.args[0], info)
            else:
                lbl, _ = self.intern_string(e.func)
                self.emitf(f"lea rax, [{lbl}]")
            return
        if e.func == "dict":
            # dict() -> fresh empty dict; dict(other) -> empty + merge other in
            # (a shallow copy via the same helper dict.update uses).
            # dict(list_of_pairs) -> iterate pairs and insert each (k, v).
            res = info.locals_[f"__dictcall_res_{id(e)}"]
            if e.args and getattr(e, "dict_from_pairs", False):
                # Build from list of 2-element tuples/lists.
                it_slot = info.locals_[f"__dictpairs_it_{id(e)}"]
                stop_slot = info.locals_[f"__dictpairs_stop_{id(e)}"]
                idx_slot = info.locals_[f"__dictpairs_idx_{id(e)}"]
                key_slot = info.locals_[f"__dictpairs_key_{id(e)}"]
                self.gen_expr(e.args[0], info)
                self.emitf(
                    f"mov [rbp{it_slot:+d}], rax",
                    f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
                    f"mov [rbp{stop_slot:+d}], rbx",
                    f"mov qword [rbp{idx_slot:+d}], 0",
                )
                self._emit_empty_set(res)
                self.emitf(f"mov rax, [rbp{res:+d}]")
                top = self.fresh("dfrpairs")
                end = self.fresh("enddfrpairs")
                self.label(top)
                self.emitf(
                    f"mov rax, [rbp{idx_slot:+d}]",
                    f"cmp rax, [rbp{stop_slot:+d}]",
                    f"jge {end}",
                )
                # pair = it.buf[idx] -> a tuple/list header
                self.emitf(
                    f"mov rbx, [rbp{it_slot:+d}]",
                    f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                    f"mov rcx, [rbp{idx_slot:+d}]",
                    "mov rax, [rbx+rcx*8]",
                )
                # key = pair.buf[0], val = pair.buf[1]
                self.emitf(
                    f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
                    "mov rcx, [rbx]",        # key = buf[0]
                    f"mov [rbp{key_slot:+d}], rcx",
                    "mov rcx, [rbx+8]",      # val = buf[1]
                    f"mov rbx, [rbp{key_slot:+d}]",
                    f"mov rax, [rbp{res:+d}]",
                    "call _runtime_dict_set",
                )
                self.emitf(f"inc qword [rbp{idx_slot:+d}]", f"jmp {top}")
                self.label(end)
                self.emitf(f"mov rax, [rbp{res:+d}]")
            elif e.args:
                src_slot = info.locals_[f"__dictcall_src_{id(e)}"]
                self.gen_expr(e.args[0], info)
                self.emitf(f"mov [rbp{src_slot:+d}], rax")
                self._emit_empty_set(res)  # an empty dict: same layout
                self.emitf(
                    f"mov rax, [rbp{res:+d}]",
                    f"mov rbx, [rbp{src_slot:+d}]",
                    "call _runtime_dict_update",
                )
            else:
                self._emit_empty_set(res)
                self.emitf(f"mov rax, [rbp{res:+d}]")
            return
        if e.func in ("all", "any"):
            # all(xs)/any(xs) over a list/tuple: scan the buffer testing each
            # raw 8-byte slot for truthiness. Register-only (no calls inside).
            is_all = e.func == "all"
            self.gen_expr(e.args[0], info)  # rax = list/tuple header
            loop = self.fresh("aa_loop")
            hit = self.fresh("aa_hit")
            end = self.fresh("aa_end")
            self.emitf(
                f"mov rdx, [rax+{self.LIST_LEN_OFF}]",
                f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
                "xor rcx, rcx",
            )
            self.label(loop)
            self.emitf(f"cmp rcx, rdx", f"jge {end}")
            self.emitf(
                "mov r8, [rbx+rcx*8]",
                "test r8, r8",
                (f"jz {hit}" if is_all else f"jnz {hit}"),
                "inc rcx",
                f"jmp {loop}",
            )
            self.label(end)
            # Ran off the end: all() -> 1 (no falsy found), any() -> 0.
            self.emitf(f"mov rax, {1 if is_all else 0}")
            done = self.fresh("aa_done")
            self.emitf(f"jmp {done}")
            self.label(hit)
            # Early exit: all() found falsy -> 0; any() found truthy -> 1.
            self.emitf(f"mov rax, {0 if is_all else 1}")
            self.label(done)
            return
        if e.func == "sum":
            # sum(xs[, start]): integer accumulation over a list/tuple buffer.
            # Use a frame slot for `start` to avoid push/pop misaligning rsp
            # across the list-literal malloc calls inside gen_expr(xs).
            start_slot = info.locals_.get(f"__sum_start_{id(e)}")
            if len(e.args) == 2:
                self.gen_expr(e.args[1], info)
                if start_slot is not None:
                    self.emitf(f"mov [rbp{start_slot:+d}], rax")
            self.gen_expr(e.args[0], info)
            loop = self.fresh("sum_loop")
            end = self.fresh("sum_end")
            self.emitf(
                f"mov rdx, [rax+{self.LIST_LEN_OFF}]",
                f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
                "xor rcx, rcx",
                "xor rax, rax",
            )
            self.label(loop)
            self.emitf(
                "cmp rcx, rdx",
                f"jge {end}",
                "add rax, [rbx+rcx*8]",
                "inc rcx",
                f"jmp {loop}",
            )
            self.label(end)
            if len(e.args) == 2 and start_slot is not None:
                self.emitf(f"add rax, [rbp{start_slot:+d}]")
            return
        if e.func in ("max", "min"):
            # max/min: the 2-arg scalar form compares directly; the 1-arg form
            # scans a list/tuple buffer. A `key=` callable or str elements
            # need the general scan (_emit_minmax_scan); plain int elements
            # keep the fast cmov loop.
            cmov = "cmovl" if e.func == "max" else "cmovg"
            sort_key = getattr(e, "sort_key", None)
            if len(e.args) == 2:
                self.gen_expr(e.args[0], info)
                self.emitf("push rax")
                self.gen_expr(e.args[1], info)
                self.emitf("mov rbx, rax", "pop rax", "cmp rax, rbx", f"{cmov} rax, rbx")
                return
            if len(e.args) >= 3:
                best_slot = info.locals_[f"__mmvar_best_{id(e)}"]
                # cmov meaning changes when we do cmp rax,rbx (candidate,best):
                # for max: cmovl rax,rbx → if candidate < best, take best
                # for min: cmovg rax,rbx → if candidate > best, take best
                cmov_var = "cmovl" if e.func == "max" else "cmovg"
                self.gen_expr(e.args[0], info)
                self.emitf(f"mov [rbp{best_slot:+d}], rax")
                for arg in e.args[1:]:
                    self.gen_expr(arg, info)
                    self.emitf(
                        f"mov rbx, [rbp{best_slot:+d}]",
                        "cmp rax, rbx",
                        f"{cmov_var} rax, rbx",
                        f"mov [rbp{best_slot:+d}], rax",
                    )
                self.emitf(f"mov rax, [rbp{best_slot:+d}]")
                return
            if len(e.args) == 1:
                arg = e.args[0]
                if isinstance(arg, A.Name):
                    el_kind = arg.list_el_type
                elif isinstance(arg, A.ListLit):
                    el_kind = arg.el_type
                else:
                    el_kind = "int"
                if sort_key is not None or el_kind == "str":
                    self._emit_minmax_scan(e, info, el_kind)
                    return
                self.gen_expr(arg, info)  # rax = list/tuple header
                loop = self.fresh("mx_loop")
                end = self.fresh("mx_end")
                self.emitf(
                    f"mov rdx, [rax+{self.LIST_LEN_OFF}]",
                    f"mov rbx, [rax+{self.LIST_BUF_OFF}]",
                    "mov rax, [rbx]",  # best = buf[0]
                    "mov rcx, 1",
                )
                self.label(loop)
                self.emitf(
                    "cmp rcx, rdx",
                    f"jge {end}",
                    "mov r8, [rbx+rcx*8]",
                    "cmp rax, r8",
                    f"{cmov} rax, r8",
                    "inc rcx",
                    f"jmp {loop}",
                )
                self.label(end)
                return
            raise NotImplementedError(f"{e.func}() with {len(e.args)} args")
        if e.func == "repr":
            # repr(x) by static kind: float -> decimal text, int -> decimal,
            # str -> quote-wrapped copy. (Float text goes through the same
            # %g-based formatter as str(float) — fewer digits than CPython's
            # shortest-round-trip repr, an accepted precision caveat.)
            arg_t = A.expr_type(e.args[0])
            self.gen_expr(e.args[0], info)
            if arg_t == "float":
                self._emit_float_to_str()
            elif arg_t == "str":
                q, _ = self.intern_string("'")
                self.emitf(
                    "mov rbx, rax",
                    f"lea rax, [{q}]",
                    "call _runtime_str_concat",
                    f"lea rbx, [{q}]",
                    "call _runtime_str_concat",
                )
            elif arg_t == "int" and A.is_bool_expr(e.args[0]):
                self._emit_bool_to_str()
            elif arg_t == "int" and A.is_none_expr(e.args[0]):
                self.emitf("lea rax, [_runtime_none_str]")
            elif arg_t.startswith("instance:"):
                resolved = self._resolve_repr_dunder(arg_t.split(":", 1)[1])
                if resolved is not None:
                    owner, method = resolved
                    self.emitf(f"mov {self._arg_reg(0)}, rax")
                    self.emit_call(self._method_symbol(owner, method))
                else:
                    self._emit_int_to_str()
            else:
                self._emit_int_to_str()
            return
        if e.func == "sorted":
            # sorted(x) -> a NEW sorted list. set/dict sources sort their keys
            # (str); list/tuple sources are full-copied first. Element compare
            # picks str vs int from the source's element kind (sets/dicts are
            # str-keyed; lists use their tracked kind, default int) — unless
            # `key=` is given, in which case a parallel "keys" list (one
            # key(elem) per element) drives the comparison instead.
            arg = e.args[0]
            arg_t = A.expr_type(arg)
            self.gen_expr(arg, info)
            if arg_t in ("set", "dict"):
                self.emitf("call _runtime_dict_keys")
                el_kind = "str"
            else:
                SENTINEL_MIN = "0x8000000000000000"
                SENTINEL_MAX = "0x7fffffffffffffff"
                self.emitf(
                    f"mov rbx, {SENTINEL_MIN}",
                    f"mov rcx, {SENTINEL_MAX}",
                    "call _runtime_list_slice",
                )
                if isinstance(arg, A.Name):
                    el_kind = arg.list_el_type
                elif isinstance(arg, A.ListLit):
                    el_kind = arg.el_type
                else:
                    el_kind = "int"
            sort_key = getattr(e, "sort_key", None)
            if sort_key is not None:
                elems = info.locals_[f"__sortkey_elems_{id(e)}"]
                self.emitf(f"mov [rbp{elems:+d}], rax")
                self._emit_sort_keys_list(e, info)
                sort_fn = (
                    "_runtime_sort_pairs_str"
                    if e.sort_key_ret == "str"
                    else "_runtime_sort_pairs_int"
                )
                keys = info.locals_[f"__sortkey_keys_{id(e)}"]
                self.emitf(
                    f"mov rax, [rbp{elems:+d}]",
                    f"mov rbx, [rbp{keys:+d}]",
                    f"call {sort_fn}",
                )
            elif el_kind == "str":
                self.emitf("call _runtime_sort_str")
            else:
                self.emitf("call _runtime_sort_int")
            if getattr(e, "sort_reverse", None) is not None:
                self._emit_conditional_list_reverse(e, info)
            return
        if e.func == "reversed":
            # reversed(x) -> a new reversed copy of the list/tuple.
            self.gen_expr(e.args[0], info)
            SENTINEL_MIN = "0x8000000000000000"
            SENTINEL_MAX = "0x7fffffffffffffff"
            self.emitf(
                f"mov rbx, {SENTINEL_MIN}",
                f"mov rcx, {SENTINEL_MAX}",
                "call _runtime_list_slice",
                "call _runtime_list_reverse",
            )
            return
        if e.func == "bool":
            # bool(x) -> 0/1 truthiness, by static kind: containers test their
            # length (list/tuple and dict share a len field at +8), strings
            # test the first byte, scalars/pointers test the raw value.
            arg_t = A.expr_type(e.args[0])
            if arg_t.startswith("instance:"):
                cls_name = arg_t.split(":", 1)[1]
                owner = self._resolve_method_owner(cls_name, "__bool__")
                if owner is None:
                    owner = self._resolve_method_owner(cls_name, "__len__")
                    method = "__len__"
                else:
                    method = "__bool__"
                if owner is not None:
                    self.gen_expr(e.args[0], info)
                    self.emitf(f"mov {self._arg_reg(0)}, rax")
                    self.emit_call(self._method_symbol(owner, method))
                    self.emitf("test rax, rax", "setne al", "movzx rax, al")
                    return
                # No __bool__/__len__: non-null pointer is truthy.
                self.gen_expr(e.args[0], info)
                self.emitf("test rax, rax", "setne al", "movzx rax, al")
                return
            self.gen_expr(e.args[0], info)
            if arg_t in ("list", "tuple", "dict", "set"):
                # An Optional container can be a NULL pointer; None is falsy
                # too, so skip the length-read when rax is already 0.
                skip_lbl = self.fresh("bool_container_null")
                self.emitf(
                    "test rax, rax", f"jz {skip_lbl}",
                    "mov rax, [rax+8]",  # LIST_LEN_OFF == DICT_LEN_OFF
                )
                self.label(skip_lbl)
            elif arg_t == "str":
                # A `str | None` value can be a NULL pointer; None is falsy
                # too, so skip the byte-read when rax is already 0.
                skip_lbl = self.fresh("bool_str_null")
                self.emitf(
                    "test rax, rax", f"jz {skip_lbl}",
                    "movzx rax, byte [rax]",
                )
                self.label(skip_lbl)
            elif arg_t == "float":
                self.emitf(
                    "xorpd xmm1, xmm1",
                    "ucomisd xmm0, xmm1",
                    "setne al",
                    "movzx rax, al",
                )
                return
            self.emitf("test rax, rax", "setne al", "movzx rax, al")
            return
        if e.func == "type":
            # type(x) -> a "<class '...'>" string, matching CPython's repr.
            arg_t = A.expr_type(e.args[0])
            if arg_t == "int" and A.is_bool_expr(e.args[0]):
                label, _ = self.intern_string("<class 'bool'>")
                self.emitf(f"lea rax, [{label}]")
                return
            if arg_t == "int" and A.is_none_expr(e.args[0]):
                label, _ = self.intern_string("<class 'NoneType'>")
                self.emitf(f"lea rax, [{label}]")
                return
            if arg_t in ("int", "float", "str", "list", "dict", "tuple", "set"):
                label, _ = self.intern_string(f"<class '{arg_t}'>")
                self.emitf(f"lea rax, [{label}]")
                return
            if arg_t.startswith("instance:"):
                # Read the instance's RTTI class id (the "__class__" tag the
                # constructor stored) and use it to index a per-class table of
                # "<class '__main__.Name'>" strings.
                self.gen_expr(e.args[0], info)
                key_label, _ = self.intern_string("__class__")
                self.emitf(
                    f"lea rbx, [{key_label}]",
                    "xor rcx, rcx",
                    "call _runtime_dict_get_default",
                )
                table_label = self._type_name_table_label()
                self.emitf(
                    f"lea rbx, [{table_label}]",
                    "mov rax, [rbx+rax*8]",
                )
                return
            # Opaque ("any"): fall back to the instance's raw RTTI class id,
            # or 0 for an untagged value. type(x).__name__-style introspection
            # for "any" values needs true runtime type tags (post-1.0).
            self.gen_expr(e.args[0], info)
            key_label, _ = self.intern_string("__class__")
            self.emitf(
                f"lea rbx, [{key_label}]",
                "xor rcx, rcx",
                "call _runtime_dict_get_default",
            )
            return
        if e.func == "chr":
            # chr(n) -> fresh 1-char string.
            self.gen_expr(e.args[0], info)
            self.emitf("call _runtime_chr")
            return
        if e.func == "ord":
            # ord(ch) -> the first byte of the (1-char) string.
            self.gen_expr(e.args[0], info)
            self.emitf("movzx rax, byte [rax]")
            return
        if e.func == "abs":
            arg_t = A.expr_type(e.args[0])
            if arg_t == "float":
                self._gen_expr_as_float(e.args[0], info, arg_t)
                # Clear sign bit: movq, mask, movq back.
                self.emitf(
                    "movq rax, xmm0",
                    "mov rbx, 0x7FFFFFFFFFFFFFFF",
                    "and rax, rbx",
                    "movq xmm0, rax",
                )
            elif arg_t.startswith("instance:"):
                cls_name = arg_t.split(":", 1)[1]
                owner = self._resolve_method_owner(cls_name, "__abs__")
                self.gen_expr(e.args[0], info)
                if owner is not None:
                    self.emitf(f"mov {self._arg_reg(0)}, rax")
                    self.emit_call(self._method_symbol(owner, "__abs__"))
                # else: return the pointer as-is (no-op, rax already set)
            else:
                self.gen_expr(e.args[0], info)
                # Branchless abs: t = rax >> 63; rax = (rax ^ t) - t
                self.emitf("mov rbx, rax", "sar rbx, 63", "xor rax, rbx", "sub rax, rbx")
            return
        if e.func == "hash":
            arg_t = A.expr_type(e.args[0])
            self.gen_expr(e.args[0], info)
            if arg_t.startswith("instance:"):
                cls_name = arg_t.split(":", 1)[1]
                owner = self._resolve_method_owner(cls_name, "__hash__")
                if owner is not None:
                    self.emitf(f"mov {self._arg_reg(0)}, rax")
                    self.emit_call(self._method_symbol(owner, "__hash__"))
                # else: identity hash — pointer value already in rax
            elif arg_t == "str":
                # String hash: FNV-1a hasher shared with the dict runtime.
                self.emitf("call _runtime_hash_string")
            # int/float/bool: value is already in rax (identity hash)
            return
        if e.func == "round":
            arg_t = A.expr_type(e.args[0])
            if len(e.args) >= 2:
                # round(x, ndigits) -> float: scale by 10^ndigits, roundsd, unscale.
                # We use the pow helper for 10^ndigits (float via cvtsi2sd + pow).
                nd_slot = info.locals_[f"__round_nd_{id(e)}"]
                self._gen_expr_as_float(e.args[0], info, arg_t)
                self.emitf("movq rax, xmm0", f"mov [rbp{nd_slot:+d}], rax")
                self.gen_expr(e.args[1], info)  # rax = ndigits (int)
                # Compute 10.0^ndigits via pow
                self.emitf("cvtsi2sd xmm1, rax")  # xmm1 = (double)ndigits
                # xmm0 = 10.0
                scale_lbl, _ = self.intern_string("10.0")
                self.emitf(
                    "mov rax, 0x4024000000000000",  # 10.0 bit pattern
                    "movq xmm0, rax",
                )
                self._emit_call_libc_double_double("pow")  # xmm0 = 10^n
                self.emitf("movq rax, xmm0", f"movq xmm1, rax",
                           f"movq xmm0, [rbp{nd_slot:+d}]")
                self.emitf("mulsd xmm0, xmm1")     # xmm0 = x * 10^n
                self.emitf("roundsd xmm0, xmm0, 0")  # round
                self.emitf("divsd xmm0, xmm1")     # xmm0 = rounded / 10^n
            elif arg_t == "float":
                self._gen_expr_as_float(e.args[0], info, arg_t)
                # round to nearest even (banker's rounding, mode 0), then to int.
                self.emitf("roundsd xmm0, xmm0, 0", "cvtsd2si rax, xmm0")
            else:
                # round(int) is the identity.
                self.gen_expr(e.args[0], info)
            return
        if e.func == "format" and len(e.args) >= 1:
            # format(value[, spec]) -> str. Handles "b", "x", "o", "d" specs
            # for integer values; no spec or "s" -> str(value).
            spec = ""
            if len(e.args) >= 2 and isinstance(e.args[1], A.StrLit):
                spec = e.args[1].value
            self.gen_expr(e.args[0], info)
            if spec in ("b", "x", "o"):
                empty_label, _ = self.intern_string("")
                base = {"b": 2, "x": 16, "o": 8}[spec]
                self.emitf(
                    f"mov rbx, {base}",
                    f"lea rcx, [rel {empty_label}]",
                    "call _runtime_int_to_base",
                )
            elif spec == "d" or spec == "":
                self._emit_int_to_str()
            elif spec == "s":
                pass  # already a str or int-to-str fallthrough
            else:
                self._emit_int_to_str()
            return
        if e.func in ("hex", "oct", "bin"):
            # hex(n)/oct(n)/bin(n) -> "0x.."/"0o.."/"0b.." (with a leading '-'
            # for negative n), via the shared _runtime_int_to_base helper.
            self.gen_expr(e.args[0], info)
            prefix = {"hex": "0x", "oct": "0o", "bin": "0b"}[e.func]
            base = {"hex": 16, "oct": 8, "bin": 2}[e.func]
            prefix_label, _ = self.intern_string(prefix)
            self.emitf(f"mov rbx, {base}", f"lea rcx, [rel {prefix_label}]", "call _runtime_int_to_base")
            return
        if e.func == "divmod":
            # divmod(a, b) -> (a // b, a % b) as a 2-tuple, via the shared
            # _runtime_divmod helper (floor semantics, same as // and %).
            self.gen_expr(e.args[0], info)
            self.emitf("push rax")  # a
            self.gen_expr(e.args[1], info)
            self.emitf("mov rbx, rax", "pop rax", "call _runtime_divmod")
            return
        if e.func == "pow":
            # pow(base, exp) — integer exponentiation loop.
            # Evaluate base first, save to stack, then exp.
            self.gen_expr(e.args[0], info)
            self.emitf("push rax")  # base
            self.gen_expr(e.args[1], info)
            self.emitf("mov rcx, rax")  # exp
            self.emitf("pop rbx")        # base
            loop = self.fresh("pow_loop")
            end = self.fresh("pow_end")
            self.emitf("mov rax, 1")
            self.label(loop)
            self.emitf("test rcx, rcx", f"jle {end}", "imul rax, rbx", "dec rcx", f"jmp {loop}")
            self.label(end)
            return
        # Constructor: ClassName(args). Allocate an empty dict, then if the
        # class chain provides an __init__, dispatch to it with the instance
        # as the first argument.
        if e.func in self.mod.classes_sig:
            self._gen_constructor(e, info)
            return
        if e.func not in self.funcs:
            # `obj(args)` where obj is a user instance with __call__: load self
            # into arg0, place user args into arg1+, call the method.
            if getattr(e, "dunder_call_owner", None) is not None:
                owner = e.dunder_call_owner  # type: ignore[attr-defined]
                cleanup = self._emit_positional_args(e, e.args, info, start_reg=1)
                self.emitf(f"mov {self._arg_reg(0)}, {self._var_mem(e.func, info)}")
                self.emit_call(self._method_symbol(owner, "__call__"))
                if cleanup:
                    self.emitf(f"add rsp, {cleanup}")
                return
            # Calling through a variable that holds a function pointer (a lambda
            # bound to a name, a function passed as a parameter, or a global).
            # Place the args, then load the pointer into rax (not an arg
            # register on either ABI) and call indirectly.
            if e.func in info.locals_ or e.func in self.global_vars:
                # Detect closure call: variable type is "closure".
                if self._var_type(e.func, info) == "closure":
                    # Find the inner function to know the number of free vars.
                    # e.func is the variable name (e.g. "add5"), not the inner func
                    # name (e.g. "adder"). Look through module stmts for any Assign
                    # `e.func = factory_call(...)`, then find the factory's ClosureBind
                    # to get the inner function's free_vars count.
                    n_free = 0
                    # Direct match: e.func is a lifted function name with free_vars
                    for ff in self.mod.funcs:
                        if ff.name == e.func and getattr(ff, "is_lifted", False):
                            # Direct field access: ff.free_vars is always
                            # present (real FuncDef field); getattr(ff,
                            # "free_vars", []) made len() compile as
                            # strlen() on an opaque "any"-typed value (same
                            # bug class fixed elsewhere in this file).
                            n_free = len(ff.free_vars)
                            break
                    if n_free == 0:
                        # Indirect: e.func was assigned from a factory call.
                        # Find the factory name from module body assignments.
                        factory_name = None
                        all_stmts = list(self.mod.body) + [
                            s for f in self.mod.funcs for s in f.body
                        ]
                        for s in all_stmts:
                            if (
                                isinstance(s, A.Assign)
                                and s.target == e.func
                                and isinstance(s.value, A.Call)
                            ):
                                factory_name = s.value.func
                                break
                        if factory_name:
                            # Find the factory function's ClosureBind to get n_free
                            for ff in self.mod.funcs:
                                if ff.name == factory_name:
                                    for fs in ff.body:
                                        if isinstance(fs, A.ClosureBind):
                                            n_free = len(fs.free_vars)
                                            break
                                    break
                    # closure = [MAGIC, fn_ptr, fv0, fv1, ...]
                    # Build synthetic args: [fv0, fv1, ..., explicit_args...]
                    # We emit args in reverse (stack grows down) by reversing.
                    # Use the list-based scratch: load closure buf pointer.
                    # Strategy: load captured vars + explicit args using arg regs.
                    # Since there can be at most a few args total, use direct load.
                    closure_mem = self._var_mem(e.func, info)
                    # Evaluate explicit args and push them temporarily.
                    arg_temps: list = []
                    slot_base = info.locals_.get(f"__closure_arg_{id(e)}_0")
                    for i, arg_expr in enumerate(e.args):
                        slot = info.locals_.get(f"__closure_arg_{id(e)}_{i}")
                        if slot is not None:
                            self.gen_expr(arg_expr, info)
                            self.emitf(f"mov [rbp{slot:+d}], rax")
                        else:
                            # Fallback: push on stack
                            self.gen_expr(arg_expr, info)
                            self.emitf("push rax")
                        arg_temps.append(slot)
                    # Load closure buf.
                    self.emitf(
                        f"mov rax, {closure_mem}",
                        f"mov rcx, [rax+{self.LIST_BUF_OFF}]",
                        # fn_ptr at buf+8
                        "mov r10, [rcx+8]",
                    )
                    # Place captured vars into arg regs 0..n_free-1.
                    for i in range(n_free):
                        reg = self._arg_reg(i)
                        self.emitf(f"mov {reg}, [rcx+{(i + 2) * 8}]")
                    # Place explicit args into arg regs n_free..n_free+len(args)-1.
                    for i, (arg_expr, slot) in enumerate(zip(e.args, arg_temps)):
                        reg = self._arg_reg(n_free + i)
                        if slot is not None:
                            self.emitf(f"mov {reg}, [rbp{slot:+d}]")
                        else:
                            # Was pushed; pop in reverse order (but we pushed in order)
                            pass
                    self.emitf("call r10")
                    return
                cleanup = self._emit_positional_args(e, e.args, info, start_reg=0)
                self.emitf(f"mov rax, {self._var_mem(e.func, info)}", "call rax")
                if cleanup:
                    self.emitf(f"add rsp, {cleanup}")
                return
            # Unknown external function (argparse.ArgumentParser, pathlib.Path,
            # etc.): evaluate args for side effects, return 0. This keeps
            # codegen alive for the self-hosting build; actual implementations
            # replace these stubs in stdlib.
            for a in e.args:
                self.gen_expr(a, info)
            self.emitf("xor rax, rax")
            return
        # Direct-by-name call to a lifted (nested-function) closure body. Sema
        # prepends the captured free vars as leading params on the lifted
        # FuncDef itself (see sema.py's free-var-param-prepending pass). The
        # bare name is only in scope, unqualified, in two places: (a) inside
        # the lifted function's own body (true self-recursion — its own
        # params ARE the free vars), or (b) inside the enclosing function that
        # originally contained the nested `def` (the factory), before the
        # `ClosureBind` rewrote it away — e.g. `_collect_import_stmts` calling
        # its own nested `walk(...)` directly. In case (b) the free vars are
        # whatever locals in *this* scope share the free var's name (that's
        # what "free variable" means — same name resolves in both scopes).
        # Either way, look the free var names up in the current `info`'s own
        # scope rather than assuming they sit in `info.params`.
        # (External callers outside both of these go through the
        # ClosureBind-bound variable instead — the
        # `_var_type(e.func, info) == "closure"` branch above.)
        free_var_names: list = []
        for ff in self.mod.funcs:
            if ff.name == e.func and getattr(ff, "is_lifted", False):
                free_var_names = list(getattr(ff, "free_vars", []) or [])
                break
        if free_var_names:
            # Free vars + explicit args can together exceed the ABI's
            # register count (e.g. a helper with several captured vars and
            # several params), so they must go through the same
            # register/stack-spill assignment as an ordinary call rather
            # than assuming every free var fits in a register — route both
            # through the reserved `__fvarg_`/`__callarg_` slots and a single
            # _load_call_operands call covering the combined operand list.
            fv_offs: list[int] = []
            fv_types: list = []
            for i, fv_name in enumerate(free_var_names):
                # The free var's type is whatever it is in *this* (the
                # calling) scope — same variable, same value, same type in
                # both scopes by definition. More reliable than reading the
                # callee's own FuncInfo.local_types, which is only populated
                # once that function's own codegen pass runs (and a lifted
                # sibling may be emitted *after* this call site).
                fv_ty = self._var_type(fv_name, info)
                fv_types.append(fv_ty)
                if fv_ty == "float":
                    self.emitf(f"movsd xmm0, {self._var_mem(fv_name, info)}")
                    off = info.locals_[f"__fvarg_{id(e)}_{i}"]
                    self.emitf(f"movsd [rbp{off:+d}], xmm0")
                else:
                    self.emitf(f"mov rax, {self._var_mem(fv_name, info)}")
                    off = info.locals_[f"__fvarg_{id(e)}_{i}"]
                    self.emitf(f"mov [rbp{off:+d}], rax")
                fv_offs.append(off)
            arg_offs = self._eval_call_operands(e, e.args, info)
            offs = fv_offs + arg_offs
            arg_types = fv_types + [A.expr_type(a) for a in e.args]
            cleanup = self._load_call_operands(
                e, offs, info, start_reg=0, arg_types=arg_types
            )
            self.emit_call(self._user_symbol(e.func))
            if cleanup:
                self.emitf(f"add rsp, {cleanup}")
            return
        # Sema has normalized e.args to a complete positional list (defaults
        # filled, keyword args placed, varargs packed), so no _fill_defaults.
        cleanup = self._emit_positional_args(e, e.args, info, start_reg=0)
        self.emit_call(self._user_symbol(e.func))
        if cleanup:
            self.emitf(f"add rsp, {cleanup}")

    def _emit_sort_keys_list(self, e, info: FuncInfo) -> None:
        """Build the "keys" list — `key(elem)` for each elem of the list
        header parked in `__sortkey_elems_{id(e)}` — into
        `__sortkey_keys_{id(e)}`. Used by `sorted(xs, key=...)` and
        `xs.sort(key=...)` just before `_runtime_sort_pairs_{str,int}`.
        `e.sort_key` is a function-pointer-valued expr (a lambda literal or a
        name bound to one); each element is passed to it via the normal
        indirect-call convention."""
        elems = info.locals_[f"__sortkey_elems_{id(e)}"]
        fn = info.locals_[f"__sortkey_fn_{id(e)}"]
        keys = info.locals_[f"__sortkey_keys_{id(e)}"]
        n = info.locals_[f"__sortkey_n_{id(e)}"]
        i = info.locals_[f"__sortkey_i_{id(e)}"]

        self.gen_expr(e.sort_key, info)
        self.emitf(f"mov [rbp{fn:+d}], rax")

        self.emitf(
            f"mov rax, [rbp{elems:+d}]",
            f"mov rax, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{n:+d}], rax",
        )

        # keys = empty list (cap 4), grown via _runtime_list_append.
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{keys:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(f"mov rbx, [rbp{keys:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax")

        self.emitf(f"mov qword [rbp{i:+d}], 0")
        top = self.fresh("sortkey")
        end = self.fresh("sortkey_end")
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{i:+d}]",
            f"cmp rax, [rbp{n:+d}]",
            f"jge {end}",
            f"mov rcx, [rbp{elems:+d}]",
            f"mov rcx, [rcx+{self.LIST_BUF_OFF}]",
            f"mov {self._arg_reg(0)}, [rcx+rax*8]",
            f"mov rax, [rbp{fn:+d}]",
            "call rax",
            "mov rbx, rax",  # key(elem)
            f"mov rax, [rbp{keys:+d}]",
            "call _runtime_list_append",
            f"inc qword [rbp{i:+d}]",
            f"jmp {top}",
        )
        self.label(end)

    def _emit_conditional_list_reverse(self, e, info: FuncInfo) -> None:
        """If `e.sort_reverse` is truthy at runtime, reverse (in place) the
        list whose header is in rax via `_runtime_list_reverse`. Leaves the
        (possibly-reversed) header in rax either way. Used by
        `sorted(xs, reverse=...)` / `xs.sort(reverse=...)`."""
        hdr = info.locals_[f"__sortrev_hdr_{id(e)}"]
        self.emitf(f"mov [rbp{hdr:+d}], rax")
        skip = self.fresh("sortrev_skip")
        self._gen_truthy_test(e.sort_reverse, info, skip)
        self.emitf(
            f"mov rax, [rbp{hdr:+d}]",
            "call _runtime_list_reverse",
            f"mov [rbp{hdr:+d}], rax",
        )
        self.label(skip)
        self.emitf(f"mov rax, [rbp{hdr:+d}]")

    def _emit_minmax_scan(self, e, info: FuncInfo, el_kind: str) -> None:
        """min/max over the 1-arg iterable form, when either a `key=`
        callable is given or the elements are strings (raw pointer
        comparison would be wrong for str). Scans the buffer tracking the
        best (elem, key) pair: with no `key=`, key(x) is the identity, so
        `key_ret` falls back to `el_kind`. str keys compare via
        `_runtime_str_cmp`; int keys via a signed `cmp`. Leaves the best
        element in rax."""
        sort_key = getattr(e, "sort_key", None)
        key_ret = e.sort_key_ret if sort_key is not None else el_kind

        fn = info.locals_[f"__mmkey_fn_{id(e)}"]
        n = info.locals_[f"__mmkey_n_{id(e)}"]
        i = info.locals_[f"__mmkey_i_{id(e)}"]
        buf = info.locals_[f"__mmkey_buf_{id(e)}"]
        best_elem = info.locals_[f"__mmkey_best_elem_{id(e)}"]
        best_key = info.locals_[f"__mmkey_best_key_{id(e)}"]
        cur_elem = info.locals_[f"__mmkey_cur_elem_{id(e)}"]
        cur_key = info.locals_[f"__mmkey_cur_key_{id(e)}"]

        if sort_key is not None:
            self.gen_expr(sort_key, info)
            self.emitf(f"mov [rbp{fn:+d}], rax")

        self.gen_expr(e.args[0], info)  # list/tuple header
        self.emitf(
            f"mov rcx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{n:+d}], rcx",
            f"mov rax, [rax+{self.LIST_BUF_OFF}]",
            f"mov [rbp{buf:+d}], rax",
        )

        def compute_key(elem_slot: int, key_slot: int) -> None:
            if sort_key is not None:
                self.emitf(
                    f"mov {self._arg_reg(0)}, [rbp{elem_slot:+d}]",
                    f"mov rax, [rbp{fn:+d}]",
                    "call rax",
                    f"mov [rbp{key_slot:+d}], rax",
                )
            else:
                self.emitf(
                    f"mov rax, [rbp{elem_slot:+d}]",
                    f"mov [rbp{key_slot:+d}], rax",
                )

        # best_elem = buf[0]; best_key = key(best_elem)
        self.emitf(
            f"mov rax, [rbp{buf:+d}]",
            "mov rax, [rax]",
            f"mov [rbp{best_elem:+d}], rax",
        )
        compute_key(best_elem, best_key)
        self.emitf(f"mov qword [rbp{i:+d}], 1")

        top = self.fresh("mmkey")
        end = self.fresh("mmkey_end")
        upd = self.fresh("mmkey_update")
        nxt = self.fresh("mmkey_next")
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{i:+d}]",
            f"cmp rax, [rbp{n:+d}]",
            f"jge {end}",
            f"mov rcx, [rbp{buf:+d}]",
            "mov rax, [rcx+rax*8]",
            f"mov [rbp{cur_elem:+d}], rax",
        )
        compute_key(cur_elem, cur_key)
        if key_ret == "str":
            self.emitf(
                f"mov rax, [rbp{cur_key:+d}]",
                f"mov rbx, [rbp{best_key:+d}]",
                "call _runtime_str_cmp",
                "cmp rax, 0",
            )
        else:
            self.emitf(
                f"mov rax, [rbp{cur_key:+d}]",
                f"cmp rax, [rbp{best_key:+d}]",
            )
        if e.func == "max":
            self.emitf(f"jg {upd}", f"jmp {nxt}")
        else:
            self.emitf(f"jl {upd}", f"jmp {nxt}")
        self.label(upd)
        self.emitf(
            f"mov rax, [rbp{cur_elem:+d}]",
            f"mov [rbp{best_elem:+d}], rax",
            f"mov rax, [rbp{cur_key:+d}]",
            f"mov [rbp{best_key:+d}], rax",
        )
        self.label(nxt)
        self.emitf(f"inc qword [rbp{i:+d}]", f"jmp {top}")
        self.label(end)
        self.emitf(f"mov rax, [rbp{best_elem:+d}]")

    def _fill_defaults(self, args: list, defaults: list) -> list:
        """Pad `args` with the trailing defaults whose positions weren't
        supplied by the caller. Returns the combined list.

        Sema has already validated that len(args) is in the allowed range,
        so the only thing left to do is splice in the literals."""
        if not defaults or len(args) == len(defaults):
            return list(args)
        n_missing = len(defaults) - len(args)
        if n_missing <= 0:
            return list(args)
        tail = [d for d in defaults[-n_missing:] if d is not None]
        # Concatenate via extend rather than `+` (asmpython lists have no `+`).
        combined = list(args)
        combined.extend(tail)
        return combined

    def _cg_is_exception_class(self, name: str) -> bool:
        """True if `name` is a builtin exception, or a user class deriving
        (transitively) from one. Mirrors `sema._is_exception_class`."""
        cur = name
        seen: set[str] = set()
        while cur is not None and cur not in seen:
            if cur in BUILTIN_EXC_IDS:
                return True
            seen.add(cur)
            sig = self.mod.classes_sig.get(cur)
            if sig is None:
                return False
            cur = sig.parent
        return False

    def _exc_type_id(self, name: str) -> int:
        """Integer RTTI id for the exception type `name`, assigning a fresh
        id (and recording its parent for `_exc_is_a`) the first time a user
        exception class is seen."""
        if name not in self._exc_ids:
            tid = self._exc_next_id
            self._exc_next_id += 1
            self._exc_ids[name] = tid
            sig = self.mod.classes_sig.get(name)
            parent = sig.parent if sig is not None else None
            parent_id = self._exc_type_id(parent) if parent else -1
            self._exc_id_parent[str(tid)] = parent_id
        return self._exc_ids[name]

    def _exc_is_a(self, child_id: int, ancestor_id: int) -> bool:
        """True if `child_id` is `ancestor_id` or a (transitive) subtype of
        it, per the parent links recorded in `self._exc_id_parent`."""
        cur: int = child_id
        seen: list[int] = []
        while cur >= 0 and cur not in seen:
            if cur == ancestor_id:
                return True
            seen.append(cur)
            cur = self._exc_id_parent.get(str(cur), -1)
        return False

    def _exc_matching_ids(self, types: list[str]) -> list[int]:
        """The full set of runtime type ids an `except (T1, T2, ...):` clause
        should catch: each Ti, every (currently known) subtype of any Ti, and
        EXC_ANY (untyped raises always match a typed `except`, for back-compat
        with code that `raise`s bare strings)."""
        ancestors: list[int] = []
        for t in types:
            ancestors.append(self._exc_type_id(t))
        matches: list[int] = [EXC_ANY]
        for a in ancestors:
            if a not in matches:
                matches.append(a)
        for tid_str in self._exc_id_parent:
            tid = int(tid_str)
            for a in ancestors:
                if self._exc_is_a(tid, a) and tid not in matches:
                    matches.append(tid)
                    break
        return matches

    def _exc_raise_type_id(self, value: "A.Expr") -> int:
        """The RTTI id to attach to `raise value`: the exception class's id
        for `raise ExcType` / `raise ExcType(...)`, or EXC_ANY for anything
        else (string messages, variables, etc.)."""
        name = None
        if isinstance(value, A.Call):
            name = value.func
        elif isinstance(value, A.Name):
            name = value.name
        if name is not None and (
            name in BUILTIN_EXC_IDS or self._cg_is_exception_class(name)
        ):
            return self._exc_type_id(name)
        return EXC_ANY

    def _subclass_ids(self, target: str) -> list[int]:
        """Class ids of `target` and every class that (transitively) descends
        from it — the set isinstance(x, target) should accept."""
        ids: list[int] = []
        for name, cid in self.class_ids.items():
            cur = name
            seen: set[str] = set()
            while cur is not None and cur not in seen:
                if cur == target:
                    ids.append(cid)
                    break
                seen.add(cur)
                sig = self.mod.classes_sig.get(cur)
                cur = sig.parent if sig is not None else None
        return ids

    def _gen_isinstance(self, e: A.Call, info: FuncInfo) -> None:
        """isinstance(x, Cls) -> 1 if x's runtime class id is Cls or a subclass,
        else 0. Reads the hidden "__class__" tag the constructor stored.

        The second argument is a class name (or, for a tuple of classes, each
        name); an external/unknown class matches nothing here (it has no id).
        Result (0/1) left in rax.
        """
        # Collect the target class names: a bare Name, a module-qualified
        # attr (`isinstance(s, A.Pass)` — whole-program merging flattens the
        # module, so the attr IS the class), or a tuple of either.
        targets: list[str] = []
        cls_arg = e.args[1]
        if isinstance(cls_arg, A.Name):
            targets.append(cls_arg.name)
        elif isinstance(cls_arg, A.Attr):
            targets.append(cls_arg.name)
        elif isinstance(cls_arg, A.TupleLit):
            for el in cls_arg.elems:
                if isinstance(el, A.Name):
                    targets.append(el.name)
                elif isinstance(el, A.Attr):
                    targets.append(el.name)

        # For primitive types (int, str, float, bool, list, dict, tuple, set),
        # resolve statically from the sema-computed type of arg[0].
        PRIM_MAP = {
            "int": ("int",),
            "str": ("str",),
            "float": ("float",),
            "bool": ("int",),   # bool is a subtype of int
            "list": ("list",),
            "dict": ("dict",),
            "tuple": ("tuple",),
            "set": ("set",),
        }
        arg0_type = A.expr_type(e.args[0])
        has_prim_target = False
        prim_match = False
        for t in targets:
            if t in PRIM_MAP:
                has_prim_target = True
                if arg0_type in PRIM_MAP[t]:
                    prim_match = True
        if has_prim_target:
            # Evaluate arg0 for side effects (it may be a call).
            self.gen_expr(e.args[0], info)
            if prim_match:
                self.emitf("mov rax, 1")
            else:
                self.emitf("xor rax, rax")
            return

        # Build the accepted-id set across all targets.
        accept: list[int] = []
        for t in targets:
            for cid in self._subclass_ids(t):
                if cid not in accept:
                    accept.append(cid)

        # Evaluate x (the instance) and read its "__class__" id into rax.
        self.gen_expr(e.args[0], info)  # rax = instance dict header
        key_label, _ = self.intern_string("__class__")
        # dict_get_default(header=rax, key=rbx, default=rcx) -> rax = id (or -1).
        self.emitf(
            f"lea rbx, [{key_label}]",
            "mov rcx, -1",  # default: no class tag -> matches nothing
            "call _runtime_dict_get_default",
        )
        # rax now holds the runtime class id. Compare against each accepted id;
        # set rax = 1 on a match, else 0.
        match = self.fresh("isinst_yes")
        done = self.fresh("isinst_done")
        self.emitf("mov rbx, rax")  # rbx = runtime id
        for cid in accept:
            self.emitf(f"cmp rbx, {cid}", f"je {match}")
        self.emitf("xor rax, rax", f"jmp {done}")
        self.label(match)
        self.emitf("mov rax, 1")
        self.label(done)

    def _gen_import_binary(self, stmt: A.Assign, info: FuncInfo, mem: str) -> None:
        """`handle = import_binary(path)`.

        Loads the binary via LoadLibraryA (Windows) / dlopen (Linux), then
        immediately resolves every function the program declares with
        `@<handle>.imported` for this exact handle variable (collected from
        the whole program into `self.imported_funcs` at Codegen construction)
        via GetProcAddress/dlsym, storing each resolved pointer in a dict
        keyed by function name — the same instance representation every
        user class uses, so `handle.some_func(...)` reads it back through
        the ordinary attribute-dict-get path. `_handle` itself is stored too
        (unused today, but keeps the handle alive / available for a future
        close_binary()).
        """
        e = stmt.value  # the import_binary(path) Call
        handle_slot = info.locals_[f"__importbin_handle_{id(stmt)}"]
        cap = 8
        self._emit_malloc(self.DICT_HEADER)
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov {mem}, rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(f"mov rbx, {mem}", f"mov [rbx+{self.DICT_BUF_OFF}], rax")
        dict_slot_off = None
        # _emit_dict_alloc_order_buf wants a frame slot, not a memory operand;
        # reuse the handle scratch slot (not live yet at this point) to avoid
        # reserving a second one just for this call.
        self.emitf(f"mov rax, {mem}", f"mov [rbp{handle_slot:+d}], rax")
        self._emit_dict_alloc_order_buf(cap, handle_slot)

        # Load the library; keep the handle in its scratch slot across every
        # GetProcAddress/dlsym call (each clobbers rax/rbx).
        self.gen_expr(e.args[0], info)  # rax = path string
        self._emit_load_library()  # rax = handle
        self.emitf(f"mov [rbp{handle_slot:+d}], rax")

        handle_key, _ = self.intern_string("_handle")
        self.emitf(
            f"mov rcx, [rbp{handle_slot:+d}]",
            f"lea rbx, [{handle_key}]",
            f"mov rax, {mem}",
            "call _runtime_dict_set",
        )
        for func_name, _funcdef in self.imported_funcs.get(stmt.target, []):
            name_label, _ = self.intern_string(func_name)
            self.emitf(
                f"mov rax, [rbp{handle_slot:+d}]",
                f"lea rbx, [{name_label}]",
            )
            self._emit_get_proc_addr()  # rax = function ptr
            self.emitf(
                "mov rcx, rax",
                f"lea rbx, [{name_label}]",
                f"mov rax, {mem}",
                "call _runtime_dict_set",
            )

    def _gen_gl_import(self, stmt: A.Assign, info: FuncInfo, mem: str) -> None:
        """`handle = gl_import()`.

        Allocates an empty function-pointer-table dict; nothing is resolved
        here. Resolution is LAZY, done by _gen_dynamic_call on each
        function's first real call (see the "is_gl" lazy-resolve branch
        there) -- deliberately not eager like _gen_import_binary(), for two
        reasons specific to GL:

        1. SDL_GL_GetProcAddress requires a current GL context; resolving
           every @<handle>.imported function the moment `gl_import()` runs
           would return NULL for all of them whenever that line executes
           before context creation. And it always does in one important
           case: asmpython's whole-program bundler prepends a class's
           required module-level globals (see program.py's
           _materialize_value_imports class_origin handling) to the very
           TOP of the compiled program -- before ANY of the user's own
           setup code (including their own context creation) has run.
           Lazy resolution sidesteps needing the bundler to special-case
           "but not THIS global, place it after context creation" (a
           dependency the bundler has no way to know about, since nothing
           about `glfns = gl_import()` syntactically says "must run after
           SDL_GL_CreateContext").
        2. It also matches how real GL loaders (GLAD, GLEW, SDL's own
           examples) behave -- resolve what you actually use, when you
           first use it -- so a program that only calls a handful of GL
           functions doesn't pay for resolving every @imported stub it
           happens to have declared.
        """
        cap = 8
        self._emit_malloc(self.DICT_HEADER)
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov {mem}, rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(f"mov rbx, {mem}", f"mov [rbx+{self.DICT_BUF_OFF}], rax")
        dict_slot_off = info.locals_[f"__glimport_dictslot_{id(stmt)}"]
        self.emitf(f"mov rax, {mem}", f"mov [rbp{dict_slot_off:+d}], rax")
        self._emit_dict_alloc_order_buf(cap, dict_slot_off)

    def _gen_constructor(self, e: A.Call, info: FuncInfo) -> None:
        """ClassName(args)  ->  rax = pointer to new instance.

        An instance is just a fresh str->int dict; the static type of the
        Call expression carries the class identity. After alloc, dispatch to
        __init__ (walking the parent chain) if one exists. The instance lives
        in a pre-reserved frame slot so we can survive intermediate calls.
        """
        slot_name = f"__ctor_inst_{id(e)}"
        slot_off = info.locals_[slot_name]
        # Inline the empty-dict allocation (mirrors _gen_dict_lit with n=0).
        cap = 8
        self._emit_malloc(self.DICT_HEADER)
        self.emitf(
            f"mov qword [rax+{self.DICT_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
            f"mov [rbp{slot_off:+d}], rax",
        )
        self.emitf(f"mov rbx, {cap * self.DICT_SLOT_SIZE}", "call _runtime_zalloc")
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]", f"mov [rbx+{self.DICT_BUF_OFF}], rax"
        )
        self._emit_dict_alloc_order_buf(cap, slot_off)

        # Tag the instance with its class id under the reserved "__class__" key
        # so isinstance(x, Cls) can identify the runtime class. Skipped if the
        # class isn't registered (shouldn't happen for a user constructor).
        cid = self.class_ids.get(e.func)
        if cid is not None:
            key_label, _ = self.intern_string("__class__")
            self.emitf(
                f"mov rcx, {cid}",  # value = class id
                f"lea rbx, [{key_label}]",  # key = "__class__"
                f"mov rax, [rbp{slot_off:+d}]",  # dict header
                "call _runtime_dict_set",
            )

        init_owner = self._resolve_method_owner(e.func, "__init__")
        if init_owner is not None:
            # Class-body constants (`STR_METHODS = {...}` on a class with an
            # explicit __init__) live on instances too — store each evaluable
            # class-var default before __init__ runs, so `self.STR_METHODS`
            # reads work. Unevaluable defaults are skipped (read back as 0).
            # Walk the chain grandparent-first so a child's own class var
            # overwrites (rather than is shadowed by) an inherited one with
            # the same name — matches normal attribute-lookup precedence.
            # Without this, a subclass that doesn't override a base class's
            # class var (e.g. WindowsCodegen inheriting Codegen.section_bss)
            # never gets that key seeded into the instance dict at all, so
            # later instance reads of it return the dict_get_default fallback
            # (0 / NULL) instead of the base class's value.
            for cname0 in reversed(self._resolve_class_chain(e.func)):
                # Same fix as the @dataclass-style branch below: read
                # class_vars directly inside the search loop instead of via
                # a `cls_def0 = None` sentinel reassigned and read after the
                # loop. A.Module is an external (opaque) type to sema, so
                # `cls_def0.class_vars` read after the loop stayed
                # "any"-typed regardless of cls_def0's real value.
                class_vars0: list = []
                for c0 in self.mod.classes:
                    if c0.name == cname0:
                        class_vars0 = c0.class_vars
                for cv0 in class_vars0:
                    fname0, _fa0, fdef0 = cv0
                    if fdef0 is None:
                        continue
                    if isinstance(fdef0, A.Call) and fdef0.func == "field":
                        continue
                    self.gen_expr(fdef0, info)
                    if A.expr_type(fdef0) == "float":
                        self.emitf("movq rax, xmm0")
                    key0, _ = self.intern_string(fname0)
                    self.emitf(
                        "mov rcx, rax",
                        f"lea rbx, [{key0}]",
                        f"mov rax, [rbp{slot_off:+d}]",
                        "call _runtime_dict_set",
                    )
            # __init__(self, args...). Sema normalized e.args to a complete
            # positional list; the instance (already in slot_off) is reg 0.
            cleanup = self._emit_positional_args(
                e, e.args, info, start_reg=1, receiver_slot=slot_off
            )
            self.emit_call(self._method_symbol(init_owner, "__init__"))
            if cleanup:
                self.emitf(f"add rsp, {cleanup}")
        else:
            # No explicit __init__: a @dataclass-style class. Synthesize the
            # init by storing each declared field, in declaration order, from
            # the call's positionals, then keywords, then the field's literal
            # default. `field(default_factory=list/dict)` defaults synthesize a
            # fresh empty container; an unevaluable default (lambda factories)
            # leaves the field unset, which reads back as 0.
            # Read class_vars directly inside the search loop, not via a
            # `cls_def = None` sentinel reassigned to the found A.ClassDef
            # and read afterward. sema can't carry the reassignment's real
            # type forward through the `None`-typed initial declaration (it
            # has no flow-sensitive narrowing for "this loop-local variable
            # got reassigned to something with a real type"), so
            # `cls_def.class_vars` read after the loop was "any"-typed
            # regardless of cls_def's actual runtime value -- which fed
            # `enumerate(class_vars)`/iteration below with an opaquely-typed
            # list. Confirmed via gdb on a selfhost rebuild: this was the
            # actual mechanism behind a "KeyError: key not in dict" error,
            # since every @dataclass-style constructor call (including the
            # parser's own AST node construction, e.g. A.For(...)) goes
            # through this exact code, and the corrupted iteration built
            # instances with garbage dict keys instead of real field names.
            class_vars: list = []
            for c in self.mod.classes:
                if c.name == e.func:
                    class_vars = c.class_vars
            kwmap: dict = {}
            # Direct field access: e is this function's own A.Call parameter,
            # and kwargs: list is always present on it. A getattr() result
            # is opaque to sema -- `for kn, kv in <opaque>:` unpacks each
            # element with unknown per-slot types, which (matching the
            # cv-unpack pattern below) defaults kn/kv's inferred types
            # wrong, in turn making `fname in kwmap` spuriously False for
            # every real keyword arg. This was the actual remaining cause
            # of the the "KeyError: key not in dict" error: every
            # A.For(var=..., range_args=..., ...)-style keyword-argument
            # constructor call (used throughout the parser for AST nodes)
            # silently dropped every field whose value came from a keyword
            # argument instead of a positional one or a literal default.
            for kn, kv in e.kwargs:
                kwmap[kn] = kv
            for fi, cv in enumerate(class_vars):
                fname, _fannot, fdefault = cv
                if fi < len(e.args):
                    val_expr = e.args[fi]
                elif fname in kwmap:
                    val_expr = kwmap[fname]
                else:
                    val_expr = fdefault
                if val_expr is None:
                    continue
                # default_factory containers -> fresh empty list/dict.
                if (
                    isinstance(val_expr, A.Call)
                    and val_expr.func == "field"
                ):
                    factory = None
                    for kn, kv in getattr(val_expr, "kwargs", []) or []:
                        if kn == "default_factory" and isinstance(kv, A.Name):
                            factory = kv.name
                    tmp_off = info.locals_[f"__ctor_tmp_{id(e)}"]
                    if factory == "list":
                        # empty list: header cap=4 len=0 + buffer. The header
                        # is parked in a frame slot across the buffer malloc —
                        # a push would sit inside the callee's shadow space.
                        self.emitf("mov rax, 24")
                        self._emit_libc_malloc_size_in_rax()
                        self.emitf(
                            f"mov qword [rax+{self.LIST_CAP_OFF}], 4",
                            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
                            f"mov [rbp{tmp_off:+d}], rax",
                            "mov rax, 32",
                        )
                        self._emit_libc_malloc_size_in_rax()
                        self.emitf(
                            "mov rbx, rax",
                            f"mov rax, [rbp{tmp_off:+d}]",
                            f"mov [rax+{self.LIST_BUF_OFF}], rbx",
                        )
                    elif factory == "dict":
                        self._emit_malloc(self.DICT_HEADER)
                        self.emitf(
                            f"mov qword [rax+{self.DICT_CAP_OFF}], 8",
                            f"mov qword [rax+{self.DICT_LEN_OFF}], 0",
                            f"mov qword [rax+{self.DICT_TOMB_OFF}], 0",
                            f"mov [rbp{tmp_off:+d}], rax",
                            f"mov rbx, {8 * self.DICT_SLOT_SIZE}",
                            "call _runtime_zalloc",
                        )
                        self.emitf(
                            "mov rbx, rax",
                            f"mov rax, [rbp{tmp_off:+d}]",
                            f"mov [rax+{self.DICT_BUF_OFF}], rbx",
                        )
                        self._emit_dict_alloc_order_buf(8, tmp_off)
                    else:
                        continue  # unevaluable factory -> leave unset
                else:
                    self.gen_expr(val_expr, info)
                    if A.expr_type(val_expr) == "float":
                        self.emitf("movq rax, xmm0")
                # Store: dict_set(inst, "fname", value-in-rax).
                key_label, _ = self.intern_string(fname)
                self.emitf(
                    "mov rcx, rax",
                    f"lea rbx, [{key_label}]",
                    f"mov rax, [rbp{slot_off:+d}]",
                    "call _runtime_dict_set",
                )
        # Result: the instance pointer (caller may discard).
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    def _method_symbol(self, class_name: str, method_name: str) -> str:
        """Mangle a method into a unique linker symbol: ClassName__method.

        Note that `__init__` mangles to `Class____init__` (four underscores)
        which is intentional — the separator is always two underscores.

        A plain instance method (not a staticmethod) so codegen stays within
        asmpython's self-compilable subset; `self` is unused but required.
        """
        return f"{class_name}__{method_name}"

    def _resolve_class_chain(self, name: str) -> list[str]:
        """Return [name, parent, grandparent, ...] for a class."""
        out: list[str] = []
        cur = name
        while cur is not None and cur not in out:
            out.append(cur)
            sig = self.mod.classes_sig.get(cur)
            cur = sig.parent if sig else None
        return out

    def _class_has_method(self, class_name: str, method: str) -> bool:
        sig = self.mod.classes_sig.get(class_name)
        return sig is not None and method in sig.methods

    def _resolve_method_owner(self, class_name: str, method: str) -> Optional[str]:
        for c in self._resolve_class_chain(class_name):
            if self._class_has_method(c, method):
                return c
        return None

    def _find_method_def(self, class_name: str, method: str):
        """The FuncDef for `method` resolved up `class_name`'s chain, or None.
        Used to read a method's decorators (@staticmethod / @classmethod)."""
        for cname in self._resolve_class_chain(class_name):
            for c in self.mod.classes:
                if c.name == cname:
                    for m in c.methods:
                        if m.name == method:
                            return m
        return None

    def _resolve_str_dunder(self, class_name: str) -> Optional[tuple[str, str]]:
        """(owner, method) of `__str__` or `__repr__` on `class_name`'s chain
        (in that priority order), or None if neither is defined. Used by
        str(), f-strings, and print() to stringify a user instance."""
        for method in ("__str__", "__repr__"):
            owner = self._resolve_method_owner(class_name, method)
            if owner is not None:
                return owner, method
        return None

    def _resolve_repr_dunder(self, class_name: str) -> Optional[tuple[str, str]]:
        """Like `_resolve_str_dunder` but for `repr()`/`!r`/`!a`: `__repr__`
        takes priority over `__str__` (the reverse of str()'s order)."""
        for method in ("__repr__", "__str__"):
            owner = self._resolve_method_owner(class_name, method)
            if owner is not None:
                return owner, method
        return None

    def _virtual_dispatch_rows(self, class_name: str, method: str) -> list:
        """[(class_id, owner)] for every user class that is `class_name` or
        descends from it and resolves `method` somewhere on its chain.

        A method call on a `class_name`-typed receiver can bind statically only
        when every row shares one owner. With overrides in play (the abstract-
        base pattern: `Codegen.emit_entry_prologue` raising NotImplementedError,
        overridden per target), the call must dispatch on the instance's
        runtime `__class__` id instead — the static type names the base, but
        the receiver at runtime is a subclass."""
        rows: list = []
        for cls in self.mod.classes:
            if class_name not in self._resolve_class_chain(cls.name):
                continue
            owner = self._resolve_method_owner(cls.name, method)
            if owner is not None:
                rows.append((self.class_ids[cls.name], owner))
        return rows

    def _method_defaults(self, class_name: str, method: str) -> list:
        """Return the parallel `defaults` list of the named method (in the
        class that actually defines it), or [] if no defaults are declared."""
        for cls in self.mod.classes:
            if cls.name != class_name:
                continue
            for m in cls.methods:
                if m.name == method:
                    return list(m.defaults)
        return []

    # ---- FFI dispatch ------------------------------------------------------
    #
    # The asmpython FFI is purely positional. Each foreign function's signature
    # is fully known statically (asmpython/stdlib/<mod>.py). The codegen
    # evaluates each argument, promotes int -> float as needed, and places it
    # in the correct ABI register slot. Integer args use the integer regs
    # (rdi/rsi/... on Linux, rcx/rdx/... on Windows); float args use XMM0..N.
    # Result type tells callers whether to expect rax or xmm0.

    def _gen_ffi_call(self, fn: stdlib.Func, args: list, info: FuncInfo) -> None:
        # Evaluate args left-to-right, spill each into a scratch frame slot.
        # We use `__ffi_arg<i>` slots that we pre-reserve in _collect_locals.
        slot_offs: list[int] = []
        for i, (arg, want) in enumerate(zip(args, fn.arg_types)):
            got = A.expr_type(arg)
            if want == "float":
                # Always end up with the float in xmm0, then spill.
                self._gen_expr_as_float(arg, info, got)
                slot = info.locals_[f"__ffi_arg_{id(fn)}_{i}"]
                self.emitf(f"movsd [rbp{slot:+d}], xmm0")
                slot_offs.append(slot)
            elif want == "list_buf":
                # Pass a list[int]'s underlying data buffer (not its header)
                # as a raw pointer -- see sema's _check_ffi_call.
                self.gen_expr(arg, info)
                self.emitf(f"mov rax, [rax+{self.LIST_BUF_OFF}]")
                slot = info.locals_[f"__ffi_arg_{id(fn)}_{i}"]
                self.emitf(f"mov [rbp{slot:+d}], rax")
                slot_offs.append(slot)
            else:  # int / str (pointer)
                self.gen_expr(arg, info)
                slot = info.locals_[f"__ffi_arg_{id(fn)}_{i}"]
                self.emitf(f"mov [rbp{slot:+d}], rax")
                slot_offs.append(slot)
        # Now load each arg into the ABI register slot, or onto the stack for
        # positions beyond the register count (Win64: 4 int + 8 xmm regs,
        # positionally assigned; SysV: 6 int + 8 xmm regs, assigned
        # independently per type — all our FFI signatures fit in SysV's
        # registers, but a >4-int-arg call like gui.create_window overflows
        # Win64's 4 integer argument registers).
        assigns = self._assign_arg_regs(list(fn.arg_types))
        stack_positions = [i for i, a in enumerate(assigns) if a is None]
        cleanup = 0
        if stack_positions:
            shadow = self._caller_shadow_space()
            area = shadow + 8 * len(stack_positions)
            if area % 16:
                area += 16 - (area % 16)
            cleanup = area
            self.emitf(f"sub rsp, {area}")
            for k, i in enumerate(stack_positions):
                self.emitf(
                    f"mov rax, [rbp{slot_offs[i]:+d}]",
                    f"mov [rsp+{shadow + 8 * k}], rax",
                )
        float_idx = 0
        for i, slot in enumerate(slot_offs):
            assign = assigns[i]
            if assign is None:
                continue
            reg, is_xmm = assign
            if is_xmm:
                self.emitf(f"movsd {reg}, [rbp{slot:+d}]")
                float_idx += 1
                # Windows: variadic functions also need the value mirrored
                # into the integer register; non-variadic functions don't
                # care. We mirror unconditionally on Windows because the
                # cost is one mov and the simplicity is worth it.
                if self._needs_xmm_mirror_to_int() and i < len(self._int_arg_regs()):
                    int_reg = self._int_arg_regs()[i]
                    self.emitf(f"movq {int_reg}, {reg}")
            else:
                self.emitf(f"mov {reg}, [rbp{slot:+d}]")
        # System V variadic ABI requires AL = number of xmm args used.
        # Non-variadic libc functions ignore AL. Setting it is harmless.
        if self._sysv_needs_al_count():
            self.emitf(f"mov al, {float_idx}")
        _ffi_sym = self._platform_c_name(fn)
        self.ffi_called.add(_ffi_sym)
        self.emit_call(_ffi_sym)
        if cleanup:
            self.emitf(f"add rsp, {cleanup}")
        if getattr(fn, "ret_conv", None) == "f2i":
            # The C function returns a double in xmm0 but asmpython's
            # ret_type is "int" (e.g. trunc/floor/ceil, which CPython's
            # math.trunc/floor/ceil narrow to int) -- truncate toward zero
            # into rax instead of reading eax/rax (which holds garbage).
            self.emitf("cvttsd2si rax, xmm0")
        elif getattr(fn, "ret_conv", None) == "ptr":
            # The C function returns a real 64-bit pointer/handle (e.g.
            # SDL_CreateWindow's SDL_Window*) in RAX, not a 32-bit C `int`.
            # Leave RAX as-is -- sign-extending just EAX would truncate the
            # pointer to its low 32 bits.
            pass
        elif fn.ret_type == "int":
            # C `int` is 32-bit: the callee returns it in EAX with the upper 32
            # bits of RAX undefined. asmpython values are full 64-bit slots, so
            # a result like fgetc()'s -1 (EOF) would otherwise read as
            # 0x00000000FFFFFFFF and never compare equal to -1. Sign-extend EAX
            # into RAX for int returns.
            self.emitf("movsxd rax, eax")

    def _gen_dynamic_call(
        self,
        e: A.MethodCall,
        funcdef: A.FuncDef,
        info: FuncInfo,
        handle_name: str | None = None,
        skip_self: bool = False,
    ) -> None:
        """`handle.func(args)` for a `@handle.imported` function: marshal args
        exactly like `_gen_ffi_call`, but call through the function pointer
        GetProcAddress/dlsym already resolved into the handle dict (keyed by
        `func`'s name) instead of a static `extern` symbol.

        `handle_name`/`skip_self` cover the wrapped-in-a-class shape (e.g.
        `instance.glClearColor(...)` where glClearColor is a `@glfns.imported`
        *method* on instance's class, not a direct `glfns.glClearColor(...)`
        call): `e.obj` is the instance (its own dict, unrelated to the GL
        handle), so the handle dict has to be fetched by `handle_name`
        instead of by evaluating `e.obj`; `funcdef.param_types`/`.params`
        still include `self` as their first entry (it's a real method), so
        `skip_self` drops that entry when matching against `e.args` (which,
        being the call's actual argument list, never includes the implicit
        receiver).

        Scalar (int/float/str-as-pointer) parameters are supported, plus
        `list[int]`/`list[float]` as `list_buf` (the list's raw backing
        buffer pointer, not its header -- see _gen_ffi_call's identical
        "list_buf" handling, used the same way by e.g. os._stat's
        struct-out-param and GL functions like glShaderSource/glBufferData
        that take a raw data pointer). The foreign function's real
        signature isn't introspectable, so the stub's own annotations are
        the only contract; an unannotated parameter defaults to "int".

        For a handle from gl_import() specifically (tracked in
        self.gl_import_handles), every `float` argument/return is narrowed
        to/from a 32-bit C `float` (GLfloat) instead of staying a 64-bit
        `double` -- OpenGL's API is GLfloat throughout (glClearColor,
        glUniform*f, ...), but asmpython's `float` is always a double;
        passing a double's raw 64 bits where the driver reads a 32-bit
        float silently corrupts the value (confirmed: double 1.0's low 32
        bits decode as float 0.0 -- exactly why glClearColor(1.0, ...)
        visibly did nothing despite a correct call reaching the correct,
        verified-via-disassembly-identical-to-a-working-C-program function
        pointer). The handful of genuine GLdouble functions are listed in
        _GL_DOUBLE_FUNCS below and excluded from this narrowing -- they
        were never the bug (glDepthRange's GLdouble args already round-
        tripped correctly as asmpython doubles; narrowing THEM would be
        the new bug).
        """
        effective_handle: str | None = handle_name
        if effective_handle is None and isinstance(e.obj, A.Name):
            effective_handle = e.obj.name
        is_gl: bool = (
            effective_handle is not None
            and effective_handle in self.gl_import_handles
            and funcdef.name not in self._GL_DOUBLE_FUNCS
        )
        # When skip_self is set, funcdef.param_types[0] is `self`'s
        # annotation slot (always None/unused) -- offset past it so
        # param_types[1:] lines up positionally with e.args, the same way
        # it already does for a plain function/direct handle call.
        param_offset = 1 if skip_self else 0
        arg_types: list[str] = []
        for i in range(len(e.args)):
            pidx = i + param_offset
            annot = funcdef.param_types[pidx] if pidx < len(funcdef.param_types) else None
            base = annot[0] if annot else "int"
            if base == "list":
                arg_types.append("list_buf")
                continue
            if base not in ("int", "float", "str"):
                raise NotImplementedError(
                    f"@{effective_handle}.imported function {funcdef.name!r}: "
                    f"parameter type {base!r} is not supported "
                    "(only int/float/str/list[int]/list[float])"
                )
            arg_types.append(base)
        slot_offs: list[int] = []
        for i, (arg, want) in enumerate(zip(e.args, arg_types)):
            got = A.expr_type(arg)
            slot = info.locals_[f"__dyncall_arg_{id(e)}_{i}"]
            if want == "float":
                self._gen_expr_as_float(arg, info, got)
                if is_gl:
                    self.emitf("cvtsd2ss xmm0, xmm0")
                    self.emitf(f"movss [rbp{slot:+d}], xmm0")
                else:
                    self.emitf(f"movsd [rbp{slot:+d}], xmm0")
            elif want == "list_buf":
                self.gen_expr(arg, info)  # rax = list header
                self.emitf(f"mov rax, [rax+{self.LIST_BUF_OFF}]")
                self.emitf(f"mov [rbp{slot:+d}], rax")
            else:
                self.gen_expr(arg, info)
                self.emitf(f"mov [rbp{slot:+d}], rax")
            slot_offs.append(slot)
        assigns = self._assign_arg_regs(arg_types)
        stack_positions = [i for i, a in enumerate(assigns) if a is None]
        # Always reserve shadow space before the indirect `call rax` below,
        # even with zero stack-passed args: Win64 requires the caller to
        # reserve 32 bytes of shadow space below rsp for *every* call, not
        # just ones with 5+ arguments (the same class of bug as the
        # hand-rolled runtime helpers' shadow-space fix -- see CHANGELOG).
        # Without this, a 1-4-argument dynamically-resolved call (e.g.
        # `glGetString(name)`, one int arg) had stack_positions == [] and
        # skipped shadow-space allocation entirely, letting the callee
        # silently corrupt the caller's own frame -- confirmed: glGetString
        # resolved to a real, distinct, correct function pointer and was
        # called with the right argument, but returned NULL/silently
        # corrupted state instead of erroring, because Linux/SysV has no
        # shadow-space requirement at all (this bug is Windows-only) while
        # the GL driver's internal call on Windows clobbered our frame.
        shadow = self._caller_shadow_space()
        area = shadow + 8 * len(stack_positions)
        if area % 16:
            area += 16 - (area % 16)
        cleanup = area
        # Look up the resolved function pointer (stored by name in the
        # handle dict at import_binary()/gl_import() time) BEFORE reserving
        # this call's own shadow space / stack args, and before loading arg
        # registers -- the lookup itself is a `call _runtime_dict_get_default`,
        # which (like any call) needs its OWN 32 bytes of shadow space below
        # rsp. Reserving this call's frame (`sub rsp, area`) first and then
        # calling the lookup *inside* that already-adjusted frame let the
        # lookup's internal shadow-space usage land on the exact same
        # [rsp, rsp+32) bytes already written with stack-spilled arguments
        # (positions 5+, e.g. glReadPixels' format/type/data params),
        # silently corrupting them before the real call ever ran (confirmed:
        # glReadPixels resolved correctly and was reached, but its
        # stack-spilled args were always read back as garbage/zero).
        ptr_slot = info.locals_[f"__dyncall_ptr_{id(e)}"]
        name_label, _ = self.intern_string(funcdef.name)
        if handle_name is not None:
            # Method-wrapped form: the handle dict is a module-level global
            # (`glfns`, not `e.obj` -- e.obj is `self`/the instance, an
            # unrelated dict) referenced purely by name, so synthesize the
            # same Name-lookup a direct `glfns.func(...)` call already does.
            self.gen_expr(A.Name(name=handle_name, pos=e.pos), info)  # rax = handle dict
        else:
            self.gen_expr(e.obj, info)  # rax = handle dict
        if is_gl:
            # Lazy resolve-and-cache (see _gen_gl_import's docstring for
            # why gl_import() can't resolve eagerly): look up the cached
            # pointer first; a NULL/missing result means "not resolved
            # yet" (a real GL function pointer is never NULL once a
            # context exists), so resolve it via SDL_GL_GetProcAddress and
            # store it back into the handle dict before proceeding, the
            # same one-time cost _gen_gl_import used to pay for every
            # function up front regardless of whether it was ever called.
            dict_slot = info.locals_[f"__dyncall_gldict_{id(e)}"]
            self.emitf(f"mov [rbp{dict_slot:+d}], rax")  # save handle dict
            self.emitf(f"lea rbx, [{name_label}]", "xor rcx, rcx", "call _runtime_dict_get_default")
            self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
            not_null = self.fresh("gllazy_resolved")
            self.emitf("test rax, rax", f"jnz {not_null}")
            self.emitf(f"lea rax, [{name_label}]")
            self._emit_get_gl_proc_addr()  # rax = resolved function ptr, or NULL
            self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
            self.emitf(
                "mov rcx, rax",
                f"lea rbx, [{name_label}]",
                f"mov rax, [rbp{dict_slot:+d}]",
                "call _runtime_dict_set",
            )
            self.label(not_null)
        else:
            self.emitf(f"lea rbx, [{name_label}]", "xor rcx, rcx", "call _runtime_dict_get_default")
            self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
        self.emitf(f"sub rsp, {area}")
        for k, i in enumerate(stack_positions):
            self.emitf(
                f"mov rax, [rbp{slot_offs[i]:+d}]",
                f"mov [rsp+{shadow + 8 * k}], rax",
            )
        float_idx = 0
        for i, slot in enumerate(slot_offs):
            assign = assigns[i]
            if assign is None:
                continue
            reg, is_xmm = assign
            if is_xmm:
                if is_gl and arg_types[i] == "float":
                    # 32-bit GLfloat: only the low 4 bytes of the slot are
                    # meaningful (written by movss above) -- load with
                    # movss, not movsd, or the high 4 (stale/undefined)
                    # bytes would corrupt the value. Skip the int-register
                    # mirror entirely: it exists only for Win64's variadic
                    # convention (a float arg also needs to land in the
                    # matching integer register so a varargs callee can
                    # read it positionally without knowing the type), and
                    # no GL function is variadic -- mirroring a
                    # single-precision value into a 64-bit int register
                    # with movq would also be the wrong width regardless.
                    self.emitf(f"movss {reg}, [rbp{slot:+d}]")
                else:
                    self.emitf(f"movsd {reg}, [rbp{slot:+d}]")
                    if self._needs_xmm_mirror_to_int() and i < len(self._int_arg_regs()):
                        int_reg = self._int_arg_regs()[i]
                        self.emitf(f"movq {int_reg}, {reg}")
                float_idx += 1
            else:
                self.emitf(f"mov {reg}, [rbp{slot:+d}]")
        if self._sysv_needs_al_count():
            self.emitf(f"mov al, {float_idx}")
        self.emitf(f"mov rax, [rbp{ptr_slot:+d}]", "call rax")
        if cleanup:
            self.emitf(f"add rsp, {cleanup}")
        # Unlike _gen_ffi_call, no sign-extending truncation (`movsxd rax,
        # eax`) here for `-> int`-declared returns: a dynamically-resolved
        # function's real C return width isn't known (the stub's `-> int`
        # annotation is the caller's best guess, not an introspected
        # signature), and plenty of real functions called this way return a
        # genuine 64-bit pointer through an `int`-typed stub (e.g.
        # glGetString's `const GLubyte*`, lumen.gl's whole reason for
        # dynamic resolution). Truncating-then-sign-extending a real
        # pointer corrupts it whenever its high 32 bits matter; leaving rax
        # untouched is correct for a real pointer and for any `int`-typed
        # return where the callee already zero-extends eax into rax (true
        # for both MSVC- and GCC-compiled callees on x86-64, which use
        # `mov eax, ...` and rely on the architecture's implicit
        # zero-extension to upper 32 bits).
        ret_base = funcdef.ret_type[0] if funcdef.ret_type else "int"
        if is_gl and ret_base == "float":
            # The callee returns a 32-bit GLfloat in the low 32 bits of
            # xmm0; widen to a 64-bit double (asmpython's only float
            # width) so the rest of the program reads a correct value
            # instead of the low-32-bits-only garbage a bare xmm0 would
            # decode as if read as a double directly.
            self.emitf("cvtss2sd xmm0, xmm0")

    # ---- os.getcwd / os.listdir inline helpers --------------------------------

    def _emit_os_getcwd(self) -> None:
        """Emit code for os.getcwd() -> str. Result in rax."""
        raise NotImplementedError

    def _emit_os_listdir(self, path_arg, info: FuncInfo) -> None:
        """Emit code for os.listdir(path) -> list[str]. Result in rax."""
        raise NotImplementedError

    def _emit_load_library(self) -> None:
        """rax = path -> rax = library handle, or NULL. See _gen_import_binary."""
        raise NotImplementedError

    def _emit_get_proc_addr(self) -> None:
        """rax = handle, rbx = name -> rax = function ptr, or NULL."""
        raise NotImplementedError

    def _emit_get_gl_proc_addr(self) -> None:
        """rax = name -> rax = GL function ptr, or NULL. See _gen_gl_import."""
        raise NotImplementedError

    def _emit_cwd_buf_if_needed(self) -> None:
        """Emit the static _cwd_buf BSS slot if os.getcwd() was used."""
        if self._needs_cwd_buf:
            self.emit("section .bss")
            self.emit("_cwd_buf: resb 4096")

    def _platform_c_name(self, fn) -> str:
        """Return the symbol name to use for this target (may differ from fn.c_name)."""
        return fn.c_name

    def _int_arg_regs(self) -> list[str]:
        raise NotImplementedError

    def _needs_xmm_mirror_to_int(self) -> bool:
        return False  # Overridden on Windows.

    def _sysv_needs_al_count(self) -> bool:
        return False  # Overridden on Linux.

    # ---- call sequence (ABI-specific stack alignment) ----------------------

    def emit_call(self, target: str) -> None:
        raise NotImplementedError

    # ---- print impls --------------------------------------------------------
    #
    # print(a, b, c) emits, per-arg, the value (no newline) then a space,
    # except after the last arg, after which it emits a newline. Empty call
    # `print()` just emits a newline.

    def _gen_print(self, e: A.Call, info: FuncInfo) -> None:
        # Extract sep= / end= kwargs (all optional; defaults: " ", "\n").
        # file= is accepted but ignored for routing purposes — print always
        # goes to stdout in the current implementation.
        kwargs = {}
        for kn, kv in (getattr(e, "kwargs", None) or []):
            kwargs[kn] = kv
        sep_expr = kwargs.get("sep")
        end_expr = kwargs.get("end")
        if not e.args:
            if end_expr is not None and isinstance(end_expr, A.StrLit):
                end_str = end_expr.value
                if end_str:
                    end_lbl, _ = self.intern_string(end_str)
                    self.emitf(f"lea rax, [rel {end_lbl}]")
                    self._emit_print_str_ptr_no_newline()
            else:
                self._emit_print_newline()
            return
        for i, arg in enumerate(e.args):
            if isinstance(arg, A.FString):
                for seg in arg.segments:
                    self._emit_print_value(seg, info)
            else:
                self._emit_print_value(arg, info)
            if i < len(e.args) - 1:
                if sep_expr is not None and isinstance(sep_expr, A.StrLit):
                    sep_str = sep_expr.value
                    if sep_str:
                        sep_lbl, _ = self.intern_string(sep_str)
                        self.emitf(f"lea rax, [rel {sep_lbl}]")
                        self._emit_print_str_ptr_no_newline()
                    # sep="" → no separator at all
                else:
                    self._emit_print_space()
        if end_expr is not None and isinstance(end_expr, A.StrLit):
            end_str = end_expr.value
            if end_str == "\n":
                self._emit_print_newline()
            elif end_str:
                end_lbl, _ = self.intern_string(end_str)
                self.emitf(f"lea rax, [rel {end_lbl}]")
                self._emit_print_str_ptr_no_newline()
            # end="" → no terminator
        else:
            self._emit_print_newline()

    def _list_repr_kind(self, expr) -> int:
        """Map a list/tuple expression's element type to the repr kind code
        used by _runtime_list_repr (see _composite_repr_kind)."""
        el = "int"
        el_val = "int"
        if isinstance(expr, A.Name):
            el = expr.list_el_type or "int"
            el_val = expr.list_el_value_type or "int"
        elif isinstance(expr, A.ListLit):
            el = expr.el_type or "int"
            el_val = getattr(expr, "el_value_type", "int") or "int"
        else:
            el = getattr(expr, "list_el_type", "int") or "int"
            el_val = getattr(expr, "list_el_value_type", "int") or "int"
        return self._composite_repr_kind(el, el_val)

    _REPR_KIND = {"str": 1, "float": 2}

    def _value_repr_kind(self, t: str) -> int:
        """Repr kind code (0 int, 1 str, 2 float) for a scalar type name."""
        return self._REPR_KIND.get(t, 0)

    def _composite_repr_kind(self, t: str, inner: str) -> int:
        """Repr kind code for a value of static type `t`, where `inner` is
        the one-level-deep element/value kind when `t` is itself a 'list' or
        'dict' (so _runtime_fmt_elem can recurse via _runtime_list_repr /
        _runtime_dict_repr). Encoding: base kind in bits 0-3 (0=int, 1=str,
        2=float, 3=list, 4=dict), inner scalar kind in bits 4-7."""
        if t == "list":
            return 3 | (self._value_repr_kind(inner) << 4)
        if t == "dict":
            return 4 | (self._value_repr_kind(inner) << 4)
        return self._value_repr_kind(t)

    def _emit_container_repr(self, expr, t: str) -> None:
        """Given a container value already in rax and its static type `t`
        (list/tuple/dict/set), emit a call leaving its repr string in rax."""
        if t == "tuple":
            self._emit_tuple_repr_inline(expr)
        elif t == "list":
            self.emitf(f"mov rbx, {self._list_repr_kind(expr)}", "call _runtime_list_repr")
        elif t == "dict":
            # Keys are str-hashed; values carry a tracked kind.
            vt = getattr(expr, "value_type", "int") or "int"
            inner = getattr(expr, "inner_value_type", "int") or "int"
            vk = self._composite_repr_kind(vt, inner)
            self.emitf("mov rbx, 1", f"mov rcx, {vk}", "call _runtime_dict_repr")
        elif t == "set":
            # Set elements are str-keyed in asmpython's set model.
            self.emitf("mov rbx, 1", "call _runtime_set_repr")

    def _emit_tuple_repr_inline(self, expr) -> None:
        """Tuple value in rax -> repr string `(a, b, c)` in rax.

        Tuples have heterogeneous per-slot kinds known at compile time, so we
        unroll. The tuple ptr and accumulator live on the stack (pushed as a
        16-byte pair to keep rsp aligned) across the conversion/concat calls.
        Matches CPython's trailing comma for 1-tuples: `(x,)`.
        """
        kinds = A.tuple_element_types(expr)
        n = len(kinds)
        # tuple ptr in rax. Reserve [rsp]=acc, [rsp+8]=tuple ptr. Push the tuple
        # ptr first and a placeholder for acc; concat_dup clobbers rbx, so we
        # can't keep the tuple ptr in a register across it.
        self.emitf(
            "push rax",                # [rsp+8] eventually = tuple ptr (saved first)
            "push rax",                # [rsp]   = placeholder for acc
            "lea rax, [_runtime_lparen_str]",
            "call _runtime_str_concat_dup",  # rax = fresh "("
            "mov [rsp], rax",          # [rsp] = acc; [rsp+8] still = tuple ptr
        )
        for i, k in enumerate(kinds):
            kind = self._value_repr_kind(k)
            if i > 0:
                # acc = acc + ", "
                self.emitf(
                    "mov rax, [rsp]",
                    "lea rbx, [_runtime_comma_str]",
                    "call _runtime_str_concat",
                    "mov [rsp], rax",
                )
            # load element i: tuple_buf[i]
            self.emitf(
                "mov rbx, [rsp+8]",                        # tuple ptr
                f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",     # buf
                f"mov rax, [rbx+{i * 8}]",                 # element
                f"mov rbx, {kind}",
                "call _runtime_fmt_elem",                  # rax = elem repr
                "mov rbx, rax",
                "mov rax, [rsp]",
                "call _runtime_str_concat",
                "mov [rsp], rax",
            )
        # close: 1-tuple gets a trailing comma -> "(x,)"
        close = "_runtime_comma_rparen_str" if n == 1 else "_runtime_rparen_str"
        self.emitf(
            "mov rax, [rsp]",
            f"lea rbx, [{close}]",
            "call _runtime_str_concat",
            "add rsp, 16",   # pop acc + tuple ptr
        )

    def _emit_print_value(self, expr, info: FuncInfo) -> None:
        """Emit code that prints a single typed value (no newline, no space)."""
        t = A.expr_type(expr)
        spec = getattr(expr, "fmt_spec", "")
        conv = getattr(expr, "conv_flag", "")
        # An f-string segment may carry a `:format-spec` (e.g. `{x:.2f}`) or a
        # `!r`/`!s`/`!a` conversion. Route those through the segment
        # formatter (which always yields a str pointer), then print it.
        if (spec and t in ("int", "float", "str")) or (
            conv and (t in ("str", "int", "float") or t.startswith("instance:"))
        ):
            self._gen_fstring_segment(expr, info)
            self._emit_print_str_ptr_no_newline()
            return
        self.gen_expr(expr, info)
        if getattr(expr, "dict_get_none_default", False):
            # dict.get(k) with no explicit default: 0 means "key missing" → None
            _dg_nonzero = self.fresh("dg_nonzero")
            _dg_done = self.fresh("dg_done")
            if t == "float":
                # float zero is 0x0000...0 in IEEE754, so test rax works
                self.emitf(f"test rax, rax", f"jnz {_dg_nonzero}")
                self._emit_print_none_no_newline()
                self.emitf(f"jmp {_dg_done}")
                self.label(_dg_nonzero)
                self.emitf("movq xmm0, rax")
                self._emit_print_float_no_newline()
            else:
                self.emitf(f"test rax, rax", f"jnz {_dg_nonzero}")
                self._emit_print_none_no_newline()
                self.emitf(f"jmp {_dg_done}")
                self.label(_dg_nonzero)
                if t == "str":
                    self._emit_print_str_ptr_no_newline()
                else:
                    self._emit_print_int_no_newline()
            self.label(_dg_done)
            return
        if t == "str":
            self._emit_print_str_ptr_no_newline()
        elif t == "int" and A.is_bool_expr(expr):
            self._emit_print_bool_no_newline()
        elif t == "int" and A.is_none_expr(expr):
            self._emit_print_none_no_newline()
        elif t == "float":
            self._emit_print_float_no_newline()
        elif t in ("list", "tuple", "dict", "set"):
            self._emit_container_repr(expr, t)
            self._emit_print_str_ptr_no_newline()
        elif t.startswith("instance:"):
            resolved = self._resolve_str_dunder(t.split(":", 1)[1])
            if resolved is not None:
                # Same NULL guard as _gen_fstring_segment's instance case
                # just above, and for the same reason: a statically
                # `instance:T`-typed expr can be a runtime-None
                # `Optional[T]` value, which is_none_expr can't catch
                # (it only sees statically-always-None expressions).
                # print()-ing such a value must call the dunder unconditionally
                # only when non-NULL.
                none_lbl = self.fresh("print_inst_none")
                end_lbl = self.fresh("print_inst_end")
                self.emitf("test rax, rax", f"jz {none_lbl}")
                owner, method = resolved
                self.emitf(f"mov {self._arg_reg(0)}, rax")
                self.emit_call(self._method_symbol(owner, method))
                self._emit_print_str_ptr_no_newline()
                self.emitf(f"jmp {end_lbl}")
                self.label(none_lbl)
                self._emit_print_none_no_newline()
                self.label(end_lbl)
            else:
                self._emit_print_int_no_newline()
        else:
            self._emit_print_int_no_newline()

    # Runtime primitives provided by the target subclass. Declared here as
    # abstract stubs so type-checkers can see them on the base class — that
    # way callers in `Codegen` don't each need a `# type: ignore`.

    def emit_print_impls(self) -> None:
        raise NotImplementedError

    def _asmlib_inline_syms(self) -> set:
        """Return symbols that this target defines inline via emit_asmlib_runtime.

        These must NOT be declared `extern` in the output assembly — emitting
        both `extern X` and the label `X:` in the same file is a NASM error.
        The base implementation returns an empty set; hosted targets override.
        """
        return set()

    def emit_asmlib_runtime(self) -> None:
        """Emit inline helper bodies for asmlib symbols that have no libc equivalent.

        Called by each hosted target's emit_print_impls when _net_*, _gui_*, or
        _hw_* symbols appear in ffi_externs.  The default implementation is a
        no-op; targets override to provide their ABI-specific bodies.
        """

    def _emit_console_runtime(self) -> None:
        """Emit asmlib.hardware's `_hw_console_*` helpers (real on Windows/Linux).

        Shared by both hosted targets: each links libc with printf/sprintf/
        putchar at the same prototypes, and `_arg_reg(i)` already maps to the
        platform's i-th integer argument register for a C call. On
        `--target freestanding` these names are instead thin wrappers around
        the VGA helpers `print()` uses (see target_freestanding.py); this is
        not called there.

        Output is driven by ANSI/VT100 escapes (clear+home, SGR color, cursor
        position) written via printf. Escapes are write-only, so the cursor
        position returned by console_get_row/console_get_col is tracked
        locally in _con_row/_con_col, updated by putc/write/set_cursor.

        Each helper assumes the standard ABI invariant that rsp % 16 == 8 on
        entry (a `call` just pushed the return address onto an aligned
        stack), and reserves 32/40 bytes so the printf/sprintf/putchar calls
        inside happen from a 16-byte-aligned rsp (Windows also needs >= 32
        bytes of shadow space for these calls).
        """
        a0, a1, a2, a3 = (self._arg_reg(i) for i in range(4))

        self.emit("")
        self.emit("section .bss")
        self.emit("_con_row:   resq 1")
        self.emit("_con_col:   resq 1")
        self.emit("_con_ch:    resq 1")
        self.emit("_con_ansi1: resq 1")
        self.emit("_con_ansi2: resq 1")
        self.emit("_con_buf:   resb 32")

        self.emit("")
        self.emit(self.section_rodata)
        self.emit('_con_fmt_clear:  db 27, "[2J", 27, "[H", 0')
        self.emit('_con_fmt_color:  db 27, "[%dm", 27, "[%dm", 0')
        self.emit('_con_fmt_cursor: db 27, "[%d;%dH", 0')

        self.emit("")
        self.emit("section .text")

        # console_clear() -- ESC[2J ESC[H, and reset the tracked cursor to (0, 0).
        self.label("_hw_console_clear")
        self.emitf("sub rsp, 40")
        self.emitf(f"lea {a0}, [_con_fmt_clear]", "xor eax, eax", "call printf")
        self.emitf("xor eax, eax", "mov [_con_row], rax", "mov [_con_col], rax")
        self.emitf("add rsp, 40", "ret")

        # console_putc(ch) -- putchar(ch); newline wraps row/col, else col++.
        self.label("_hw_console_putc")
        self.emitf("sub rsp, 40")
        self.emitf(f"mov [_con_ch], {a0}", "call putchar")
        self.emitf("mov rax, [_con_ch]", "cmp rax, 10", "je .nl")
        self.emitf("inc qword [_con_col]", "jmp .done")
        self.label(".nl")
        self.emitf("mov qword [_con_col], 0", "inc qword [_con_row]")
        self.label(".done")
        self.emitf("xor eax, eax", "add rsp, 40", "ret")

        # console_write(s) -- printf("%s", s); track row/col over each char.
        self.label("_hw_console_write")
        self.emitf("push rbx", "sub rsp, 32")
        self.emitf(f"mov rbx, {a0}", f"mov {a1}, {a0}", f"lea {a0}, [fmt_str]",
                   "xor eax, eax", "call printf")
        self.label(".loop")
        self.emitf("movzx eax, byte [rbx]", "test eax, eax", "jz .done")
        self.emitf("cmp eax, 10", "je .nl")
        self.emitf("inc qword [_con_col]", "jmp .next")
        self.label(".nl")
        self.emitf("mov qword [_con_col], 0", "inc qword [_con_row]")
        self.label(".next")
        self.emitf("inc rbx", "jmp .loop")
        self.label(".done")
        self.emitf("xor eax, eax", "add rsp, 32", "pop rbx", "ret")

        # console_set_color(fg, bg) -- VGA palette index -> ANSI SGR code
        # (0-7 -> 30-37/40-47 "normal"; 8-15 fg/bg -> 90-97/100-107 "bright"
        # aixterm codes), then write "ESC[<fg>m ESC[<bg>m".
        self.label("_hw_console_set_color")
        self.emitf("sub rsp, 40")
        self.emitf(f"mov rax, {a0}", "cmp rax, 8", "jl .fg_lo",
                   "add rax, 82", "jmp .fg_done")
        self.label(".fg_lo")
        self.emitf("add rax, 30")
        self.label(".fg_done")
        self.emitf("mov [_con_ansi1], rax")
        self.emitf(f"mov rax, {a1}", "cmp rax, 8", "jl .bg_lo",
                   "add rax, 92", "jmp .bg_done")
        self.label(".bg_lo")
        self.emitf("add rax, 40")
        self.label(".bg_done")
        self.emitf("mov [_con_ansi2], rax")
        self.emitf(f"lea {a0}, [_con_buf]", f"lea {a1}, [_con_fmt_color]",
                   f"mov {a2}, [_con_ansi1]", f"mov {a3}, [_con_ansi2]",
                   "xor eax, eax", "call sprintf")
        self.emitf(f"lea {a0}, [fmt_str]", f"lea {a1}, [_con_buf]",
                   "xor eax, eax", "call printf")
        self.emitf("xor eax, eax", "add rsp, 40", "ret")

        # console_set_cursor(row, col) -- track 0-indexed (row, col); write
        # "ESC[<row+1>;<col+1>H" (ANSI cursor positions are 1-indexed).
        self.label("_hw_console_set_cursor")
        self.emitf("sub rsp, 40")
        self.emitf(f"mov rax, {a0}", "mov [_con_row], rax", "inc rax",
                   "mov [_con_ansi1], rax")
        self.emitf(f"mov rax, {a1}", "mov [_con_col], rax", "inc rax",
                   "mov [_con_ansi2], rax")
        self.emitf(f"lea {a0}, [_con_buf]", f"lea {a1}, [_con_fmt_cursor]",
                   f"mov {a2}, [_con_ansi1]", f"mov {a3}, [_con_ansi2]",
                   "xor eax, eax", "call sprintf")
        self.emitf(f"lea {a0}, [fmt_str]", f"lea {a1}, [_con_buf]",
                   "xor eax, eax", "call printf")
        self.emitf("xor eax, eax", "add rsp, 40", "ret")

        # console_get_row() / console_get_col() -- tracked cursor position.
        self.label("_hw_console_get_row")
        self.emitf("mov rax, [_con_row]", "ret")
        self.label("_hw_console_get_col")
        self.emitf("mov rax, [_con_col]", "ret")

    # Print helpers --------------------------------------------------------
    def _emit_print_int_no_newline(self) -> None:
        """In: rax = signed int. Emits the value to stdout, no newline."""
        raise NotImplementedError

    def _emit_print_str_ptr_no_newline(self) -> None:
        """In: rax = nul-terminated ptr. Emits the string, no newline."""
        raise NotImplementedError

    def _emit_print_float_no_newline(self) -> None:
        """In: xmm0 = double. Emits the value with `%g`, no newline."""
        raise NotImplementedError

    def _emit_bool_to_str(self) -> None:
        """In: rax = 0/1. Out: rax = pointer to static "True"/"False"."""
        false_lbl = self.fresh("booltostr_false")
        end_lbl = self.fresh("booltostr_end")
        self.emitf("test rax, rax", f"jz {false_lbl}")
        self.emitf("lea rax, [_runtime_true_str]", f"jmp {end_lbl}")
        self.label(false_lbl)
        self.emitf("lea rax, [_runtime_false_str]")
        self.label(end_lbl)

    def _emit_print_bool_no_newline(self) -> None:
        """In: rax = 0/1. Emits "False"/"True" to stdout, no newline."""
        self._emit_bool_to_str()
        self._emit_print_str_ptr_no_newline()

    def _emit_print_none_no_newline(self) -> None:
        """Emits "None" to stdout, no newline."""
        self.emitf("lea rax, [_runtime_none_str]")
        self._emit_print_str_ptr_no_newline()

    def _emit_print_space(self) -> None:
        raise NotImplementedError

    def _emit_print_newline(self) -> None:
        raise NotImplementedError

    def _emit_strlen(self) -> None:
        """In: rax = ptr. Out: rax = byte length. Like _emit_libc_strlen but
        the target may inline a fast variant for the print path."""
        raise NotImplementedError

    # Numeric / string conversions ----------------------------------------
    def _emit_int_to_str(self) -> None:
        """In: rax = int. Out: rax = pointer to nul-terminated ASCII."""
        raise NotImplementedError

    def _emit_str_to_int(self) -> None:
        """In: rax = nul-terminated ptr. Out: rax = parsed int (atoll)."""
        raise NotImplementedError

    def _emit_str_to_int_base(self) -> None:
        """In: rax = nul-terminated ptr, rbx = base. Out: rax = parsed int.
        Uses strtoll; base 0 auto-detects 0x/0o/0b prefixes."""
        raise NotImplementedError

    def _emit_normalize_0b_prefix(self) -> None:
        """In: rax = str ptr, rbx = base. Out: rax/rbx possibly adjusted.

        C's strtoll with base 0 understands 0x (hex) and a leading 0 (octal)
        but NOT Python's 0b/0B binary prefix. Emit a guard that, when base==0
        and the string starts with '0b'/'0B', advances rax past the prefix and
        forces rbx=2. Other inputs pass through untouched. Called by each
        target's `_emit_str_to_int_base` just before invoking strtoll, so the
        ABI-specific register moves happen on the already-normalized values."""
        done = self.fresh("notbin")
        self.emitf(
            "cmp rbx, 0",
            f"jne {done}",  # explicit base given -> leave alone
            "cmp byte [rax], '0'",
            f"jne {done}",
            "mov cl, byte [rax+1]",
            "or cl, 0x20",  # lowercase
            "cmp cl, 'b'",
            f"jne {done}",
            "add rax, 2",  # skip "0b"
            "mov rbx, 2",  # force binary
        )
        self.label(done)

    def _emit_float_to_str(self) -> None:
        """In: xmm0 = double. Out: rax = ptr to nul-terminated `%g` form."""
        raise NotImplementedError

    def _emit_float_repr_fixup(self) -> None:
        """In/out: rax = ptr to a nul-terminated `%g`-formatted float string,
        mutated in place.

        C's `%g` drops the decimal point entirely for whole numbers (`2.0`
        -> `"2"`, `-0.0` -> `"-0"`), but CPython's float repr always shows
        one (`"2.0"`, `"-0.0"`). Scan for a byte that already marks the value
        as non-integral or non-finite ('.', 'e'/'E' for exponents, 'n'/'N'/
        'i'/'I' for "nan"/"inf"/"-inf") and, if none is found before the
        terminator, append ".0". Only `_emit_float_to_str` (the bare
        `print(x)`/`str(x)` path) should call this -- not `_emit_float_fmt`,
        whose explicit format specs (`.2f` etc.) already match Python."""
        scan = self.fresh("frf_scan")
        append = self.fresh("frf_append")
        done = self.fresh("frf_done")
        self.emitf("mov rbx, rax")
        self.label(scan)
        self.emitf(
            "mov cl, [rbx]",
            "test cl, cl",
            f"jz {append}",
            "cmp cl, '.'",
            f"je {done}",
            "cmp cl, 'e'",
            f"je {done}",
            "cmp cl, 'E'",
            f"je {done}",
            "cmp cl, 'n'",
            f"je {done}",
            "cmp cl, 'N'",
            f"je {done}",
            "cmp cl, 'i'",
            f"je {done}",
            "cmp cl, 'I'",
            f"je {done}",
            "inc rbx",
            f"jmp {scan}",
        )
        self.label(append)
        self.emitf(
            "mov byte [rbx], '.'",
            "mov byte [rbx+1], '0'",
            "mov byte [rbx+2], 0",
        )
        self.label(done)

    def _emit_float_fmt(self, fmt_label: str) -> None:
        """In: xmm0 = double. Out: rax = ptr to sprintf(buf, <fmt_label>, x).
        `fmt_label` names a .rodata C format string (e.g. `"%.2f"`)."""
        raise NotImplementedError

    def _emit_int_fmt(self, fmt_label: str) -> None:
        """In: rax = int. Out: rax = ptr to sprintf(buf, <fmt_label>, x)."""
        raise NotImplementedError

    def _emit_str_to_float(self) -> None:
        """In: rax = nul-terminated ptr. Out: xmm0 = parsed double (atof)."""
        raise NotImplementedError

    # I/O ------------------------------------------------------------------
    def _emit_strtoll_endptr(self) -> None:
        """In: rax = str ptr, rbx = ptr to char* storage for endptr, rcx = base.
        Out: rax = int64 result; *rbx = first unconsumed character ptr.
        Implementations must preserve rbx so the caller can load from it."""
        raise NotImplementedError

    def _emit_input_line(self) -> None:
        """Out: rax = ptr to the most recent input buffer (\\n stripped)."""
        raise NotImplementedError

    # Memory ---------------------------------------------------------------
    def _emit_malloc(self, n: int) -> None:
        """Compile-time `n` bytes -> rax = ptr. (`n` is used by overrides.)"""
        raise NotImplementedError

    def _emit_libc_malloc_size_in_rax(self) -> None:
        """In: rax = size. Out: rax = ptr."""
        raise NotImplementedError

    def _emit_libc_memset_zero(self) -> None:
        """In: rax = ptr, rbx = len. Zeros `len` bytes starting at `ptr`."""
        raise NotImplementedError

    def _emit_libc_memcpy(self) -> None:
        """In: rax = dst, rbx = src, rcx = n. Copies `n` bytes."""
        raise NotImplementedError

    def _emit_libc_free(self) -> None:
        """In: rax = ptr. Calls free()."""
        raise NotImplementedError

    # String helpers -------------------------------------------------------
    def _emit_libc_strcmp(self) -> None:
        """In: rax, rbx = nul-terminated ptrs. Out: rax = signed cmp result."""
        raise NotImplementedError

    def _emit_libc_strdup(self) -> None:
        """In: rax = ptr. Out: rax = freshly-allocated copy."""
        raise NotImplementedError

    def _emit_libc_strlen(self) -> None:
        """In: rax = ptr. Out: rax = byte length (strlen)."""
        raise NotImplementedError

    def _emit_libc_strstr(self) -> None:
        """In: rax = haystack, rbx = needle. Out: rax = ptr (or NULL)."""
        raise NotImplementedError

    # Floats ---------------------------------------------------------------
    def _emit_call_libc_double_double(self, fn: str) -> None:
        """In: xmm0, xmm1 = args. Out: xmm0 = result. Calls `fn(double,double)`.
        (`fn` is used by overrides.)"""
        raise NotImplementedError

    # Exception machinery --------------------------------------------------
    def _emit_call_setjmp(self, buf_off: int) -> None:
        """Sets up a setjmp call against the buffer at [rbp+buf_off].
        (`buf_off` is used by overrides.)"""
        raise NotImplementedError
