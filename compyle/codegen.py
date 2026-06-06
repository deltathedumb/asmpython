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
    locals_: dict[str, int] = field(default_factory=dict)   # name -> RBP offset (negative)
    local_types: dict[str, str] = field(default_factory=dict)  # name -> 'int'|'float'|'str'|'list'
    frame_size: int = 0                                     # bytes to subtract from RSP


# --- Base codegen -------------------------------------------------------------

class Codegen:
    """Common shape; subclasses provide the target-specific prologue,
    print implementations, _exit, and section directives."""

    # Each subclass sets these:
    section_text: str = ""
    section_data: str = ""
    section_rodata: str = ""
    label_main: str = ""        # public entry symbol (e.g. _start on Linux, main on Windows)

    def __init__(self, mod: A.Module) -> None:
        self.mod = mod
        self.lines: list[str] = []
        self.strings: list[tuple[str, str]] = []   # (label, bytes-literal)
        self.floats: list[tuple[str, float]] = []  # (label, value)
        self.label_counter = 0
        # FFI surface: { compyle_name: stdlib.Func } across all imports, used
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
            self.funcs[f.name] = FuncInfo(name=f.name, params=list(f.params))

    # ---- emit helpers -------------------------------------------------------

    def emit(self, line: str = "") -> None:
        self.lines.append(line)

    def emitf(self, *lines: str) -> None:
        for l in lines:
            self.lines.append("    " + l)

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
        self.emit(f"; compyle generated for target = {self.__class__.__name__}")
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
        self.emit_print_impls()
        self.emit_data_sections()
        return "\n".join(self.lines) + "\n"

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
        info = FuncInfo(name=f.name, params=list(f.params))
        # Each local (incl. params) gets an 8-byte slot at a negative RBP offset.
        offset = 0
        for p in f.params:
            offset -= 8
            info.locals_[p] = offset
            info.local_types[p] = "int"
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

        def walk_expr(expr):
            # Pre-allocate a scratch slot per ListLit so codegen never has to
            # extend the frame at emit-time.
            if isinstance(expr, A.ListLit):
                define(f"__listlit_{id(expr)}")
                for el in expr.elems:
                    walk_expr(el)
            elif isinstance(expr, A.BinOp):
                walk_expr(expr.left); walk_expr(expr.right)
            elif isinstance(expr, A.Compare):
                for o in expr.operands: walk_expr(o)
            elif isinstance(expr, A.BoolOp):
                walk_expr(expr.left); walk_expr(expr.right)
            elif isinstance(expr, A.UnaryOp):
                walk_expr(expr.operand)
            elif isinstance(expr, A.Call):
                # FFI call needs one scratch slot per arg.
                if expr.func in self.ffi_funcs:
                    fn = self.ffi_funcs[expr.func]
                    for k in range(len(expr.args)):
                        define(f"__ffi_arg_{id(fn)}_{k}",
                               "float" if fn.arg_types[k] == "float" else "int")
                for a in expr.args: walk_expr(a)
            elif isinstance(expr, A.MethodCall):
                # math.sqrt(x) — same FFI scratch reservation.
                if isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_modules:
                    bindings = self.imported_modules[expr.obj.name]
                    b = bindings.get(expr.method)
                    if b is not None and hasattr(b, "arg_types"):
                        for k in range(len(expr.args)):
                            define(f"__ffi_arg_{id(b)}_{k}",
                                   "float" if b.arg_types[k] == "float" else "int")
                else:
                    walk_expr(expr.obj)
                for a in expr.args: walk_expr(a)
            elif isinstance(expr, A.Subscript):
                walk_expr(expr.obj); walk_expr(expr.index)
            elif isinstance(expr, A.Attr):
                # Module attr access doesn't need to evaluate the obj.
                if isinstance(expr.obj, A.Name) and expr.obj.name in self.imported_modules:
                    pass
                else:
                    walk_expr(expr.obj)
            elif isinstance(expr, A.FString):
                for s in expr.segments: walk_expr(s)

        def walk(stmts):
            for s in stmts:
                if isinstance(s, A.Assign):
                    define(s.target, A.expr_type(s.value))
                    walk_expr(s.value)
                elif isinstance(s, A.AugAssign):
                    define(s.target, A.expr_type(s.value))
                    walk_expr(s.value)
                elif isinstance(s, A.For):
                    define(s.var, "int")
                    define(f"__for_stop_{id(s)}", "int")
                    define(f"__for_step_{id(s)}", "int")
                    if s.iter is not None:
                        define(f"__for_iter_{id(s)}", "int")  # ptr; treat as int slot
                        walk_expr(s.iter)
                    else:
                        for a in s.range_args: walk_expr(a)
                    walk(s.body)
                elif isinstance(s, A.If):
                    walk_expr(s.test)
                    walk(s.then); walk(s.orelse)
                elif isinstance(s, A.While):
                    walk_expr(s.test); walk(s.body)
                elif isinstance(s, A.Return):
                    if s.value is not None: walk_expr(s.value)
                elif isinstance(s, A.ExprStmt):
                    walk_expr(s.expr)
                elif isinstance(s, A.IndexAssign):
                    walk_expr(s.target.obj)
                    walk_expr(s.target.index)
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
        if isinstance(stmt, A.AugAssign):
            off = info.locals_[stmt.target]
            ty = info.local_types.get(stmt.target, "int")
            if ty == "float":
                self._gen_expr_as_float(stmt.value, info, A.expr_type(stmt.value))
                self.emitf("movsd xmm1, xmm0",
                           f"movsd xmm0, [rbp{off:+d}]")
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
            self.gen_expr(stmt.target.index, info)
            self.emitf("push rax")
            self.gen_expr(stmt.value, info)
            self.emitf("push rax")
            self.gen_expr(stmt.target.obj, info)   # rax = header
            self.emitf("pop rbx",                  # rbx = value
                       "pop rcx",                  # rcx = index
                       f"mov rax, [rax+{self.LIST_BUF_OFF}]",
                       "mov [rax+rcx*8], rbx")
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
        self.gen_expr(start_expr, info); self.emitf(f"mov [rbp{var_off:+d}], rax")
        self.gen_expr(stop_expr, info);  self.emitf(f"mov [rbp{stop_off:+d}], rax")
        self.gen_expr(step_expr, info);  self.emitf(f"mov [rbp{step_off:+d}], rax")

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

    def _gen_for_list(self, stmt: A.For, info: FuncInfo) -> None:
        # Lower as:  i = 0; iter = <list>; while i < iter.length: var = iter[i]; body; i += 1
        # We reuse stop_off (length cache) and iter_off (list pointer).
        var_off = info.locals_[stmt.var]
        iter_off = info.locals_[f"__for_iter_{id(stmt)}"]
        stop_off = info.locals_[f"__for_stop_{id(stmt)}"]
        step_off = info.locals_[f"__for_step_{id(stmt)}"]   # repurposed as index

        # Evaluate iterable (header ptr); store header + cached length.
        self.gen_expr(stmt.iter, info)
        self.emitf(f"mov [rbp{iter_off:+d}], rax",
                   f"mov rbx, [rax+{self.LIST_LEN_OFF}]",
                   f"mov [rbp{stop_off:+d}], rbx",
                   f"mov qword [rbp{step_off:+d}], 0")

        top = self.fresh("for_list")
        cont = self.fresh("for_list_cont")
        end = self.fresh("endfor_list")
        self.loop_labels.append((cont, end))
        self.label(top)
        self.emitf(f"mov rax, [rbp{step_off:+d}]",
                   f"cmp rax, [rbp{stop_off:+d}]",
                   f"jge {end}")
        # iter is header; reload buffer each iteration (it may have been
        # reallocated by append calls inside the loop).
        self.emitf(f"mov rbx, [rbp{iter_off:+d}]",
                   f"mov rbx, [rbx+{self.LIST_BUF_OFF}]",
                   f"mov rcx, [rbp{step_off:+d}]",
                   "mov rax, [rbx+rcx*8]",
                   f"mov [rbp{var_off:+d}], rax")
        for s in stmt.body:
            self.gen_stmt(s, info)
        self.label(cont)
        self.emitf(f"inc qword [rbp{step_off:+d}]",
                   f"jmp {top}")
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
                self.emitf(f"movsd xmm1, [{zero_lbl}]",
                           "subsd xmm1, xmm0",
                           "movsd xmm0, xmm1")
                return
            if operand_t == "float" and expr.op == "not":
                # not <float> -> int 0/1
                self.gen_expr(expr.operand, info)
                zero_lbl = self.intern_float(0.0)
                self.emitf(f"movsd xmm1, [{zero_lbl}]",
                           "ucomisd xmm0, xmm1",
                           "sete al",                # equal & no NaN -> true
                           "movzx rax, al")
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
        raise NotImplementedError(f"expr {expr}")

    def _gen_attr(self, e: A.Attr, info: FuncInfo) -> None:
        # Module constant access: math.pi etc.
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
            bindings = self.imported_modules[e.obj.name]
            b = bindings.get(e.name)
            if b is not None and not hasattr(b, "arg_types"):  # Const
                self._gen_const_load(b)
                return
        raise NotImplementedError(f"attr {e.name!r} on non-module")

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

    def _gen_list_lit(self, e: A.ListLit, info: FuncInfo) -> None:
        # We cannot use push/pop across `call` (it breaks 16-byte stack
        # alignment), so each ListLit gets a pre-reserved frame slot to
        # hold the header pointer between malloc calls.
        slot_off = info.locals_[f"__listlit_{id(e)}"]

        n = len(e.elems)
        cap = max(n, 4)
        self._emit_malloc(self.LIST_HEADER)        # rax = header
        self.emitf(f"mov qword [rax+{self.LIST_CAP_OFF}], {cap}",
                   f"mov qword [rax+{self.LIST_LEN_OFF}], {n}",
                   f"mov [rbp{slot_off:+d}], rax") # park header in slot
        self._emit_malloc(cap * 8)                 # rax = buffer
        self.emitf(f"mov rbx, [rbp{slot_off:+d}]", # rbx = header
                   f"mov [rbx+{self.LIST_BUF_OFF}], rax")
        for i, el in enumerate(e.elems):
            self.gen_expr(el, info)                # rax = value (may call!)
            self.emitf(f"mov rbx, [rbp{slot_off:+d}]",
                       f"mov rcx, [rbx+{self.LIST_BUF_OFF}]",
                       f"mov [rcx+{i*8}], rax")
        self.emitf(f"mov rax, [rbp{slot_off:+d}]")

    def _gen_subscript(self, e: A.Subscript, info: FuncInfo) -> None:
        self.gen_expr(e.index, info)
        self.emitf("push rax")
        self.gen_expr(e.obj, info)                 # rax = header
        self.emitf("pop rcx",                      # rcx = index
                   f"mov rax, [rax+{self.LIST_BUF_OFF}]",
                   "mov rax, [rax+rcx*8]")

    def _gen_method_call(self, e: A.MethodCall, info: FuncInfo) -> None:
        # math.sqrt(x), math.pow(a, b) etc.
        if isinstance(e.obj, A.Name) and e.obj.name in self.imported_modules:
            bindings = self.imported_modules[e.obj.name]
            b = bindings.get(e.method)
            if b is not None and hasattr(b, "arg_types"):
                self._gen_ffi_call(b, e.args, info)
                return
        if e.method == "append":
            self.gen_expr(e.args[0], info)
            self.emitf("push rax")
            self.gen_expr(e.obj, info)             # rax = header
            self.emitf("pop rbx",                  # rbx = value
                       "call _runtime_list_append")
            return
        if e.method == "pop":
            self.gen_expr(e.obj, info)             # rax = header
            self.emitf("call _runtime_list_pop")
            return
        raise NotImplementedError(f"method {e.method!r}")

    def _gen_binop(self, e: A.BinOp, info: FuncInfo) -> None:
        lt, rt = A.expr_type(e.left), A.expr_type(e.right)
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
            self.gen_expr(expr, info)              # xmm0 = value
            zero_lbl = self.intern_float(0.0)
            past_nan = self.fresh("not_nan")
            self.emitf(f"movsd xmm1, [{zero_lbl}]",
                       "ucomisd xmm0, xmm1",
                       # ucomisd sets PF on unordered (NaN). We treat NaN as
                       # truthy (Python semantics), so skip the zero-check.
                       f"jp {past_nan}",
                       f"je {false_target}")
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
        "==": "sete", "!=": "setne",
        "<": "setl",  "<=": "setle",
        ">": "setg",  ">=": "setge",
    }
    # Inverse jump for short-circuit on chained compares.
    JCC_INV = {
        "==": "jne", "!=": "je",
        "<": "jge",  "<=": "jg",
        ">": "jle",  ">=": "jl",
    }
    # For ucomisd: signed-comparison setcc/jcc don't work because the result
    # flags are unordered/equal/below/above. Use the unsigned variants.
    SETCC_FLOAT = {
        "==": "sete", "!=": "setne",
        "<": "setb",  "<=": "setbe",
        ">": "seta",  ">=": "setae",
    }
    JCC_INV_FLOAT = {
        "==": "jne", "!=": "je",
        "<": "jae",  "<=": "ja",
        ">": "jbe",  ">=": "jb",
    }

    def _gen_compare(self, e: A.Compare, info: FuncInfo) -> None:
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
                self._gen_expr_as_float(e.operands[i+1], info, A.expr_type(e.operands[i+1]))
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
            self.emitf(f"jnz {end}") # left true  -> result is left (nonzero)
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
            self.gen_expr(arg, info)               # rax = ptr
            if A.expr_type(arg) == "list":
                self.emitf(f"mov rax, [rax+{self.LIST_LEN_OFF}]")
            else:
                self._emit_strlen()                # rax = length (string)
            return
        if e.func == "str":
            arg_t = A.expr_type(e.args[0])
            self.gen_expr(e.args[0], info)
            if arg_t == "float":
                # xmm0 has the value; print into our int_to_str buffer via sprintf.
                self._emit_float_to_str()          # rax = ptr
            elif arg_t == "str":
                pass                               # already a str ptr in rax
            else:
                self._emit_int_to_str()            # rax = ptr to ASCII
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
                self._emit_str_to_float()          # xmm0 = parsed
            elif arg_t == "int":
                self.emitf("cvtsi2sd xmm0, rax")
            # float -> float: nothing to do.
            return
        if e.func == "input":
            if e.args:
                # Print the prompt first; no trailing newline.
                self.gen_expr(e.args[0], info)
                self._emit_print_str_ptr_no_newline()
            self._emit_input_line()                # rax = ptr to read buffer
            return
        if e.func not in self.funcs:
            raise NameError(f"undefined function {e.func}")
        # Evaluate args left-to-right, push to runtime stack, then pop into
        # ABI argument registers in reverse.
        for a in e.args:
            self.gen_expr(a, info)
            self.emitf("push rax")
        for i in reversed(range(len(e.args))):
            reg = self._arg_reg(i)
            if reg is None:
                raise NotImplementedError("too many call args")
            self.emitf(f"pop {reg}")
        self.emit_call(e.func)

    # ---- FFI dispatch ------------------------------------------------------
    #
    # The compyle FFI is purely positional. Each foreign function's signature
    # is fully known statically (compyle/stdlib/<mod>.py). The codegen
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
                    self.emitf(f"movq {int_reg}, xmm{float_idx-1}")
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

    # The runtime primitives below are provided by the target subclass:
    #   _emit_print_int_no_newline       (in: rax = signed int)
    #   _emit_print_str_ptr_no_newline   (in: rax = nul-terminated ptr)
    #   _emit_print_space
    #   _emit_print_newline
    #   _emit_strlen                     (in: rax = ptr; out: rax = length)
    #   _emit_int_to_str                 (in: rax = int; out: rax = ptr)
    #   _emit_str_to_int                 (in: rax = ptr; out: rax = int)
    #   _emit_input_line                 (out: rax = ptr to read buffer)
    #   _emit_malloc(n)                  (out: rax = ptr; n is a compile-time const)
    #   _emit_call_libc_double_double(fn) (xmm0,xmm1 = args; result in xmm0)
    def emit_print_impls(self) -> None:
        raise NotImplementedError
