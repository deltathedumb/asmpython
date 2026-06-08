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


# --- Base codegen -------------------------------------------------------------


class Codegen:
    """Common shape; subclasses provide the target-specific prologue,
    print implementations, _exit, and section directives."""

    # Each subclass sets these:
    section_text: str = ""
    section_data: str = ""
    section_rodata: str = ""
    label_main: str = ""  # public entry symbol (e.g. _start on Linux, main on Windows)

    def __init__(self, mod: A.Module, *, use_runtime_lib: bool = False) -> None:
        self.mod = mod
        # If True, skip emitting runtime bodies and assume libserpent_rt is linked.
        self.use_runtime_lib = use_runtime_lib
        self.lines: list[str] = []
        self.strings: list[tuple[str, str]] = []  # (label, bytes-literal)
        self.floats: list[tuple[str, float]] = []  # (label, value)
        self.label_counter = 0
        # FFI surface: { serpent_name: stdlib.Func } across all imports, used
        # for dispatching bare and module-attribute calls. Also any constants
        # imported by `from <mod> import <name>` for direct value substitution.
        self.ffi_funcs: dict = dict(mod.ffi_funcs)
        self.ffi_consts: dict = dict(mod.ffi_consts)
        self.imported_modules: dict = dict(mod.imported_modules)
        # Set of c_name symbols we'll need `extern` declarations for.
        self.ffi_externs: set[str] = set()
        for fn in self.ffi_funcs.values():
            self.ffi_externs.add(fn.c_name)
        for mod_bindings in self.imported_modules.values():
            for b in mod_bindings.values():
                if hasattr(b, "c_name"):
                    self.ffi_externs.add(b.c_name)
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
            )
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
        # RTTI: each user class gets a small integer id. Instances are tagged
        # with their id (a hidden `__class__` dict entry) at construction, and
        # isinstance walks the `__class_parents` table to honour inheritance.
        self.class_ids: dict[str, int] = {
            cls.name: i for i, cls in enumerate(mod.classes)
        }

    # ---- emit helpers -------------------------------------------------------

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

    def _collect_frame_bound(self, stmts: list, acc: set) -> None:
        """Collect names bound by a for-loop variable or tuple-unpack target
        anywhere in `stmts` (recursing into nested blocks). Those codegen paths
        store directly into a frame slot, so such names can't live in .bss."""
        for s in stmts:
            if isinstance(s, A.TupleAssign):
                acc.update(s.targets)
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
                for _bind, hbody in s.extra_handlers:
                    self._collect_frame_bound(hbody, acc)
                self._collect_frame_bound(s.else_body, acc)
                self._collect_frame_bound(s.finally_body, acc)

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
        """Add a string to .data, return (label, byte_length)."""
        label = f"str_{len(self.strings)}"
        # Encode to bytes once so length is accurate even for escapes / UTF-8.
        raw = s.encode("utf-8")
        # NASM needs the data as a sequence of byte values to be robust.
        body = ",".join(str(b) for b in raw) if raw else "0"
        self.strings.append((label, body))
        return label, len(raw)

    # ---- driver -------------------------------------------------------------

    def generate(self) -> str:
        self.emit(f"; serpent generated for target = {self.__class__.__name__}")
        self.emit("BITS 64")
        self.emit("default rel")
        before = len(self.lines)
        self.emit_externs()
        # Avoid duplicate `extern foo` declarations: the target subclass
        # already emits some.
        already = {
            line.strip().split()[-1]
            for line in self.lines[before:]
            if line.strip().startswith("extern")
        }
        for sym in sorted(self.ffi_externs):
            if sym not in already:
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
                )
                self.emit_function(mangled)
        self.emit_print_impls()
        self.emit_data_sections()
        return "\n".join(self.lines) + "\n"

    def generate_runtime_only(self) -> str:
        """Emit a freestanding `.asm` containing the serpent runtime helpers and
        nothing else. Used by the `serpent.runtime.build` step to produce
        `libserpent_rt_<target>.a`.

        The output declares every `_runtime_*` symbol (plus scratch buffers)
        as `global` so user programs can `extern` them at link time.
        """
        assert not self.use_runtime_lib, "Runtime build must emit bodies, not externs."
        self.emit(f"; serpent runtime library, target = {self.__class__.__name__}")
        self.emit("BITS 64")
        self.emit("default rel")
        # Externs we need from libc (printf, malloc, etc.)
        self.emit_externs()
        # Publish every runtime entry point + the scratch buffers user
        # programs reference.
        publish = sorted(self.RUNTIME_GLOBALS | {"itoa_str_buf", "input_buf"})
        for sym in publish:
            self.emit(f"global {sym}")
        self.emit_print_impls()
        return "\n".join(self.lines) + "\n"

    # Symbols the runtime library exposes (functions and globals).
    # Anything emitted by `emit_print_impls`, `emit_dict_runtime`,
    # `emit_string_runtime`, or `emit_exception_runtime` that user programs
    # call or load.
    RUNTIME_GLOBALS = {
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
        "_runtime_list_slice",
        # String runtime
        "_runtime_str_concat",
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
        "_runtime_str_upper",
        "_runtime_str_lower",
        "_runtime_str_strip",
        "_runtime_str_lstrip",
        "_runtime_str_rstrip",
        "_runtime_str_replace",
        "_runtime_str_split",
        "_runtime_str_join",
        # Exception runtime + globals
        "_runtime_setjmp",
        "_runtime_longjmp",
        "_runtime_raise",
        "_runtime_handler_top",
        "_runtime_exc_msg",
        # I/O + collection helpers
        "_runtime_input",
        "_runtime_list_append",
        "_runtime_list_pop",
    }

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
                # repr(float) round-trips to the exact bit pattern.
                self.emit(f"{label}: dq {repr(val)}")
        # bss: one zero-initialized 8-byte slot per module-level variable.
        # Module-level code (`main`) writes the real value at startup; every
        # function reads it through the symbol.
        if self.global_vars:
            self.emit("section .bss")
            for name in self.global_vars:
                self.emit(f"{self._global_label(name)}: resq 1")

    # ---- entry point: top-level statements ----------------------------------

    def emit_entry(self) -> None:
        """Emit the OS-specific entry symbol that runs module-level code."""
        # Build a synthetic "main" FuncInfo for top-level body.
        top = A.FuncDef(name=self.label_main, params=[], body=list(self.mod.body))
        # Reuse the function emitter, but with custom prologue/epilogue.
        info = self._collect_locals(top)
        self.funcs[self.label_main] = info
        self.label(self.label_main)
        self.emit_entry_prologue(info)
        for stmt in top.body:
            self.gen_stmt(stmt, info)
        self.emit_entry_epilogue(info)

    def emit_entry_prologue(self, info: FuncInfo) -> None:
        raise NotImplementedError

    def emit_entry_epilogue(self, info: FuncInfo) -> None:
        raise NotImplementedError

    # ---- regular function emission ------------------------------------------

    def emit_function(self, f: A.FuncDef) -> None:
        info = self._collect_locals(f)
        self.funcs[f.name] = info
        self.label(f.name)
        self.emit_func_prologue(info)
        self.emit_func_epilogue_label = self.fresh(f"ret_{f.name}")
        for stmt in f.body:
            self.gen_stmt(stmt, info)
        # Fallthrough = implicit return 0.
        self.emitf("xor rax, rax")
        self.label(self.emit_func_epilogue_label)
        self.emit_func_epilogue(info)

    def emit_func_prologue(self, info: FuncInfo) -> None:
        self.emitf("push rbp", "mov rbp, rsp")
        if info.frame_size:
            self.emitf(f"sub rsp, {info.frame_size}")
        # Spill incoming register args into their stack slots.
        for i, p in enumerate(info.params):
            off = info.locals_[p]
            if info.local_types.get(p) == "float":
                # Float arguments arrive in xmm registers, not the integer
                # ABI registers, and the SysV/Win64 counters differ. Not wired
                # up yet — fail loudly rather than spill the wrong register.
                raise NotImplementedError(
                    "float parameters are not supported yet"
                )
            reg = self._arg_reg(i)
            if reg is None:
                # Beyond ABI register count: argument is already on the stack
                # at [rbp + 16 + 8*(i-N)] (System V) / shadow rules differ on
                # Windows. We don't bother supporting >4/6 args right now.
                raise NotImplementedError("too many parameters")
            self.emitf(f"mov [rbp{off:+d}], {reg}")

    def emit_func_epilogue(self, info: FuncInfo) -> None:
        self.emitf("mov rsp, rbp", "pop rbp", "ret")

    def _arg_reg(self, i: int) -> Optional[str]:
        raise NotImplementedError

    def _param_type_from_annot(self, annot) -> Optional[str]:
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
        info = FuncInfo(name=f.name, params=list(f.params), defaults=list(f.defaults))
        # In module-level code (`main`), a write to a global name targets its
        # .bss slot, not a frame slot — so don't give those names a local.
        is_main = f.name == self.label_main
        # Each local (incl. params) gets an 8-byte slot at a negative RBP offset.
        offset = 0
        for i, p in enumerate(f.params):
            offset -= 8
            info.locals_[p] = offset
            # Param type, in priority order: explicit annotation, then the
            # default's type, then int. This decides the read/write register
            # class (int/pointer via rax vs float via xmm0).
            annot = f.param_types[i] if i < len(f.param_types) else None
            ty = self._param_type_from_annot(annot)
            if ty is None:
                ty = "int"
                if i < len(f.defaults) and f.defaults[i] is not None:
                    ty = A.expr_type(f.defaults[i])
            info.local_types[p] = ty

        # Walk the body for any name that becomes bound.
        def define(name: str, ty: str = "int") -> None:
            nonlocal offset
            if is_main and name in self.global_vars:
                return  # module global: lives in .bss, addressed by symbol
            if name not in info.locals_:
                offset -= 8
                info.locals_[name] = offset
                info.local_types[name] = ty
            elif info.local_types.get(name) == "int" and ty != "int":
                # Promote int to wider type if a later assignment reveals it.
                info.local_types[name] = ty

        def define_bytes(name: str, n_bytes: int) -> None:
            """Reserve an arbitrarily-sized slot (e.g. 200-byte jmp_buf).
            Rounds up to a multiple of 8 so subsequent 8-byte locals stay aligned."""
            nonlocal offset
            if name in info.locals_:
                return
            rounded = (n_bytes + 7) & ~7
            offset -= rounded
            info.locals_[name] = offset
            info.local_types[name] = "buf"

        def walk_expr(expr):
            # Pre-allocate a scratch slot per ListLit / DictLit so codegen
            # never has to extend the frame at emit-time.
            if isinstance(expr, A.ListLit):
                define(f"__listlit_{id(expr)}")
                for el in expr.elems:
                    walk_expr(el)
            elif isinstance(expr, A.Comprehension):
                # Result list header, source iterable, cached length, index,
                # and a scratch for each appended value; plus the loop var.
                define(f"__comp_res_{id(expr)}")
                define(f"__comp_iter_{id(expr)}")
                define(f"__comp_stop_{id(expr)}")
                define(f"__comp_idx_{id(expr)}")
                define(f"__comp_val_{id(expr)}")
                # Loop variable inherits the iterable's element kind.
                var_ty = "int"
                if A.expr_type(expr.iter) == "list":
                    if isinstance(expr.iter, A.Name):
                        var_ty = expr.iter.list_el_type
                    elif isinstance(expr.iter, A.ListLit):
                        var_ty = expr.iter.el_type
                    else:
                        var_ty = getattr(expr.iter, "list_el_type", "int")
                define(expr.var, var_ty)
                walk_expr(expr.iter)
                walk_expr(expr.elt)
                if expr.cond is not None:
                    walk_expr(expr.cond)
            elif isinstance(expr, A.TupleLit):
                # One scratch slot to park the header pointer across the two
                # mallocs (mirrors ListLit). Tuples reuse the list layout.
                define(f"__tuplelit_{id(expr)}")
                for el in expr.elems:
                    walk_expr(el)
            elif isinstance(expr, A.DictLit):
                define(f"__dictlit_{id(expr)}")
                define(f"__dictlit_key_{id(expr)}")
                for k in expr.keys:
                    walk_expr(k)
                for v in expr.values:
                    walk_expr(v)
            elif isinstance(expr, A.BinOp):
                # String concat / repeat needs a scratch slot to park the left
                # operand across the right-side evaluation (which may call).
                lt, rt = A.expr_type(expr.left), A.expr_type(expr.right)
                if "str" in (lt, rt):
                    define(f"__binstr_{id(expr)}")
                walk_expr(expr.left)
                walk_expr(expr.right)
            elif isinstance(expr, A.Compare):
                # Two str operands with ==/!= or in/not in -> runtime call
                # needs a scratch slot to park the lhs across the rhs eval.
                if len(expr.ops) == 1 and len(expr.operands) == 2:
                    op = expr.ops[0]
                    lt, rt = (
                        A.expr_type(expr.operands[0]),
                        A.expr_type(expr.operands[1]),
                    )
                    if op in ("==", "!=", "<", "<=", ">", ">=") and lt == "str" and rt == "str":
                        define(f"__strcmp_{id(expr)}")
                    elif op in ("in", "not in") and lt == "str" and rt == "str":
                        define(f"__strin_{id(expr)}")
                    elif op in ("in", "not in") and rt in ("list", "tuple"):
                        # Element type drives slot kind (float needs xmm-sized
                        # spill, but our locals are 8 bytes which matches).
                        # Tuples reuse the list layout, so the scan is identical.
                        define(f"__listin_{id(expr)}")
                    elif op in ("in", "not in") and rt == "dict":
                        define(f"__dictin_{id(expr)}")
                for o in expr.operands:
                    walk_expr(o)
            elif isinstance(expr, A.BoolOp):
                walk_expr(expr.left)
                walk_expr(expr.right)
            elif isinstance(expr, A.IfExp):
                walk_expr(expr.test)
                walk_expr(expr.body)
                walk_expr(expr.orelse)
            elif isinstance(expr, A.UnaryOp):
                walk_expr(expr.operand)
            elif isinstance(expr, A.Call):
                # FFI call needs one scratch slot per arg.
                if expr.func in self.ffi_funcs:
                    fn = self.ffi_funcs[expr.func]
                    for k in range(len(expr.args)):
                        define(
                            f"__ffi_arg_{id(fn)}_{k}",
                            "float" if fn.arg_types[k] == "float" else "int",
                        )
                # Constructor needs a slot to park the freshly-allocated
                # instance ptr across the __init__ call.
                if expr.func in self.mod.classes_sig:
                    define(f"__ctor_inst_{id(expr)}")
                # getattr(obj, "name", default) parks the default across the
                # object's evaluation (which may itself call).
                if expr.func == "getattr" and len(expr.args) == 3:
                    define(f"__getattr_def_{id(expr)}")
                # User function / constructor calls pass arguments through
                # frame slots (alignment-safe). One slot per positional arg.
                if expr.func in self.funcs or expr.func in self.mod.classes_sig:
                    for k in range(len(expr.args)):
                        define(f"__callarg_{id(expr)}_{k}")
                for a in expr.args:
                    walk_expr(a)
            elif isinstance(expr, A.MethodCall):
                # math.sqrt(x) — same FFI scratch reservation.
                if (
                    isinstance(expr.obj, A.Name)
                    and expr.obj.name in self.imported_modules
                ):
                    bindings = self.imported_modules[expr.obj.name]
                    b = bindings.get(expr.method)
                    if b is not None and hasattr(b, "arg_types"):
                        for k in range(len(expr.args)):
                            define(
                                f"__ffi_arg_{id(b)}_{k}",
                                "float" if b.arg_types[k] == "float" else "int",
                            )
                else:
                    # String methods spill the object pointer (and one extra
                    # for replace's 2-arg signature) across argument eval.
                    if A.expr_type(expr.obj) == "str":
                        define(f"__strm_obj_{id(expr)}")
                        if len(expr.args) >= 2:
                            define(f"__strm_a1_{id(expr)}")
                    # User-class (and super) method calls pass args through
                    # frame slots; instance calls also park the receiver.
                    obj_t = A.expr_type(expr.obj)
                    if obj_t.startswith("instance:"):
                        define(f"__callself_{id(expr)}")
                    if obj_t.startswith("instance:") or obj_t.startswith("super:"):
                        for k in range(len(expr.args)):
                            define(f"__callarg_{id(expr)}_{k}")
                    walk_expr(expr.obj)
                for a in expr.args:
                    walk_expr(a)
            elif isinstance(expr, A.Subscript):
                if isinstance(expr.index, A.Slice):
                    # slice needs scratch slots for obj/start (always) plus
                    # stop/step when the step-aware path is used.
                    define(f"__strsl_obj_{id(expr)}")
                    define(f"__strsl_start_{id(expr)}")
                    if expr.index.step is not None:
                        define(f"__strsl_stop_{id(expr)}")
                        define(f"__strsl_step_{id(expr)}")
                    # List slicing dispatches through a different helper; the
                    # codegen path uses its own pair of slots.
                    if A.expr_type(expr.obj) == "list":
                        define(f"__lstsl_obj_{id(expr)}")
                        define(f"__lstsl_start_{id(expr)}")
                    walk_expr(expr.obj)
                    if expr.index.start is not None:
                        walk_expr(expr.index.start)
                    if expr.index.stop is not None:
                        walk_expr(expr.index.stop)
                    if expr.index.step is not None:
                        walk_expr(expr.index.step)
                elif A.expr_type(expr.obj) == "str":
                    define(f"__stridx_{id(expr)}")
                    walk_expr(expr.obj)
                    walk_expr(expr.index)
                else:
                    walk_expr(expr.obj)
                    walk_expr(expr.index)
            elif isinstance(expr, A.Attr):
                # Module attr access doesn't need to evaluate the obj.
                if (
                    isinstance(expr.obj, A.Name)
                    and expr.obj.name in self.imported_modules
                ):
                    pass
                else:
                    walk_expr(expr.obj)
            elif isinstance(expr, A.FString):
                define(f"__fstr_acc_{id(expr)}")
                for s in expr.segments:
                    walk_expr(s)

        def walk(stmts):
            for s in stmts:
                if isinstance(s, A.Assign):
                    define(s.target, A.expr_type(s.value))
                    walk_expr(s.value)
                elif isinstance(s, A.AugAssign):
                    define(s.target, A.expr_type(s.value))
                    walk_expr(s.value)
                elif isinstance(s, A.TupleAssign):
                    if len(s.values) == 1 and A.expr_type(s.values[0]) == "tuple":
                        # Unpack form: park the tuple ptr in one slot, then
                        # type each target from the tuple's element kinds.
                        define(f"__tupunpack_{id(s)}", "int")
                        walk_expr(s.values[0])
                        ets = A.tuple_element_types(s.values[0])
                        for i, t in enumerate(s.targets):
                            define(t, ets[i] if i < len(ets) else "int")
                    else:
                        for i, v in enumerate(s.values):
                            walk_expr(v)
                            define(f"__tup_tmp_{id(s)}_{i}", A.expr_type(v))
                        for t, v in zip(s.targets, s.values):
                            define(t, A.expr_type(v))
                elif isinstance(s, A.For) and self._for_zip_spec(s) is not None:
                    # for a, b in zip(A, B) / for i, (a, b) in enumerate(zip):
                    # two iterables walked in lockstep. Reserve the per-buffer
                    # pointers, the bound, the counter, and the target slots.
                    zidx, za, zb, zae, zbe = self._for_zip_spec(s)
                    if zidx is not None:
                        define(zidx, "int")
                    define(za, self._list_expr_el_kind(zae))
                    define(zb, self._list_expr_el_kind(zbe))
                    define(f"__zip_a_{id(s)}", "int")
                    define(f"__zip_b_{id(s)}", "int")
                    define(f"__zip_stop_{id(s)}", "int")
                    define(f"__zip_i_{id(s)}", "int")
                    walk_expr(zae)
                    walk_expr(zbe)
                    walk(s.body)
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
                    define(s.targets[0], "int")
                    define(s.targets[1], el_ty)
                    define(f"__for_stop_{id(s)}", "int")
                    define(f"__for_step_{id(s)}", "int")
                    define(f"__for_iter_{id(s)}", "int")
                    walk_expr(inner)
                    walk(s.body)
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
                    elif s.iter is not None and A.expr_type(s.iter) == "str":
                        var_ty = "str"
                    define(s.var, var_ty)
                    define(f"__for_stop_{id(s)}", "int")
                    define(f"__for_step_{id(s)}", "int")
                    if s.iter is not None:
                        define(f"__for_iter_{id(s)}", "int")  # ptr; treat as int slot
                        walk_expr(s.iter)
                    else:
                        for a in s.range_args:
                            walk_expr(a)
                    walk(s.body)
                elif isinstance(s, A.If):
                    walk_expr(s.test)
                    walk(s.then)
                    walk(s.orelse)
                elif isinstance(s, A.While):
                    walk_expr(s.test)
                    walk(s.body)
                elif isinstance(s, A.Return):
                    if s.value is not None:
                        walk_expr(s.value)
                elif isinstance(s, A.ExprStmt):
                    walk_expr(s.expr)
                elif isinstance(s, A.IndexAssign):
                    walk_expr(s.target.obj)
                    walk_expr(s.target.index)
                    walk_expr(s.value)
                elif isinstance(s, A.AttrAssign):
                    walk_expr(s.obj)
                    walk_expr(s.value)
                elif isinstance(s, A.Try):
                    # jmp_buf on x86-64 libc is typically <= 200 bytes.
                    define_bytes(f"__try_buf_{id(s)}", 200)
                    # Saved previous handler ptr to restore on normal exit.
                    define(f"__try_parent_{id(s)}", "int")
                    if s.bind_name is not None:
                        define(s.bind_name, "str")
                    walk(s.body)
                    walk(s.handler)
                elif isinstance(s, A.Raise):
                    walk_expr(s.value)

        walk(f.body)
        # Frame size must be 16-byte aligned for ABI compliance before calls.
        frame = -offset
        if frame % 16:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        return info

    # ---- statement codegen --------------------------------------------------

    def gen_stmt(self, stmt, info: FuncInfo) -> None:
        if isinstance(stmt, A.Pass):
            return
        if isinstance(stmt, (A.Import, A.FromImport)):
            # Imports are resolved statically by sema; nothing runtime-side.
            return
        if isinstance(stmt, A.Assign):
            ty = self._var_type(stmt.target, info)
            value_t = A.expr_type(stmt.value)
            mem = self._var_mem(stmt.target, info)
            if ty == "float":
                # Slot expects a float; promote int RHS to float.
                self._gen_expr_as_float(stmt.value, info, value_t)
                self.emitf(f"movsd {mem}, xmm0")
            else:
                self.gen_expr(stmt.value, info)
                self.emitf(f"mov {mem}, rax")
            return
        if isinstance(stmt, A.TupleAssign):
            # Unpack form: `a, b = <tuple>`. Evaluate the tuple once, park its
            # header ptr, then copy each slot out into the matching target.
            if len(stmt.values) == 1 and A.expr_type(stmt.values[0]) == "tuple":
                ptr_slot = info.locals_[f"__tupunpack_{id(stmt)}"]
                ets = A.tuple_element_types(stmt.values[0])
                self.gen_expr(stmt.values[0], info)  # rax = tuple header ptr
                self.emitf(f"mov [rbp{ptr_slot:+d}], rax")
                for i, target in enumerate(stmt.targets):
                    off = info.locals_[target]
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
                off = info.locals_[target]
                self.emitf(f"mov rax, [rbp{tmp:+d}]", f"mov [rbp{off:+d}], rax")
            return
        if isinstance(stmt, A.AugAssign):
            mem = self._var_mem(stmt.target, info)
            ty = self._var_type(stmt.target, info)
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
            obj_t = A.expr_type(stmt.target.obj)
            if obj_t == "dict":
                # Stack order: push key, push value, eval header, then call.
                self.gen_expr(stmt.target.index, info)
                self.emitf("push rax")
                self.gen_expr(stmt.value, info)
                self.emitf("push rax")
                self.gen_expr(stmt.target.obj, info)  # rax = header
                self.emitf(
                    "pop rcx",  # rcx = value
                    "pop rbx",  # rbx = key
                    "call _runtime_dict_set",
                )
                return
            self.gen_expr(stmt.target.index, info)
            self.emitf("push rax")
            self.gen_expr(stmt.value, info)
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
            # obj.name = value  ->  dict_set(obj, "name", value)
            key_label, _ = self.intern_string(stmt.name)
            self.gen_expr(stmt.value, info)
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
            # Evaluate message into rax, then call _runtime_raise.
            self.gen_expr(stmt.value, info)
            self.emitf("call _runtime_raise")
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
        """Recognize `for a, b in zip(A, B)` and
        `for i, (a, b) in enumerate(zip(A, B))`. Returns
        (idx_name_or_None, a_name, b_name, a_expr, b_expr) or None. Mirrors the
        analyzer's detection so codegen and sema agree on the loop shape."""
        it = s.iter
        if it is None or not isinstance(it, A.Call):
            return None
        if it.func == "zip":
            if (
                len(it.args) == 2
                and len(s.targets) == 2
                and isinstance(s.targets[0], str)
                and isinstance(s.targets[1], str)
            ):
                return (None, s.targets[0], s.targets[1], it.args[0], it.args[1])
            return None
        if (
            it.func == "enumerate"
            and len(it.args) == 1
            and isinstance(it.args[0], A.Call)
            and it.args[0].func == "zip"
        ):
            z = it.args[0]
            if (
                len(z.args) == 2
                and len(s.targets) == 2
                and isinstance(s.targets[0], str)
                and isinstance(s.targets[1], list)
                and len(s.targets[1]) == 2
            ):
                return (
                    s.targets[0],
                    s.targets[1][0],
                    s.targets[1][1],
                    z.args[0],
                    z.args[1],
                )
            return None
        return None

    def _gen_for(self, stmt: A.For, info: FuncInfo) -> None:
        zspec = self._for_zip_spec(stmt)
        if zspec is not None:
            self._gen_for_zip(stmt, info, zspec)
            return
        if (
            stmt.iter is not None
            and isinstance(stmt.iter, A.Call)
            and stmt.iter.func == "enumerate"
        ):
            self._gen_for_enumerate(stmt, info)
            return
        if stmt.iter is not None:
            iter_t = A.expr_type(stmt.iter)
            if iter_t == "dict":
                self._gen_for_dict(stmt, info)
                return
            if iter_t == "str":
                self._gen_for_str(stmt, info)
                return
            self._gen_for_list(stmt, info)
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

        self.loop_labels.append((cont, end))
        self.label(top)
        # Choose comparison based on sign of step (computed at runtime so the
        # step can be dynamic without needing constant folding).
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]",
            "test rax, rax",
            f"jg {pos_branch}",
            # step <= 0:  if var <= stop:  goto end
            f"mov rax, [rbp{var_off:+d}]",
            f"mov rbx, [rbp{stop_off:+d}]",
            "cmp rax, rbx",
            f"jle {end}",
            f"jmp {body_lbl}",
        )
        self.label(pos_branch)
        # step > 0:  if var >= stop:  goto end
        self.emitf(
            f"mov rax, [rbp{var_off:+d}]",
            f"mov rbx, [rbp{stop_off:+d}]",
            "cmp rax, rbx",
            f"jge {end}",
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
        self.label(end)
        self.loop_labels.pop()

    def _gen_try(self, stmt: A.Try, info: FuncInfo) -> None:
        """Compile  try: body  except [as e]: handler  via setjmp/longjmp.

        We push a fresh jmp_buf onto the global handler chain, setjmp into
        it, and run the body. A `raise` deep in any callee invokes longjmp,
        which transfers control to the post-setjmp return path with eax != 0;
        we then dispatch to the handler block. On normal body exit we pop
        the handler before continuing.
        """
        if stmt.extra_handlers or stmt.else_body or stmt.finally_body:
            # Multiple except clauses need exception-class RTTI to dispatch;
            # else/finally need the full unwinding machinery. The parser and
            # sema accept these shapes (so the front-end self-host gauntlet can
            # measure progress), but codegen only implements the single
            # catch-all handler for now. Fail loudly rather than miscompile.
            raise NotImplementedError(
                "try with multiple 'except' clauses, 'else', or 'finally' is "
                "not supported by codegen yet"
            )
        buf_off = info.locals_[f"__try_buf_{id(stmt)}"]
        parent_off = info.locals_[f"__try_parent_{id(stmt)}"]
        handler_lbl = self.fresh("try_handler")
        end_lbl = self.fresh("try_end")

        # parent_handler = _runtime_handler_top
        self.emitf(
            "mov rax, [rel _runtime_handler_top]", f"mov [rbp{parent_off:+d}], rax"
        )
        # _runtime_handler_top = &buf
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "mov [rel _runtime_handler_top], rax")
        # setjmp(buf). Returns 0 on direct call, nonzero after longjmp.
        self._emit_call_setjmp(buf_off)
        self.emitf("test eax, eax", f"jnz {handler_lbl}")

        # ---- body ----
        for s in stmt.body:
            self.gen_stmt(s, info)
        # Normal completion: restore parent handler and skip handler block.
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]",
            "mov [rel _runtime_handler_top], rax",
            f"jmp {end_lbl}",
        )

        # ---- handler ----
        self.label(handler_lbl)
        # Restore parent handler too (we caught it).
        self.emitf(
            f"mov rax, [rbp{parent_off:+d}]", "mov [rel _runtime_handler_top], rax"
        )
        if stmt.bind_name is not None:
            exc_off = info.locals_[stmt.bind_name]
            self.emitf("mov rax, [rel _runtime_exc_msg]", f"mov [rbp{exc_off:+d}], rax")
        for s in stmt.handler:
            self.gen_stmt(s, info)
        self.label(end_lbl)

    def _gen_for_dict(self, stmt: A.For, info: FuncInfo) -> None:
        # Linear sweep over the slot buffer: skip empty (key==0) and
        # tombstoned (key==1) slots, otherwise bind var = key_ptr.
        var_off = info.locals_[stmt.var]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]

        self.gen_expr(stmt.iter, info)  # rax = header
        self.emitf(
            f"mov [rbp{iter_off:+d}], rax",
            f"mov rbx, [rax+{self.DICT_CAP_OFF}]",
            f"mov [rbp{stop_off:+d}], rbx",
            f"mov qword [rbp{step_off:+d}], 0",
        )  # i = 0

        top = self.fresh("for_dict")
        cont = self.fresh("for_dict_cont")
        end = self.fresh("endfor_dict")
        body_lbl = self.fresh("for_dict_body")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # Load slot: ptr = buf + i * 16; key = [ptr]
        self.emitf(
            f"mov rbx, [rbp{iter_off:+d}]",
            f"mov rbx, [rbx+{self.DICT_BUF_OFF}]",
            f"mov rcx, [rbp{step_off:+d}]",
            "shl rcx, 4",  # *16
            "add rbx, rcx",
            "mov rax, [rbx]",  # key ptr
            "cmp rax, 1",  # tombstone or empty?
            f"jbe {cont}",  # 0 or 1 -> skip
            f"mov [rbp{var_off:+d}], rax",
            f"jmp {body_lbl}",
        )
        self.label(body_lbl)
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{step_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    def _gen_for_enumerate(self, stmt: A.For, info: FuncInfo) -> None:
        # for i, x in enumerate(inner):  i = index, x = inner[i].
        inner = stmt.iter.args[0]
        if A.expr_type(inner) != "list":
            raise NotImplementedError(
                "enumerate() over a non-list iterable is not supported yet"
            )
        idx_off = info.locals_[stmt.targets[0]]
        el_off = info.locals_[stmt.targets[1]]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]  # index counter

        self.gen_expr(inner, info)
        self.emitf(
            f"mov [rbp{iter_off:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop_off:+d}], rbx",
            f"mov qword [rbp{step_off:+d}], 0",
        )
        top = self.fresh("for_enum")
        cont = self.fresh("for_enum_cont")
        end = self.fresh("endfor_enum")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # index var = counter; element var = buffer[counter] (reload buffer).
        self.emitf(
            f"mov rax, [rbp{step_off:+d}]",
            f"mov [rbp{idx_off:+d}], rax",
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
        """`for a, b in zip(A, B)` / `for i, (a, b) in enumerate(zip(A, B))`.

        Walks both list buffers in lockstep, stopping at the shorter (Python's
        zip semantics) via min(len A, len B). Each iteration binds a = A[i],
        b = B[i], and the optional index. Tuples reuse the list layout, so the
        same buffer arithmetic works for either A/B being a list or a tuple.
        Values are copied as raw 8-byte slots, which is correct for any element
        kind (int/str/ptr/float bits)."""
        idx_name, a_name, b_name, a_expr, b_expr = zspec
        iter_a = info.locals_[f"__zip_a_{id(stmt)}"]
        iter_b = info.locals_[f"__zip_b_{id(stmt)}"]
        stop_off = info.locals_[f"__zip_stop_{id(stmt)}"]
        i_off = info.locals_[f"__zip_i_{id(stmt)}"]
        a_off = info.locals_[a_name]
        b_off = info.locals_[b_name]

        # Evaluate both iterables (header pointers), cache them and the loop
        # bound = min(lenA, lenB).
        self.gen_expr(a_expr, info)
        self.emitf(f"mov [rbp{iter_a:+d}], rax")
        self.gen_expr(b_expr, info)
        self.emitf(f"mov [rbp{iter_b:+d}], rax")
        self.emitf(
            f"mov rax, [rbp{iter_a:+d}]",
            f"mov rax, [rax+{self.LIST_LEN_OFF}]",
            f"mov rbx, [rbp{iter_b:+d}]",
            f"mov rbx, [rbx+{self.LIST_LEN_OFF}]",
            "cmp rax, rbx",
            "cmovg rax, rbx",  # rax = min(lenA, lenB)
            f"mov [rbp{stop_off:+d}], rax",
            f"mov qword [rbp{i_off:+d}], 0",
        )

        top = self.fresh("for_zip")
        cont = self.fresh("for_zip_cont")
        end = self.fresh("endfor_zip")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{i_off:+d}]", f"cmp rax, [rbp{stop_off:+d}]", f"jge {end}"
        )
        # a = A.buf[i]; b = B.buf[i] (reload buffers each step in case a body
        # append reallocated one of them).
        self.emitf(
            f"mov rbx, [rbp{iter_a:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{i_off:+d}]",
            "mov rax, [rbx+rcx*8]",
            f"mov [rbp{a_off:+d}], rax",
            f"mov rbx, [rbp{iter_b:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            "mov rax, [rbx+rcx*8]",
            f"mov [rbp{b_off:+d}], rax",
        )
        if idx_name is not None:
            idx_off = info.locals_[idx_name]
            self.emitf(
                f"mov rax, [rbp{i_off:+d}]", f"mov [rbp{idx_off:+d}], rax"
            )
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{i_off:+d}]", f"jmp {top}")
        self.label(end)
        self.loop_labels.pop()

    def _gen_for_list(self, stmt: A.For, info: FuncInfo) -> None:
        # Lower as:  i = 0; iter = <list>; while i < iter.length: var = iter[i]; body; i += 1
        # We reuse stop_off (length cache) and iter_off (list pointer).
        var_off = info.locals_[stmt.var]
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
        # reallocated by append calls inside the loop).
        self.emitf(
            f"mov rbx, [rbp{iter_off:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
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
            if expr.name not in info.locals_ and expr.name not in self.global_vars:
                raise NameError(f"undefined variable {expr.name}")
            mem = self._var_mem(expr.name, info)
            ty = self._var_type(expr.name, info)
            if ty == "float":
                self.emitf(f"movsd xmm0, {mem}")
            else:
                self.emitf(f"mov rax, {mem}")
            return
        if isinstance(expr, A.UnaryOp):
            operand_t = A.expr_type(expr.operand)
            if operand_t == "float" and expr.op == "-":
                self.gen_expr(expr.operand, info)
                # XOR the sign bit. Easiest: subtract from 0.0.
                zero_lbl = self.intern_float(0.0)
                self.emitf(
                    f"movsd xmm1, [{zero_lbl}]", "subsd xmm1, xmm0", "movsd xmm0, xmm1"
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
            if expr.op == "-":
                self.emitf("neg rax")
            elif expr.op == "~":
                self.emitf("not rax")
            elif expr.op == "not":
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
        if isinstance(expr, A.FString):
            self._gen_fstring(expr, info)
            return
        raise NotImplementedError(f"expr {expr}")

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

        def emit_segment_as_str(seg):
            t = A.expr_type(seg)
            self.gen_expr(seg, info)
            if t == "int":
                self._emit_int_to_str()
            elif t == "float":
                self._emit_float_to_str()
            # "str" stays as-is

        # First segment seeds the accumulator.
        emit_segment_as_str(e.segments[0])
        self.emitf(f"mov [rbp{acc_slot:+d}], rax")
        # Each subsequent segment: convert -> concat with accumulator.
        for seg in e.segments[1:]:
            emit_segment_as_str(seg)
            self.emitf(
                "mov rbx, rax",
                f"mov rax, [rbp{acc_slot:+d}]",
                "call _runtime_str_concat",
                f"mov [rbp{acc_slot:+d}], rax",
            )
        self.emitf(f"mov rax, [rbp{acc_slot:+d}]")

    def _gen_attr(self, e: A.Attr, info: FuncInfo) -> None:
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
        obj_t = A.expr_type(e.obj)
        if obj_t.startswith("instance:"):
            key_label, _ = self.intern_string(e.name)
            self.gen_expr(e.obj, info)  # rax = instance dict
            self.emitf(
                f"lea rbx, [{key_label}]",
                "xor rcx, rcx",  # default = 0
                "call _runtime_dict_get_default",
            )
            return
        raise NotImplementedError(f"attr {e.name!r} on {obj_t}")

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
            # by serpent/runtime/build.py and gcc will resolve these at link.
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
                "_runtime_list_slice",
            ):
                self.emit(f"extern {sym}")
            return
        self.emit("section .text")

        # ---- _runtime_zalloc: malloc rbx bytes, zero-fill, return rax.
        self.label("_runtime_zalloc")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
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
            f"inc qword [r9+{self.DICT_LEN_OFF}]",
            "leave",
            "ret",
        )

        # ---- _runtime_dict_get: panic if missing.
        # rax = header, rbx = key -> rax = value
        self.label("_runtime_dict_get")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf(
            "test rax, rax",
            "jnz ._dg_found",
            # Missing key -> abort. Print a friendly message and exit.
            "lea rax, [_runtime_dict_key_error_msg]",
        )
        self._emit_panic_message()
        self.label("._dg_found")
        self.emitf("mov rax, [rax+8]", "leave", "ret")

        # ---- _runtime_dict_get_default
        # rax = header, rbx = key, rcx = default -> rax = value or default
        self.label("_runtime_dict_get_default")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
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
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf("test rax, rax", "setne al", "movzx rax, al", "leave", "ret")

        # ---- _runtime_dict_grow
        # rax = header. Doubles capacity, rehashes all live entries.
        # Strategy: snapshot the old slot buffer, allocate a bigger one,
        # then walk the old slots and call dict_set for each live one.
        # Locals span [rbp-8..rbp-48]; reserve 80 = 48 locals + 32 shadow.
        self.label("_runtime_dict_grow")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self.emitf("mov [rbp-8], rax")  # header
        # old_cap, old_buf
        self.emitf(
            f"mov rcx, [rax+{self.DICT_CAP_OFF}]",
            "mov [rbp-16], rcx",
            f"mov rcx, [rax+{self.DICT_BUF_OFF}]",
            "mov [rbp-24], rcx",
        )
        # new_cap = old_cap * 2
        self.emitf("mov rax, [rbp-16]", "shl rax, 1", "mov [rbp-32], rax")  # new_cap
        # new_buf = zalloc(new_cap * 16)
        self.emitf("mov rbx, rax", "shl rbx, 4", "call _runtime_zalloc")
        self.emitf("mov [rbp-40], rax")  # new_buf
        # Reset header: cap = new_cap; len = 0; tomb = 0; buf = new_buf.
        self.emitf(
            "mov r8, [rbp-8]",
            "mov r9, [rbp-32]",
            f"mov [r8+{self.DICT_CAP_OFF}], r9",
            f"mov qword [r8+{self.DICT_LEN_OFF}], 0",
            f"mov qword [r8+{self.DICT_TOMB_OFF}], 0",
            "mov r9, [rbp-40]",
            f"mov [r8+{self.DICT_BUF_OFF}], r9",
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
        # Free the old buffer.
        self.emitf("mov rax, [rbp-24]")
        self._emit_libc_free()
        self.emitf("leave", "ret")

        self._emit_dict_keys_or_values_helper("_runtime_dict_keys", value_field=False)
        self._emit_dict_keys_or_values_helper("_runtime_dict_values", value_field=True)
        self._emit_list_slice_helper()

        # Error message.
        self.emit("section .rodata")
        self.emit('_runtime_dict_key_error_msg: db "KeyError: key not in dict",10,0')

    def _emit_dict_keys_or_values_helper(self, name: str, *, value_field: bool) -> None:
        """Generate `_runtime_dict_keys` or `_runtime_dict_values`.

        In:  rax = dict header.
        Out: rax = newly-allocated list header.
             - keys: list[str] of live key pointers.
             - values: list[int] of value field of each live slot.
        Live = key_ptr > 1 (0 = empty, 1 = tombstone).

        Locals span [rbp-8..rbp-40]; reserve 80 = 40 locals + 32 shadow so
        malloc's shadow store can't clobber them.
        """
        self.label(name)
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        # [rbp-8]  = dict header (input)
        # [rbp-16] = list header (output)
        # [rbp-24] = list buffer ptr (also stored into header)
        # [rbp-32] = i (slot index)
        # [rbp-40] = w (write index into list)
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
        # Walk dict slots: for i in 0..dict.cap, if key > 1 copy out.
        self.emitf(
            "mov qword [rbp-32], 0",  # i = 0
            "mov qword [rbp-40], 0",  # w = 0
        )
        loop = self.fresh("dkv_loop")
        skip = self.fresh("dkv_skip")
        done = self.fresh("dkv_done")
        self.label(loop)
        self.emitf(
            "mov rax, [rbp-8]",  # dict header
            f"mov rbx, [rax+{self.DICT_CAP_OFF}]",
            "mov rcx, [rbp-32]",
            "cmp rcx, rbx",
            f"jge {done}",
            # slot = dict.buf + i*16
            f"mov rdx, [rax+{self.DICT_BUF_OFF}]",
            "mov r8, rcx",
            "shl r8, 4",
            "add rdx, r8",  # rdx = slot ptr
            "mov r9, [rdx]",  # r9 = key
            "cmp r9, 1",
            f"jbe {skip}",  # 0 or 1 -> empty/tombstone
            # live: write the requested field into list_buf[w*8]
            "mov r10, [rbp-24]",  # r10 = list buf
            "mov r11, [rbp-40]",
            "shl r11, 3",
            "add r10, r11",
        )
        if value_field:
            self.emitf("mov rax, [rdx+8]", "mov [r10], rax")
        else:
            self.emitf("mov [r10], r9")
        self.emitf("inc qword [rbp-40]")  # w++
        self.label(skip)
        self.emitf("inc qword [rbp-32]", f"jmp {loop}")
        self.label(done)
        # Return the list header.
        self.emitf("mov rax, [rbp-16]", "leave", "ret")

    def _emit_list_slice_helper(self) -> None:
        """`_runtime_list_slice`: `xs[start:stop]` -> new list (no step yet).

        In:  rax = src list header, rbx = start (or INT64_MIN sentinel),
             rcx = stop (or INT64_MAX sentinel).
        Out: rax = newly-allocated list header.

        Element type is irrelevant at runtime — slots are 8 bytes regardless.
        Locals span [rbp-8..rbp-72]; reserve 80 = 48 locals + 32 shadow.
        """
        INT64_MIN = "0x8000000000000000"
        INT64_MAX = "0x7fffffffffffffff"

        self.label("_runtime_list_slice")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
        self.emitf(
            "mov [rbp-8], rax",   # src header
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
        self.emitf("mov rax, [rbp-48]", "sub rax, [rbp-40]", f"jg {nle}", "xor rax, rax")
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
            "mov [rbp-8], rax",   # s
            "mov [rbp-16], rbx",  # start (raw)
            "mov [rbp-24], rcx",  # stop  (raw)
            "mov [rbp-32], r8",   # step
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
        self.emitf("mov rax, [rbp-32]", "test rax, rax", f"jg {step_pos}", f"jmp {step_neg}")

        self.label(step_pos)
        # start (missing sentinel -> 0; else normalize + clamp to [0, len])
        self.emitf(f"mov rax, [rbp-16]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
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
        self.emitf(f"mov rax, [rbp-24]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
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
        self.emitf(f"mov rax, [rbp-16]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
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
        self.emitf(f"mov rax, [rbp-24]", f"mov rbx, {INT64_MIN}", "cmp rax, rbx")
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
                "_runtime_str_upper",
                "_runtime_str_lower",
                "_runtime_str_strip",
                "_runtime_str_lstrip",
                "_runtime_str_rstrip",
                "_runtime_str_replace",
                "_runtime_str_split",
                "_runtime_str_join",
            ):
                self.emit(f"extern {sym}")
            return
        self.emit("section .text")

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
        self.label("_runtime_str_eq")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
        self._emit_libc_strcmp()
        self.emitf("test rax, rax", "sete al", "movzx rax, al", "leave", "ret")

        # ---- _runtime_str_cmp ------------------------------------------------
        # rax = a, rbx = b -> rax = -1/0/+1 (signed compare result).
        self.label("_runtime_str_cmp")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
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
        self.emitf("lea rax, [rel _runtime_str_oob_msg]", "call _runtime_raise")
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
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
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

        # ---- _runtime_str_upper ----------------------------------------------
        # rax = s -> rax = newly-allocated upper-case copy. ASCII only.
        self.label("_runtime_str_upper")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
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
        self.label("_runtime_str_lower")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48", "mov [rbp-8], rax")
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
            "sub rsp, 32",
            "call _runtime_str_rstrip",
            "call _runtime_str_lstrip",
            "leave",
            "ret",
        )

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
        self._emit_str_join_helper()

        self.emit("section .rodata")
        self.emit('_runtime_str_oob_msg: db "string index out of range",0')

    def _emit_str_split_helper(self) -> None:
        """`_runtime_str_split`: `s.split(sep)` -> list[str].

        In:  rax = s, rbx = sep. Empty sep falls back to wrapping s in a
             single-element list (CPython raises ValueError; we degenerate
             gracefully).
        Out: rax = list header.

        Strategy: pre-count occurrences via _runtime_str_count, allocate a
        list of exactly n+1 slots, then re-walk via strstr copying each
        segment into a fresh nul-terminated allocation.

        Locals span [rbp-8..rbp-96]; reserve 128 = 96 + 32 shadow.
        """
        self.label("_runtime_str_split")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 128")
        self.emitf(
            "mov [rbp-8], rax",   # s
            "mov [rbp-16], rbx",  # sep
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
            "mov [rbp-8], rax",   # sep
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
        self.emitf("dec rcx", "mov rax, [rbp-32]", "imul rax, rcx", f"jmp {sep_zero}_done")
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
            ):
                self.emit(f"extern {sym}")
            return
        # BSS globals — zero-initialized, 8 bytes each.
        self.emit("section .bss")
        self.emit("_runtime_handler_top: resq 1")
        self.emit("_runtime_exc_msg:     resq 1")

        self.emit("section .rodata")
        self.emit('_runtime_unhandled_prefix: db "Unhandled exception: ",0')

        self.emit("section .text")

        # ---- _runtime_setjmp -------------------------------------------------
        # Serpent's internal calling convention for runtime helpers: rax = primary
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
        # rax = exception message (string ptr). Stash it in the global.
        self.emitf("mov [rel _runtime_exc_msg], rax")
        # If no handler installed, print and exit.
        self.emitf(
            "mov rax, [rel _runtime_handler_top]", "test rax, rax", "jnz ._rr_jump"
        )
        # Unhandled path.
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 32",
            "lea rax, [rel _runtime_unhandled_prefix]",
        )
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

    def _emit_exit_one(self) -> None:
        raise NotImplementedError

    def _gen_const_load(self, c) -> None:
        if c.ty == "float":
            label = self.intern_float(c.value)
            self.emitf(f"movsd xmm0, [{label}]")
        elif c.ty == "int":
            self.emitf(f"mov rax, {c.value}")
        elif c.ty == "str":
            label, _ = self.intern_string(c.value)
            self.emitf(f"lea rax, [{label}]")
        else:
            raise NotImplementedError(f"const type {c.ty!r}")

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
    # Header (32 bytes, stable):
    #   [0..8)   capacity (number of slots; always a power of 2)
    #   [8..16)  length (live entries)
    #   [16..24) tombstones (deleted slots not yet reclaimed)
    #   [24..32) slots_ptr (heap buffer of slots)
    # Slot (16 bytes each):
    #   [0..8)   key_ptr  (0 = empty, 1 = tombstone, otherwise nul-term string)
    #   [8..16)  value
    DICT_CAP_OFF = 0
    DICT_LEN_OFF = 8
    DICT_TOMB_OFF = 16
    DICT_BUF_OFF = 24
    DICT_HEADER = 32
    DICT_SLOT_SIZE = 16

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
        if A.expr_type(e.iter) != "list":
            raise NotImplementedError(
                "comprehension iterable must be a list for now"
            )
        res = info.locals_[f"__comp_res_{id(e)}"]
        it = info.locals_[f"__comp_iter_{id(e)}"]
        stop = info.locals_[f"__comp_stop_{id(e)}"]
        idx = info.locals_[f"__comp_idx_{id(e)}"]
        val = info.locals_[f"__comp_val_{id(e)}"]
        var = info.locals_[e.var]

        # result = empty list (cap 4)
        cap = 4
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{res:+d}], rax",
        )
        self._emit_malloc(cap * 8)
        self.emitf(
            f"mov rbx, [rbp{res:+d}]", f"mov [rbx+{self.LIST_BUF_OFF}], rax"
        )

        # Iterate the source list (reloading the buffer each step since append
        # below may have grown a different list — here the source isn't grown,
        # but appends to `res` are independent).
        self.gen_expr(e.iter, info)
        self.emitf(
            f"mov [rbp{it:+d}], rax",
            f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
            f"mov [rbp{stop:+d}], rbx",
            f"mov qword [rbp{idx:+d}], 0",
        )
        top = self.fresh("comp")
        end = self.fresh("endcomp")
        self.label(top)
        self.emitf(
            f"mov rax, [rbp{idx:+d}]", f"cmp rax, [rbp{stop:+d}]", f"jge {end}"
        )
        self.emitf(
            f"mov rbx, [rbp{it:+d}]",
            f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
            f"mov rcx, [rbp{idx:+d}]",
            "mov rax, [rbx+rcx*8]",
            f"mov [rbp{var:+d}], rax",
        )
        # Optional filter.
        skip = None
        if e.cond is not None:
            skip = self.fresh("comp_skip")
            self._gen_truthy_test(e.cond, info, skip)
        # Append elt to result: rax = header, rbx = value.
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
        obj_t = A.expr_type(e.obj)
        if obj_t == "dict":
            # Evaluate key (rax = str ptr), spill, evaluate dict header,
            # call runtime helper.
            self.gen_expr(e.index, info)
            self.emitf("push rax")
            self.gen_expr(e.obj, info)  # rax = header
            self.emitf(
                "pop rbx",  # rbx = key ptr
                "call _runtime_dict_get",
            )  # raises if missing
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
        self.gen_expr(e.index, info)
        self.emitf("push rax")
        self.gen_expr(e.obj, info)  # rax = header
        # Negative index: rcx += len. We need the header in rax to read len,
        # so do the wrap BEFORE switching rax to the buffer pointer.
        pos = self.fresh("idx_pos")
        self.emitf(
            "pop rcx",  # rcx = index
            "test rcx, rcx",
            f"jns {pos}",
            f"add rcx, [rax+{self.LIST_LEN_OFF}]",
        )
        self.label(pos)
        self.emitf(f"mov rax, [rax+{self.LIST_BUF_OFF}]")
        # If this list holds floats, drop the 8-byte slot into xmm0; otherwise
        # keep it in rax (int / str-ptr both 8-byte integers).
        if e.inferred_type == "float":
            self.emitf("movsd xmm0, [rax+rcx*8]")
        else:
            self.emitf("mov rax, [rax+rcx*8]")

    def _gen_list_slice(self, e: A.Subscript, info: FuncInfo) -> None:
        """`xs[start:stop]` -> _runtime_list_slice(xs, start, stop).

        Uses INT64_MIN for missing start and INT64_MAX for missing stop;
        the runtime fills in 0 and len respectively.
        """
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
        SENTINEL_MAX = "0x7fffffffffffffff"

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
        # Insert each (key, value) pair via the runtime set helper. We can't
        # use `push rax / pop rbx` to stash the key across the value-eval:
        # if v_expr calls anything (e.g. a constructor), the callee's MS x64
        # shadow-space store at [rsp..rsp+31] would clobber the pushed key.
        # Use the pre-reserved frame slot instead.
        key_slot = info.locals_[f"__dictlit_key_{id(e)}"]
        for k_expr, v_expr in zip(e.keys, e.values):
            self.gen_expr(k_expr, info)  # rax = key ptr
            self.emitf(f"mov [rbp{key_slot:+d}], rax")
            self.gen_expr(v_expr, info)  # rax = value
            self.emitf(
                "mov rcx, rax",  # rcx = value
                f"mov rbx, [rbp{key_slot:+d}]",  # rbx = key ptr
                f"mov rax, [rbp{slot_off:+d}]",
                "call _runtime_dict_set",
            )
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    # Each entry maps str method name -> runtime symbol it dispatches to.
    # The arg count (and order) follows the sema.STR_METHODS table.
    STR_METHOD_RUNTIME = {
        "upper": "_runtime_str_upper",
        "lower": "_runtime_str_lower",
        "strip": "_runtime_str_strip",
        "lstrip": "_runtime_str_lstrip",
        "rstrip": "_runtime_str_rstrip",
        "startswith": "_runtime_str_starts_with",
        "endswith": "_runtime_str_ends_with",
        "find": "_runtime_str_index_of",
        "count": "_runtime_str_count",
        "replace": "_runtime_str_replace",
        "split": "_runtime_str_split",
        "join": "_runtime_str_join",
    }

    def _gen_str_method(self, e: A.MethodCall, info: FuncInfo) -> None:
        sym = self.STR_METHOD_RUNTIME[e.method]
        # Reusable slot stash so we can evaluate arguments (which may call)
        # without push/pop across the call.
        obj_slot = info.locals_[f"__strm_obj_{id(e)}"]
        if len(e.args) == 0:
            self.gen_expr(e.obj, info)
            self.emitf(f"call {sym}")
            return
        if len(e.args) == 1:
            self.gen_expr(e.obj, info)
            self.emitf(f"mov [rbp{obj_slot:+d}], rax")
            self.gen_expr(e.args[0], info)
            self.emitf("mov rbx, rax", f"mov rax, [rbp{obj_slot:+d}]", f"call {sym}")
            return
        if len(e.args) == 2:
            # replace(old, new). Two scratch slots.
            a1_slot = info.locals_[f"__strm_a1_{id(e)}"]
            self.gen_expr(e.obj, info)
            self.emitf(f"mov [rbp{obj_slot:+d}], rax")
            self.gen_expr(e.args[0], info)
            self.emitf(f"mov [rbp{a1_slot:+d}], rax")
            self.gen_expr(e.args[1], info)
            self.emitf(
                "mov rcx, rax",
                f"mov rbx, [rbp{a1_slot:+d}]",
                f"mov rax, [rbp{obj_slot:+d}]",
                f"call {sym}",
            )
            return
        raise NotImplementedError(f"str.{e.method}: too many args")

    def _gen_method_call(self, e: A.MethodCall, info: FuncInfo) -> None:
        # math.sqrt(x), math.pow(a, b) etc.
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
            bindings = self.imported_modules[e.obj.name]
            b = bindings.get(e.method)
            if b is not None and hasattr(b, "arg_types"):
                self._gen_ffi_call(b, e.args, info)
                return
        obj_t = A.expr_type(e.obj)
        if obj_t.startswith("super:"):
            # super().method(args): dispatch to the base class's method, but
            # with the *current* instance (`self`) as the receiver.
            parent = obj_t.split(":", 1)[1]
            owner = self._resolve_method_owner(parent, e.method)
            if owner is None:
                raise NotImplementedError(
                    f"super().{e.method}: base {parent!r} is not a user class "
                    f"serpent can dispatch to"
                )
            # Receiver is the enclosing method's own `self`, already in its slot.
            self._emit_positional_args(
                e, e.args, info, start_reg=1,
                receiver_slot=info.locals_["self"],
            )
            self.emit_call(self._method_symbol(owner, e.method))
            return
        if obj_t.startswith("instance:"):
            class_name = obj_t.split(":", 1)[1]
            owner = self._resolve_method_owner(class_name, e.method)
            if owner is None:
                raise NotImplementedError(f"no method {e.method!r} on {class_name}")
            # Sema normalized e.args to a complete positional list; evaluate the
            # receiver (e.obj) and args into slots, then load reg0=self, reg1..
            self._emit_positional_args(
                e, e.args, info, start_reg=1,
                receiver_expr=e.obj,
                receiver_slot=info.locals_[f"__callself_{id(e)}"],
            )
            self.emit_call(self._method_symbol(owner, e.method))
            return
        if obj_t == "str":
            self._gen_str_method(e, info)
            return
        if obj_t == "dict":
            if e.method == "get":
                # get(key, default): rax = default, rbx = key, rcx (header).
                self.gen_expr(e.args[0], info)
                self.emitf("push rax")  # key
                self.gen_expr(e.args[1], info)
                self.emitf("push rax")  # default
                self.gen_expr(e.obj, info)  # rax = header
                self.emitf(
                    "pop rcx",  # rcx = default
                    "pop rbx",  # rbx = key
                    "call _runtime_dict_get_default",
                )
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
            # pop returns the raw 8 bytes in rax. If the caller expects a
            # float, drop those bytes into xmm0.
            if e.inferred_type == "float":
                self.emitf("movq xmm0, rax")
            return
        raise NotImplementedError(f"method {e.method!r}")

    def _gen_binop(self, e: A.BinOp, info: FuncInfo) -> None:
        lt, rt = A.expr_type(e.left), A.expr_type(e.right)
        # String ops dispatch to runtime helpers.
        if "str" in (lt, rt):
            self._gen_binop_str(e, info, lt, rt)
            return
        # True division always returns a float (Python semantics) so route
        # through the float path even when both operands are ints.
        if "float" in (lt, rt) or e.op == "/":
            self._gen_binop_float(e, info, lt, rt)
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
        raise NotImplementedError(f"string binop {e.op!r}")

    def _gen_binop_float(self, e: A.BinOp, info: FuncInfo, lt: str, rt: str) -> None:
        # Evaluate left (promote to float if int), spill to stack, evaluate
        # right (promote), then load into xmm1, restore left to xmm0.
        self._gen_expr_as_float(e.left, info, lt)
        # Spill xmm0 to a scratch frame slot via the stack.
        self.emitf("sub rsp, 8", "movsd [rsp], xmm0")
        self._gen_expr_as_float(e.right, info, rt)
        self.emitf("movsd xmm1, xmm0", "movsd xmm0, [rsp]", "add rsp, 8")
        op = e.op
        if op == "+":
            self.emitf("addsd xmm0, xmm1")
        elif op == "-":
            self.emitf("subsd xmm0, xmm1")
        elif op == "*":
            self.emitf("mulsd xmm0, xmm1")
        elif op in ("//", "/"):
            # Both Python's / and // on floats give a float; for // Python
            # does a floor. We use a true div here for /, and divsd + floor
            # for //.
            self.emitf("divsd xmm0, xmm1")
            if op == "//":
                # Floor via roundsd; SSE4.1. Mode 1 = round toward -inf.
                self.emitf("roundsd xmm0, xmm0, 1")
        elif op == "%":
            # No native sd-mod; use libc fmod(xmm0, xmm1). System V/Win64:
            # fmod's first arg is xmm0, second is xmm1 — already in place.
            # On Windows we need shadow space + 16-aligned rsp.
            self._emit_call_libc_double_double("fmod")
        else:
            raise NotImplementedError(f"float binop {op!r}")

    def _gen_expr_as_float(self, expr, info: FuncInfo, ty: str) -> None:
        """Evaluate expr; ensure result is in xmm0 as a float, promoting if needed."""
        self.gen_expr(expr, info)
        if ty == "int":
            # int → float via cvtsi2sd
            self.emitf("cvtsi2sd xmm0, rax")

    def _gen_truthy_test(self, expr, info: FuncInfo, false_target: str) -> None:
        """Evaluate expr; jump to false_target if value is falsy."""
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
        # Default: int or pointer; non-zero is truthy.
        self.gen_expr(expr, info)
        self.emitf("test rax, rax", f"jz {false_target}")

    def _emit_binop_inline_float(self, op: str) -> None:
        """xmm0 (LHS), xmm1 (RHS) -> xmm0 = LHS op RHS."""
        if op == "+":
            self.emitf("addsd xmm0, xmm1")
        elif op == "-":
            self.emitf("subsd xmm0, xmm1")
        elif op == "*":
            self.emitf("mulsd xmm0, xmm1")
        elif op == "/" or op == "//":
            self.emitf("divsd xmm0, xmm1")
            if op == "//":
                self.emitf("roundsd xmm0, xmm0, 1")
        elif op == "%":
            self._emit_call_libc_double_double("fmod")
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
            # IDIV uses RDX:RAX / RBX -> RAX (quot), RDX (rem). CQO sign-extends.
            self.emitf("cqo", "idiv rbx")
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
        else:
            raise NotImplementedError(op)

    SETCC = {
        "==": "sete",
        "!=": "setne",
        "<": "setl",
        "<=": "setle",
        ">": "setg",
        ">=": "setge",
        # `is` / `is not` lower to identity-as-bit-equality. With serpent's
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
            rt = A.expr_type(e.operands[1])
            if rt in ("list", "tuple"):
                # Tuples share the list [cap,len,buf] layout, so the linear
                # scan is identical once we know the element kind.
                self._gen_list_in(e, info)
                return
            if rt == "dict":
                self._gen_dict_in(e, info)
                return
        # String compare: ==/!= and in/not in dispatch to runtime helpers.
        if (
            len(e.ops) == 1
            and A.expr_type(e.operands[0]) == "str"
            and A.expr_type(e.operands[1]) == "str"
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
        is_float = any(A.expr_type(o) == "float" for o in e.operands)

        if len(e.ops) == 1:
            if is_float:
                self._gen_expr_as_float(e.operands[0], info, A.expr_type(e.operands[0]))
                self.emitf("sub rsp, 8", "movsd [rsp], xmm0")
                self._gen_expr_as_float(e.operands[1], info, A.expr_type(e.operands[1]))
                self.emitf("movsd xmm1, xmm0", "movsd xmm0, [rsp]", "add rsp, 8")
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
                # xmm0 = current LHS
                self.emitf("sub rsp, 8", "movsd [rsp], xmm0")
                self._gen_expr_as_float(
                    e.operands[i + 1], info, A.expr_type(e.operands[i + 1])
                )
                self.emitf("movsd xmm1, xmm0", "movsd xmm0, [rsp]", "add rsp, 8")
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
        must be a str (sema enforces).
        """
        op = e.ops[0]
        slot_off = info.locals_[f"__dictin_{id(e)}"]
        self.gen_expr(e.operands[0], info)  # rax = key ptr
        self.emitf(f"mov [rbp{slot_off:+d}], rax")
        self.gen_expr(e.operands[1], info)  # rax = dict header
        self.emitf(
            f"mov rbx, [rbp{slot_off:+d}]",
            "call _runtime_dict_contains",
        )
        if op == "not in":
            self.emitf("xor rax, 1")

    def _gen_boolop(self, e: A.BoolOp, info: FuncInfo) -> None:
        # Short-circuit. Result is 0 or 1.
        end = self.fresh("bool_end")
        self.gen_expr(e.left, info)
        self.emitf("test rax, rax")
        if e.op == "and":
            self.emitf(f"jz {end}")  # left false -> result is left (0)
        else:
            self.emitf(f"jnz {end}")  # left true  -> result is left (nonzero)
        self.gen_expr(e.right, info)
        self.label(end)
        # Normalize to 0/1.
        self.emitf("test rax, rax", "setne al", "movzx rax, al")

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
        self, e, args, info, *, start_reg, receiver_expr=None, receiver_slot=None
    ) -> None:
        """Evaluate a call's receiver (if any) and `args` into pre-reserved
        frame slots, then load them into the ABI argument registers.

        Using slots instead of push/pop keeps rsp 16-byte aligned throughout —
        essential because an argument's own evaluation may emit a `call`
        (malloc for a list/dict literal, string concat, a nested call), and a
        stray 8-byte push would misalign the stack for that inner call.
        """
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
        # All evaluation done; load registers with no intervening call.
        if receiver_slot is not None:
            self.emitf(f"mov {self._arg_reg(0)}, [rbp{receiver_slot:+d}]")
        for i, off in enumerate(offs):
            reg = self._arg_reg(start_reg + i)
            if reg is None:
                raise NotImplementedError("too many call args")
            self.emitf(f"mov {reg}, [rbp{off:+d}]")

    def _gen_call(self, e: A.Call, info: FuncInfo) -> None:
        # FFI: bare-imported foreign function. The args may need int->float
        # promotion to match the declared signature.
        if e.func in self.ffi_funcs:
            self._gen_ffi_call(self.ffi_funcs[e.func], e.args, info)
            return
        if e.func == "print":
            self._gen_print(e, info)
            return
        if e.func == "len":
            arg = e.args[0]
            self.gen_expr(arg, info)  # rax = ptr
            t = A.expr_type(arg)
            if t in ("list", "tuple"):
                # Tuples reuse the list layout, so len lives at LIST_LEN_OFF.
                self.emitf(f"mov rax, [rax+{self.LIST_LEN_OFF}]")
            elif t == "dict":
                self.emitf(f"mov rax, [rax+{self.DICT_LEN_OFF}]")
            else:
                self._emit_strlen()  # rax = length (string)
            return
        if e.func == "str":
            arg_t = A.expr_type(e.args[0])
            self.gen_expr(e.args[0], info)
            if arg_t == "float":
                # xmm0 has the value; print into our int_to_str buffer via sprintf.
                self._emit_float_to_str()  # rax = ptr
            elif arg_t == "str":
                pass  # already a str ptr in rax
            else:
                self._emit_int_to_str()  # rax = ptr to ASCII
            return
        if e.func == "int":
            arg_t = A.expr_type(e.args[0])
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
            # getattr(obj, "name"[, default]) -> dict_get_default(obj, "name",
            # default-or-0). Instances are dicts keyed by field name, so this is
            # the same helper that plain `obj.name` uses, just with a caller-
            # supplied default. The name arg is a string literal (sema-checked).
            key_label, _ = self.intern_string(e.args[1].value)
            if len(e.args) == 3:
                dslot = info.locals_[f"__getattr_def_{id(e)}"]
                self.gen_expr(e.args[2], info)
                if A.expr_type(e.args[2]) == "float":
                    self.emitf("movq rax, xmm0")
                self.emitf(f"mov [rbp{dslot:+d}], rax")
                self.gen_expr(e.args[0], info)  # rax = instance dict
                self.emitf(
                    f"lea rbx, [{key_label}]",
                    f"mov rcx, [rbp{dslot:+d}]",
                    "call _runtime_dict_get_default",
                )
            else:
                self.gen_expr(e.args[0], info)  # rax = instance dict
                self.emitf(
                    f"lea rbx, [{key_label}]",
                    "xor rcx, rcx",
                    "call _runtime_dict_get_default",
                )
            return
        if e.func == "hasattr":
            # hasattr(obj, "name") -> dict_contains(obj, "name") (0/1).
            key_label, _ = self.intern_string(e.args[1].value)
            self.gen_expr(e.args[0], info)  # rax = instance dict
            self.emitf(
                f"lea rbx, [{key_label}]",
                "call _runtime_dict_contains",
            )
            return
        # Constructor: ClassName(args). Allocate an empty dict, then if the
        # class chain provides an __init__, dispatch to it with the instance
        # as the first argument.
        if e.func in self.mod.classes_sig:
            self._gen_constructor(e, info)
            return
        if e.func not in self.funcs:
            raise NameError(f"undefined function {e.func}")
        # Sema has normalized e.args to a complete positional list (defaults
        # filled, keyword args placed, varargs packed), so no _fill_defaults.
        self._emit_positional_args(e, e.args, info, start_reg=0)
        self.emit_call(e.func)

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
        return list(args) + tail

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

        init_owner = self._resolve_method_owner(e.func, "__init__")
        if init_owner is not None:
            # __init__(self, args...). Sema normalized e.args to a complete
            # positional list; the instance (already in slot_off) is reg 0.
            self._emit_positional_args(
                e, e.args, info, start_reg=1, receiver_slot=slot_off
            )
            self.emit_call(self._method_symbol(init_owner, "__init__"))
        # Result: the instance pointer (caller may discard).
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    @staticmethod
    def _method_symbol(class_name: str, method_name: str) -> str:
        """Mangle a method into a unique linker symbol: ClassName__method.

        Note that `__init__` mangles to `Class____init__` (four underscores)
        which is intentional — the separator is always two underscores.
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
    # The serpent FFI is purely positional. Each foreign function's signature
    # is fully known statically (serpent/stdlib/<mod>.py). The codegen
    # evaluates each argument, promotes int -> float as needed, and places it
    # in the correct ABI register slot. Integer args use the integer regs
    # (rdi/rsi/... on Linux, rcx/rdx/... on Windows); float args use XMM0..N.
    # Result type tells callers whether to expect rax or xmm0.

    def _gen_ffi_call(self, fn, args, info: FuncInfo) -> None:
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
            else:  # int / str (pointer)
                self.gen_expr(arg, info)
                slot = info.locals_[f"__ffi_arg_{id(fn)}_{i}"]
                self.emitf(f"mov [rbp{slot:+d}], rax")
                slot_offs.append(slot)
        # Now load each arg into the ABI register slot.
        int_regs = self._int_arg_regs()
        # Per System V we pass each float in a distinct XMM register. We do
        # the same on Windows (it also uses xmm0..xmm3 for the first 4 args).
        float_idx = 0
        int_idx = 0
        for i, (want, slot) in enumerate(zip(fn.arg_types, slot_offs)):
            if want == "float":
                self.emitf(f"movsd xmm{float_idx}, [rbp{slot:+d}]")
                float_idx += 1
                # Windows: variadic functions also need the value mirrored
                # into the integer register; non-variadic functions don't
                # care. We mirror unconditionally on Windows because the
                # cost is one mov and the simplicity is worth it.
                if self._needs_xmm_mirror_to_int():
                    int_reg = self._int_arg_regs()[i]
                    self.emitf(f"movq {int_reg}, xmm{float_idx - 1}")
            else:
                reg = int_regs[int_idx]
                int_idx += 1
                self.emitf(f"mov {reg}, [rbp{slot:+d}]")
        # System V variadic ABI requires AL = number of xmm args used.
        # Non-variadic libc functions ignore AL. Setting it is harmless.
        if self._sysv_needs_al_count():
            self.emitf(f"mov al, {float_idx}")
        self.emit_call(fn.c_name)

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
        if not e.args:
            self._emit_print_newline()
            return
        for i, arg in enumerate(e.args):
            if isinstance(arg, A.FString):
                # f-string segments are printed contiguously (no inter-
                # segment space). Each segment is either a StrLit or any
                # expression typed int/str.
                for seg in arg.segments:
                    self._emit_print_value(seg, info)
            else:
                self._emit_print_value(arg, info)
            if i < len(e.args) - 1:
                self._emit_print_space()
        self._emit_print_newline()

    def _emit_print_value(self, expr, info: FuncInfo) -> None:
        """Emit code that prints a single typed value (no newline, no space)."""
        t = A.expr_type(expr)
        self.gen_expr(expr, info)
        if t == "str":
            self._emit_print_str_ptr_no_newline()
        elif t == "float":
            self._emit_print_float_no_newline()
        else:
            self._emit_print_int_no_newline()

    # Runtime primitives provided by the target subclass. Declared here as
    # abstract stubs so type-checkers can see them on the base class — that
    # way callers in `Codegen` don't each need a `# type: ignore`.

    def emit_print_impls(self) -> None:
        raise NotImplementedError

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

    def _emit_float_to_str(self) -> None:
        """In: xmm0 = double. Out: rax = ptr to nul-terminated `%g` form."""
        raise NotImplementedError

    def _emit_str_to_float(self) -> None:
        """In: rax = nul-terminated ptr. Out: xmm0 = parsed double (atof)."""
        raise NotImplementedError

    # I/O ------------------------------------------------------------------
    def _emit_input_line(self) -> None:
        """Out: rax = ptr to the most recent input buffer (\\n stripped)."""
        raise NotImplementedError

    # Memory ---------------------------------------------------------------
    def _emit_malloc(self, n: int) -> None:
        """Compile-time `n` bytes -> rax = ptr."""
        del n
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
        """In: xmm0, xmm1 = args. Out: xmm0 = result. Calls `fn(double,double)`."""
        del fn
        raise NotImplementedError

    # Exception machinery --------------------------------------------------
    def _emit_call_setjmp(self, buf_off: int) -> None:
        """Sets up a setjmp call against the buffer at [rbp+buf_off]."""
        del buf_off
        raise NotImplementedError
