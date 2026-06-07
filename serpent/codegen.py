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

    # ---- emit helpers -------------------------------------------------------

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
        # String runtime
        "_runtime_str_concat",
        "_runtime_str_repeat",
        "_runtime_str_eq",
        "_runtime_str_cmp",
        "_runtime_str_char_at",
        "_runtime_str_slice",
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

    def _collect_locals(self, f: A.FuncDef) -> FuncInfo:
        info = FuncInfo(name=f.name, params=list(f.params), defaults=list(f.defaults))
        # Each local (incl. params) gets an 8-byte slot at a negative RBP offset.
        offset = 0
        for i, p in enumerate(f.params):
            offset -= 8
            info.locals_[p] = offset
            # Default-typed params take the default's type so the body emits
            # the right load (int vs movsd) when reading the param later.
            ty = "int"
            if i < len(f.defaults) and f.defaults[i] is not None:
                ty = A.expr_type(f.defaults[i])
            info.local_types[p] = ty

        # Walk the body for any name that becomes bound.
        def define(name: str, ty: str = "int") -> None:
            nonlocal offset
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
            elif isinstance(expr, A.DictLit):
                define(f"__dictlit_{id(expr)}")
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
                for o in expr.operands:
                    walk_expr(o)
            elif isinstance(expr, A.BoolOp):
                walk_expr(expr.left)
                walk_expr(expr.right)
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
                    walk_expr(expr.obj)
                for a in expr.args:
                    walk_expr(a)
            elif isinstance(expr, A.Subscript):
                if isinstance(expr.index, A.Slice):
                    # slice needs 3 scratch slots: obj, start, stop.
                    define(f"__strsl_obj_{id(expr)}")
                    define(f"__strsl_start_{id(expr)}")
                    walk_expr(expr.obj)
                    if expr.index.start is not None:
                        walk_expr(expr.index.start)
                    if expr.index.stop is not None:
                        walk_expr(expr.index.stop)
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
                    for i, v in enumerate(s.values):
                        walk_expr(v)
                        define(f"__tup_tmp_{id(s)}_{i}", A.expr_type(v))
                    for t, v in zip(s.targets, s.values):
                        define(t, A.expr_type(v))
                elif isinstance(s, A.For):
                    # Loop var inherits the iterable's element type so the
                    # later Name load picks the right register class.
                    var_ty = "int"
                    if s.iter is not None and A.expr_type(s.iter) == "list":
                        if isinstance(s.iter, A.Name):
                            var_ty = s.iter.list_el_type
                        elif isinstance(s.iter, A.ListLit):
                            var_ty = s.iter.el_type
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
            ty = info.local_types.get(stmt.target, "int")
            value_t = A.expr_type(stmt.value)
            off = info.locals_[stmt.target]
            if ty == "float":
                # Slot expects a float; promote int RHS to float.
                self._gen_expr_as_float(stmt.value, info, value_t)
                self.emitf(f"movsd [rbp{off:+d}], xmm0")
            else:
                self.gen_expr(stmt.value, info)
                self.emitf(f"mov [rbp{off:+d}], rax")
            return
        if isinstance(stmt, A.TupleAssign):
            # Evaluate every RHS into a pre-reserved scratch slot, then
            # commit each store. Two-pass model means `a, b = b, a` works.
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
            off = info.locals_[stmt.target]
            ty = info.local_types.get(stmt.target, "int")
            if ty == "float":
                self._gen_expr_as_float(stmt.value, info, A.expr_type(stmt.value))
                self.emitf("movsd xmm1, xmm0", f"movsd xmm0, [rbp{off:+d}]")
                # Apply float op in place.
                self._emit_binop_inline_float(stmt.op)
                self.emitf(f"movsd [rbp{off:+d}], xmm0")
            else:
                self.emitf(f"mov rax, [rbp{off:+d}]", "push rax")
                self.gen_expr(stmt.value, info)
                self.emitf("mov rbx, rax", "pop rax")
                self._emit_binop_inline(stmt.op)
                self.emitf(f"mov [rbp{off:+d}], rax")
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
            self.emitf(
                "pop rbx",  # rbx = value
                "pop rcx",  # rcx = index
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

    def _gen_for(self, stmt: A.For, info: FuncInfo) -> None:
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
            if expr.name not in info.locals_:
                raise NameError(f"undefined variable {expr.name}")
            off = info.locals_[expr.name]
            ty = info.local_types.get(expr.name, "int")
            if ty == "float":
                self.emitf(f"movsd xmm0, [rbp{off:+d}]")
            else:
                self.emitf(f"mov rax, [rbp{off:+d}]")
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
        if isinstance(expr, A.Call):
            self._gen_call(expr, info)
            return
        if isinstance(expr, A.ListLit):
            self._gen_list_lit(expr, info)
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
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
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
        self.label("_runtime_dict_set")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
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
        self.label("_runtime_dict_grow")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
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

        # Error message.
        self.emit("section .rodata")
        self.emit('_runtime_dict_key_error_msg: db "KeyError: key not in dict",10,0')

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
        self.label("_runtime_str_count")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 48",
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
        self.label("_runtime_str_replace")
        self.emitf(
            "push rbp",
            "mov rbp, rsp",
            "sub rsp, 96",
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

        self.emit("section .rodata")
        self.emit('_runtime_str_oob_msg: db "string index out of range",0')

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

    def _gen_subscript(self, e: A.Subscript, info: FuncInfo) -> None:
        if isinstance(e.index, A.Slice):
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
        self.emitf(
            "pop rcx",  # rcx = index
            f"mov rax, [rax+{self.LIST_BUF_OFF}]",
        )
        # If this list holds floats, drop the 8-byte slot into xmm0; otherwise
        # keep it in rax (int / str-ptr both 8-byte integers).
        if e.inferred_type == "float":
            self.emitf("movsd xmm0, [rax+rcx*8]")
        else:
            self.emitf("mov rax, [rax+rcx*8]")

    def _gen_str_slice(self, e: A.Subscript, info: FuncInfo) -> None:
        """s[start:stop] -> _runtime_str_slice(s, start, stop).

        Missing endpoints default to 0 (start) and INT64_MAX (stop) -- the
        runtime clamps both to [0, len], so omitting stop yields s[start:].
        """
        sl: A.Slice = e.index  # type: ignore[assignment]
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
        # Insert each (key, value) pair via the runtime set helper.
        for k_expr, v_expr in zip(e.keys, e.values):
            self.gen_expr(k_expr, info)  # rax = key ptr
            self.emitf("push rax")
            self.gen_expr(v_expr, info)  # rax = value
            self.emitf(
                "mov rcx, rax",  # rcx = value
                "pop rbx",  # rbx = key ptr
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
        if obj_t.startswith("instance:"):
            class_name = obj_t.split(":", 1)[1]
            owner = self._resolve_method_owner(class_name, e.method)
            if owner is None:
                raise NotImplementedError(f"no method {e.method!r} on {class_name}")
            # Apply default arguments declared on the resolved method.
            method_defs = self._method_defaults(owner, e.method)
            # method_defs aligns with method.params (which includes 'self').
            # User args don't include self, so trim the head default before
            # filling.
            user_defaults = method_defs[1:] if method_defs else []
            full_args = self._fill_defaults(e.args, user_defaults)
            # Push user args, then load self last so it ends up in reg 0.
            for a in full_args:
                self.gen_expr(a, info)
                self.emitf("push rax")
            for i in reversed(range(len(full_args))):
                reg = self._arg_reg(i + 1)
                if reg is None:
                    raise NotImplementedError("too many method args")
                self.emitf(f"pop {reg}")
            self.gen_expr(e.obj, info)  # rax = instance
            self.emitf(f"mov {self._arg_reg(0)}, rax")
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
            if t == "list":
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
        # Constructor: ClassName(args). Allocate an empty dict, then if the
        # class chain provides an __init__, dispatch to it with the instance
        # as the first argument.
        if e.func in self.mod.classes_sig:
            self._gen_constructor(e, info)
            return
        if e.func not in self.funcs:
            raise NameError(f"undefined function {e.func}")
        target = self.funcs[e.func]
        full_args = self._fill_defaults(e.args, target.defaults)
        # Evaluate args left-to-right, push to runtime stack, then pop into
        # ABI argument registers in reverse.
        for a in full_args:
            self.gen_expr(a, info)
            self.emitf("push rax")
        for i in reversed(range(len(full_args))):
            reg = self._arg_reg(i)
            if reg is None:
                raise NotImplementedError("too many call args")
            self.emitf(f"pop {reg}")
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
            # __init__(self, args...). Stash user args on the stack, pop into
            # ABI regs 1..N, then load self into reg 0 last.
            init_defs = self._method_defaults(init_owner, "__init__")
            user_defaults = init_defs[1:] if init_defs else []
            full_args = self._fill_defaults(e.args, user_defaults)
            for a in full_args:
                self.gen_expr(a, info)
                self.emitf("push rax")
            for i in reversed(range(len(full_args))):
                reg = self._arg_reg(i + 1)
                if reg is None:
                    raise NotImplementedError("too many ctor args")
                self.emitf(f"pop {reg}")
            self.emitf(f"mov {self._arg_reg(0)}, [rbp{slot_off:+d}]")
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
