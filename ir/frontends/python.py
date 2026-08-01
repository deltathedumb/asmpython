"""Python -> IR.

Lowers a statically-typed subset of Python using CPython's own `ast` module to
parse, so the grammar is never a source of divergence: if CPython accepts the
file, this sees exactly the tree CPython would.

WHAT THIS DEMONSTRATES
----------------------
The point of this frontend is to show where a language's semantics live. Three
places where Python and the IR genuinely disagree, and what lowering does about
each:

  `//` and `%`   Python floors toward negative infinity; `Op.DIV` truncates
                 toward zero, like C and like every machine. `-7 // 2` is -4 in
                 Python and -3 in the IR. Lowered as a truncating division plus
                 a correction when the signs differ and the remainder is not
                 zero -- five instructions, emitted once here rather than
                 implemented once per backend.

  `and` / `or`   Return an OPERAND, not a bool, and short-circuit. Lowered to
                 branches over a shared result register, which is exactly what
                 mutable registers are for.

  comparisons    Chain: `a < b < c` evaluates `b` once. Lowered as nested
                 branches, not as two independent comparisons.

THE SUBSET
----------
Annotated `int`/`float`/`bool` parameters and locals, arithmetic, comparisons,
if/while/for-over-range, function calls, and `print` of an integer. No objects,
no dynamic typing, no exceptions, no closures.

That is a real limit and it is the honest one to draw here: this is the
frontend that proves the IR is usable, not a Python implementation. Everything
absent is absent because it needs a runtime and an object model, which is a
separate body of work -- the legacy `asmpython/` tree has one, and it stays
where it is.
"""
from __future__ import annotations

import ast
from pathlib import Path

from .. import types as T
from ..core import Builder, Func, Module
from ..frontend import CompileError, Frontend, register
from ..ops import Op

#: Python annotation -> IR type. Nothing dynamic: a name this does not know is
#: rejected with the list, rather than silently becoming a pointer.
_TYPES = {"int": T.I64, "float": T.F64, "bool": T.I1, "None": T.VOID}

_CMP = {
    ast.Eq: Op.EQ, ast.NotEq: Op.NE, ast.Lt: Op.LT,
    ast.LtE: Op.LE, ast.Gt: Op.GT, ast.GtE: Op.GE,
}


class PythonFrontend(Frontend):
    name = "python"
    extensions = (".py",)
    description = "statically-annotated Python subset"

    def compile(self, source: str, path: Path | None = None) -> Module:
        try:
            tree = ast.parse(source, filename=str(path or "<source>"))
        except SyntaxError as e:
            raise CompileError(e.msg, e.lineno, path) from None
        return _Lowering(path).module_of(tree)


class _Lowering:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.m = Module(name=(path.stem if path else "module"))
        self.sigs: dict[str, tuple[list[T.Type], T.Type]] = {}

    # ── module ──────────────────────────────────────────────────────────────
    def module_of(self, tree: ast.Module) -> Module:
        for host, params in (("print_int", [T.I64]), ("print_str", [T.PTR])):
            f = Func(host, T.VOID, external=True)
            for i, ty in enumerate(params):
                f.params.append(i)
                f.regs[i] = ty
            self.m.funcs.append(f)

        fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        for node in fns:                       # signatures first, so order
            self.sigs[node.name] = self._sig(node)   # of definition is free
        for node in fns:
            self.m.funcs.append(self._func(node))

        stray = [n for n in tree.body
                 if not isinstance(n, (ast.FunctionDef, ast.Import,
                                       ast.ImportFrom, ast.Expr))]
        if stray:
            raise CompileError(
                "only function definitions are supported at module level",
                stray[0].lineno, self.path)
        if self.m.func("main") is None:
            raise CompileError("no `main` function", None, self.path)
        return self.m

    def _sig(self, node: ast.FunctionDef) -> tuple[list[T.Type], T.Type]:
        params = [self._ty(a.annotation, a.arg, node.lineno)
                  for a in node.args.args]
        ret = (self._ty(node.returns, "return", node.lineno)
               if node.returns else T.VOID)
        return params, ret

    def _ty(self, ann, what: str, line: int) -> T.Type:
        if ann is None:
            raise CompileError(
                f"{what} needs a type annotation (int, float or bool)",
                line, self.path)
        name = getattr(ann, "id", None) or getattr(ann, "value", None)
        if name not in _TYPES:
            raise CompileError(
                f"unsupported type {ast.unparse(ann)!r} for {what}; "
                f"this frontend understands: " + ", ".join(_TYPES),
                line, self.path)
        return _TYPES[name]

    # ── one function ────────────────────────────────────────────────────────
    def _func(self, node: ast.FunctionDef) -> Func:
        ptys, ret = self.sigs[node.name]
        fn = Func(node.name, ret)
        for i, (arg, ty) in enumerate(zip(node.args.args, ptys)):
            fn.params.append(i)
            fn.regs[i] = ty
        self.fn, self.b = fn, Builder(fn)
        self.vars: dict[str, int] = {
            a.arg: i for i, a in enumerate(node.args.args)}
        self.b.switch_to(self.b.new_block("entry"))

        for stmt in node.body:
            self._stmt(stmt)

        # A function may end without an explicit return; give it one so the
        # block is terminated. The verifier would otherwise reject it, and the
        # message would blame the block rather than the missing return.
        if self.b.current.terminator is None:
            if ret.is_void:
                self.b.ret()
            else:
                self.b.ret(self.b.const(ret, 0))
        return fn

    # ── statements ──────────────────────────────────────────────────────────
    def _stmt(self, node) -> None:
        match node:
            case ast.Expr():
                self._expr_discard(node.value)
            case ast.Assign(targets=[ast.Name(id=name)]):
                self._assign(name, node.value, node.lineno)
            case ast.AnnAssign(target=ast.Name(id=name), value=v) if v:
                ty = self._ty(node.annotation, name, node.lineno)
                if name not in self.vars:
                    self.vars[name] = self.b.reg(ty)
                self._assign(name, v, node.lineno)
            case ast.AugAssign(target=ast.Name(id=name)):
                fake = ast.BinOp(left=ast.Name(id=name, ctx=ast.Load()),
                                 op=node.op, right=node.value)
                ast.copy_location(fake, node)
                self._assign(name, fake, node.lineno)
            case ast.Return():
                if node.value is None:
                    self.b.ret()
                else:
                    v, _ = self._expr(node.value)
                    self.b.ret(v)
            case ast.If():
                self._if(node)
            case ast.While():
                self._while(node)
            case ast.For():
                self._for(node)
            case ast.Pass():
                pass
            case _:
                raise CompileError(
                    f"unsupported statement: {type(node).__name__}",
                    getattr(node, "lineno", None), self.path)

    def _assign(self, name: str, value, line: int) -> None:
        v, ty = self._expr(value)
        if name not in self.vars:
            self.vars[name] = self.b.reg(ty)
        dst = self.vars[name]
        want = self.fn.reg_type(dst)
        if want != ty:
            v = self._coerce(v, ty, want, line)
        self.b.copy(dst, v)

    def _if(self, node: ast.If) -> None:
        cond, _ = self._truth(node.test)
        then_b = self.b.new_block("then")
        else_b = self.b.new_block("else") if node.orelse else None
        join = self.b.new_block("endif")
        self.b.branch(cond, then_b, else_b or join)

        self.b.switch_to(then_b)
        for s in node.body:
            self._stmt(s)
        if self.b.current.terminator is None:
            self.b.jump(join)

        if else_b is not None:
            self.b.switch_to(else_b)
            for s in node.orelse:
                self._stmt(s)
            if self.b.current.terminator is None:
                self.b.jump(join)

        self.b.switch_to(join)

    def _while(self, node: ast.While) -> None:
        head = self.b.new_block("while")
        body = self.b.new_block("do")
        done = self.b.new_block("endwhile")
        self.b.jump(head)
        self.b.switch_to(head)
        cond, _ = self._truth(node.test)
        self.b.branch(cond, body, done)
        self.b.switch_to(body)
        for s in node.body:
            self._stmt(s)
        if self.b.current.terminator is None:
            self.b.jump(head)
        self.b.switch_to(done)

    def _for(self, node: ast.For) -> None:
        """`for x in range(...)` only -- lowered to a counted loop."""
        call = node.iter
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", "") == "range"):
            raise CompileError("only `for ... in range(...)` is supported",
                               node.lineno, self.path)
        if not isinstance(node.target, ast.Name):
            raise CompileError("loop target must be a plain name",
                               node.lineno, self.path)
        args = [self._expr(a)[0] for a in call.args]
        if len(args) == 1:
            start, stop, step = self.b.const(T.I64, 0), args[0], self.b.const(T.I64, 1)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], self.b.const(T.I64, 1)
        elif len(args) == 3:
            start, stop, step = args
        else:
            raise CompileError("range() takes 1 to 3 arguments",
                               node.lineno, self.path)

        var = self.vars.setdefault(node.target.id, self.b.reg(T.I64))
        self.b.copy(var, start)
        head = self.b.new_block("for")
        body = self.b.new_block("forbody")
        done = self.b.new_block("endfor")
        self.b.jump(head)
        self.b.switch_to(head)
        self.b.branch(self.b.cmp(Op.LT, T.I64, var, stop), body, done)
        self.b.switch_to(body)
        for s in node.body:
            self._stmt(s)
        if self.b.current.terminator is None:
            self.b.copy(var, self.b.add(T.I64, var, step))
            self.b.jump(head)
        self.b.switch_to(done)

    # ── expressions ─────────────────────────────────────────────────────────
    def _expr_discard(self, node) -> None:
        if isinstance(node, ast.Constant):      # a docstring
            return
        self._expr(node, discard=True)

    def _expr(self, node, discard: bool = False) -> tuple[int, T.Type]:
        match node:
            case ast.Constant(value=bool() as v):
                return self.b.const(T.I1, int(v)), T.I1
            case ast.Constant(value=int() as v):
                return self.b.const(T.I64, v), T.I64
            case ast.Constant(value=float() as v):
                return self.b.const(T.F64, v), T.F64
            case ast.Name(id=name):
                if name not in self.vars:
                    raise CompileError(f"undefined name {name!r}",
                                       node.lineno, self.path)
                r = self.vars[name]
                return r, self.fn.reg_type(r)
            case ast.UnaryOp(op=ast.USub()):
                v, ty = self._expr(node.operand)
                d = self.b.reg(ty)
                self.b.emit(_mk(Op.NEG, ty, d, [v]))
                return d, ty
            case ast.UnaryOp(op=ast.Not()):
                v, _ = self._truth(node.operand)
                d = self.b.reg(T.I1)
                self.b.emit(_mk(Op.XOR, T.I1, d, [v, self.b.const(T.I1, 1)]))
                return d, T.I1
            case ast.BinOp():
                return self._binop(node)
            case ast.Compare():
                return self._compare(node)
            case ast.BoolOp():
                return self._boolop(node)
            case ast.Call():
                return self._call(node, discard)
        raise CompileError(f"unsupported expression: {type(node).__name__}",
                           getattr(node, "lineno", None), self.path)

    def _binop(self, node: ast.BinOp) -> tuple[int, T.Type]:
        a, aty = self._expr(node.left)
        b, bty = self._expr(node.right)
        ty = self._unify(aty, bty, node.lineno)
        a = self._coerce(a, aty, ty, node.lineno)
        b = self._coerce(b, bty, ty, node.lineno)

        match node.op:
            case ast.Add(): op = Op.ADD
            case ast.Sub(): op = Op.SUB
            case ast.Mult(): op = Op.MUL
            case ast.Div():
                # Python's `/` is ALWAYS float, even on two ints.
                a = self._coerce(a, ty, T.F64, node.lineno)
                b = self._coerce(b, ty, T.F64, node.lineno)
                d = self.b.reg(T.F64)
                self.b.emit(_mk(Op.DIV, T.F64, d, [a, b]))
                return d, T.F64
            case ast.FloorDiv():
                return self._floordiv(a, b, ty), ty
            case ast.Mod():
                return self._floormod(a, b, ty), ty
            case ast.BitAnd(): op = Op.AND
            case ast.BitOr(): op = Op.OR
            case ast.BitXor(): op = Op.XOR
            case ast.LShift(): op = Op.SHL
            case ast.RShift(): op = Op.SHR
            case _:
                raise CompileError(
                    f"unsupported operator {type(node.op).__name__}",
                    node.lineno, self.path)
        d = self.b.reg(ty)
        self.b.emit(_mk(op, ty, d, [a, b]))
        return d, ty

    def _floordiv(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `//`: floor, not truncate.

        `Op.DIV` truncates toward zero. They differ exactly when the operands
        have opposite signs AND the division is inexact, in which case floor is
        one less. So: divide, then subtract 1 under that condition.

            -7 // 2   trunc -> -3, signs differ, rem -1 != 0  ->  -4
             7 // 2   trunc ->  3, same sign                  ->   3
        """
        if ty.is_float:
            d = self.b.reg(ty)
            self.b.emit(_mk(Op.DIV, ty, d, [a, b]))
            return d          # float floor would need a runtime call
        q = self.b.reg(ty); self.b.emit(_mk(Op.DIV, ty, q, [a, b]))
        r = self.b.reg(ty); self.b.emit(_mk(Op.REM, ty, r, [a, b]))
        zero = self.b.const(ty, 0)
        rem_nz = self.b.cmp(Op.NE, ty, r, zero)
        a_neg = self.b.cmp(Op.LT, ty, a, zero)
        b_neg = self.b.cmp(Op.LT, ty, b, zero)
        differ = self.b.reg(T.I1)
        self.b.emit(_mk(Op.XOR, T.I1, differ, [a_neg, b_neg]))
        adjust = self.b.reg(T.I1)
        self.b.emit(_mk(Op.AND, T.I1, adjust, [differ, rem_nz]))

        out = self.b.reg(ty)
        self.b.copy(out, q)
        fix = self.b.new_block("floordiv")
        join = self.b.new_block("endfloordiv")
        self.b.branch(adjust, fix, join)
        self.b.switch_to(fix)
        one = self.b.const(ty, 1)
        dec = self.b.reg(ty)
        self.b.emit(_mk(Op.SUB, ty, dec, [q, one]))
        self.b.copy(out, dec)
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _floormod(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `%` takes the sign of the DIVISOR; `Op.REM` takes the
        dividend's. Same condition as floordiv: add the divisor back."""
        if ty.is_float:
            d = self.b.reg(ty)
            self.b.emit(_mk(Op.REM, ty, d, [a, b]))
            return d
        r = self.b.reg(ty); self.b.emit(_mk(Op.REM, ty, r, [a, b]))
        zero = self.b.const(ty, 0)
        rem_nz = self.b.cmp(Op.NE, ty, r, zero)
        r_neg = self.b.cmp(Op.LT, ty, r, zero)
        b_neg = self.b.cmp(Op.LT, ty, b, zero)
        differ = self.b.reg(T.I1)
        self.b.emit(_mk(Op.XOR, T.I1, differ, [r_neg, b_neg]))
        adjust = self.b.reg(T.I1)
        self.b.emit(_mk(Op.AND, T.I1, adjust, [differ, rem_nz]))

        out = self.b.reg(ty)
        self.b.copy(out, r)
        fix = self.b.new_block("floormod")
        join = self.b.new_block("endfloormod")
        self.b.branch(adjust, fix, join)
        self.b.switch_to(fix)
        add = self.b.reg(ty)
        self.b.emit(_mk(Op.ADD, ty, add, [r, b]))
        self.b.copy(out, add)
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _compare(self, node: ast.Compare) -> tuple[int, T.Type]:
        """`a < b < c` evaluates b once and short-circuits."""
        out = self.b.reg(T.I1)
        self.b.copy(out, self.b.const(T.I1, 0))
        join = self.b.new_block("endcmp")

        left, lty = self._expr(node.left)
        for i, (opnode, right_node) in enumerate(zip(node.ops, node.comparators)):
            right, rty = self._expr(right_node)
            ty = self._unify(lty, rty, node.lineno)
            l2 = self._coerce(left, lty, ty, node.lineno)
            r2 = self._coerce(right, rty, ty, node.lineno)
            op = _CMP.get(type(opnode))
            if op is None:
                raise CompileError(
                    f"unsupported comparison {type(opnode).__name__}",
                    node.lineno, self.path)
            res = self.b.cmp(op, ty, l2, r2)
            last = i == len(node.ops) - 1
            if last:
                self.b.copy(out, res)
                self.b.jump(join)
            else:
                nxt = self.b.new_block("cmpnext")
                self.b.branch(res, nxt, join)
                self.b.switch_to(nxt)
            left, lty = right, rty
        self.b.switch_to(join)
        return out, T.I1

    def _boolop(self, node: ast.BoolOp) -> tuple[int, T.Type]:
        """`and`/`or` short-circuit and yield an OPERAND, not a bool.

        Mutable registers make this direct: one result register, written on
        whichever path runs. With SSA this would need a phi at the join.
        """
        first, ty = self._expr(node.values[0])
        out = self.b.reg(ty)
        self.b.copy(out, first)
        join = self.b.new_block("endbool")
        for operand in node.values[1:]:
            cont = self.b.new_block("boolnext")
            cond, _ = self._truth_of(out, ty)
            if isinstance(node.op, ast.And):
                self.b.branch(cond, cont, join)
            else:
                self.b.branch(cond, join, cont)
            self.b.switch_to(cont)
            v, vty = self._expr(operand)
            self.b.copy(out, self._coerce(v, vty, ty, node.lineno))
            self.b.jump(join)
            self.b.switch_to(join)
            join = self.b.new_block("endbool") if operand is not node.values[-1] else join
        return out, ty

    def _call(self, node: ast.Call, discard: bool) -> tuple[int, T.Type]:
        name = getattr(node.func, "id", None)
        if name is None:
            raise CompileError("only direct calls by name are supported",
                               node.lineno, self.path)
        if name == "print":
            for a in node.args:
                v, ty = self._expr(a)
                if ty.is_float:
                    raise CompileError("print() of a float is not supported yet",
                                       node.lineno, self.path)
                v = self._coerce(v, ty, T.I64, node.lineno)
                self.b.call(T.VOID, "print_int", [v])
            return self.b.const(T.I64, 0), T.I64
        if name not in self.sigs:
            raise CompileError(f"call to unknown function {name!r}",
                               node.lineno, self.path)
        ptys, ret = self.sigs[name]
        if len(node.args) != len(ptys):
            raise CompileError(
                f"{name}() takes {len(ptys)} argument(s), got {len(node.args)}",
                node.lineno, self.path)
        args = []
        for a, want in zip(node.args, ptys):
            v, ty = self._expr(a)
            args.append(self._coerce(v, ty, want, node.lineno))
        r = self.b.call(ret, name, args)
        if ret.is_void:
            return self.b.const(T.I64, 0), T.I64
        return r, ret

    # ── truthiness and numeric coercion ─────────────────────────────────────
    def _truth(self, node) -> tuple[int, T.Type]:
        v, ty = self._expr(node)
        return self._truth_of(v, ty)

    def _truth_of(self, v: int, ty: T.Type) -> tuple[int, T.Type]:
        if ty is T.I1:
            return v, T.I1
        return self.b.cmp(Op.NE, ty, v, self.b.const(ty, 0)), T.I1

    def _unify(self, a: T.Type, b: T.Type, line: int) -> T.Type:
        if a == b:
            return a
        if a.is_float or b.is_float:
            return T.F64
        return T.I64          # bool widens to int, as Python's does

    def _coerce(self, v: int, have: T.Type, want: T.Type, line: int) -> int:
        if have == want:
            return v
        d = self.b.reg(want)
        if have.is_float and want.is_float:
            op = Op.FTOF
        elif have.is_float:
            op = Op.FTOI
        elif want.is_float:
            op = Op.ITOF
        elif want.bits > have.bits:
            op = Op.EXTEND
        elif want.bits < have.bits:
            op = Op.TRUNC
        else:
            op = Op.BITCAST
        self.b.emit(_mk(op, want, d, [v]))
        return d


def _mk(op: Op, ty: T.Type, dst: int | None, args: list[int]):
    from ..core import Instr
    return Instr(op, ty, dst=dst, args=args)


register(PythonFrontend())
