"""Typed AST -> IR.

Runs only after analysis reported no errors, so it may assume every expression
has a real type and every name resolves. That assumption is what keeps this
file about CODE GENERATION rather than about validation -- there is not a
single "if this is None" here, and there should never be one.

WHERE PYTHON AND THE MACHINE DISAGREE

Three places, and handling them is most of what this file does:

  `//` and `%`   Python floors toward negative infinity and takes the sign of
                 the divisor. `Op.DIV` truncates toward zero and `Op.REM` takes
                 the sign of the dividend, like C and like every machine. Both
                 are corrected with a branch on "signs differ and the remainder
                 is non-zero".

  `and` / `or`   Yield an OPERAND, not a bool, and short-circuit. Lowered to
                 branches writing one shared result register -- which is
                 straightforward precisely because registers are mutable. Under
                 SSA each would need a phi.

  chained `<`    `a < b < c` evaluates `b` once. Lowered as nested branches, not
                 as two independent comparisons over a re-evaluated middle.

Paying that cost once here is the alternative to every backend owing Python's
semantics, and it is the entire argument for keeping the IR small.
"""
from __future__ import annotations

import ast

from ...diagnostics import SourceFile, Span
from ...ir import Builder, Function, Module, types as T
from ...ir.module import Instruction, Linkage
from ...ir.opcodes import Op
from .analysis import (
    BOOL, FLOAT, INT, NONE, FunctionInfo, SemType, TO_IR, int_literal, span_of,
)

_CMP_OPS = {
    ast.Eq: Op.EQ, ast.NotEq: Op.NE, ast.Lt: Op.LT,
    ast.LtE: Op.LE, ast.Gt: Op.GT, ast.GtE: Op.GE,
}

#: Runtime functions the frontend may call. A frontend decides its own runtime;
#: the IR has no I/O opcodes, so printing is a call like any other.
_RUNTIME = {"print_int": ([T.I64], T.VOID), "print_float": ([T.F64], T.VOID)}

#: `int(x)`/`float(x)`/`bool(x)`. Lowered as coercions, not calls -- there is
#: nothing to call, and emitting a call would make every backend depend on a
#: runtime for what is one instruction or none.
_CONVERSIONS = {"int": INT, "float": FLOAT, "bool": BOOL}


class Lowerer:
    def __init__(self, functions: dict[str, FunctionInfo],
                 source: "SourceFile") -> None:
        self.infos = functions
        self.source = source
        source_name = source.path.stem if source.path else "module"
        self.module = Module(name=source_name)
        self.module.metadata["frontend"] = "python"
        self.module.metadata["source"] = source.name

    def run(self) -> Module:
        for name, (params, ret) in _RUNTIME.items():
            fn = Function(name, ret, external=True, linkage=Linkage.IMPORT)
            for i, ty in enumerate(params):
                fn.params.append(i)
                fn.registers[i] = ty
            self.module.functions.append(fn)

        for info in self.infos.values():
            self.module.functions.append(self._function(info))

        # Drop runtime declarations nothing called. A module that declares an
        # import it never uses is not merely untidy: the link stage decides
        # whether to pull the runtime in by looking at what is declared, so a
        # program that only does arithmetic would still acquire a dependency
        # on stdio -- and "no runtime dependencies" would quietly stop being
        # true for every program.
        called = {ins.sym for fn in self.module.functions for b in fn.blocks
                  for ins in b.instructions if ins.op is Op.CALL}
        self.module.functions = [
            f for f in self.module.functions
            if not (f.external and f.name in _RUNTIME and f.name not in called)]
        return self.module

    # ── one function ────────────────────────────────────────────────────────
    def _function(self, info: FunctionInfo) -> Function:
        fn = Function(info.name, TO_IR[info.ret],
                      linkage=Linkage.EXPORT if info.name == "main"
                      else Linkage.INTERNAL)
        self.info, self.fn = info, fn
        self.b = Builder(fn)
        #: (continue-target, break-target) per enclosing loop. `continue` in a
        #: `for` must reach the increment, not the test, so the two targets
        #: differ and both are recorded rather than recomputed.
        self.loops: list[tuple[int, int]] = []

        for sym in info.params:
            reg = fn.new_register(TO_IR[sym.type])
            fn.params.append(reg)
            sym.register = reg

        self.b.switch_to(self.b.new_block("entry"))
        # Every local gets its register up front, so an assignment inside a
        # branch writes the same register the join reads. Allocating lazily is
        # what would need phis.
        for sym in info.locals.values():
            if sym.register is None:
                sym.register = self.b.reg(TO_IR[sym.type])

        for stmt in info.node.body:
            self._stmt(stmt)

        if self.b.current.terminator is None:
            if info.ret is NONE:
                self.b.ret()
            else:
                self.b.ret(self.b.const(TO_IR[info.ret], 0))
        return fn

    # ── statements ──────────────────────────────────────────────────────────
    def _stmt(self, node) -> None:
        self.b.span = self._span(node)
        match node:
            case ast.Expr():
                if not isinstance(node.value, ast.Constant):
                    self._expr(node.value)
            case ast.Assign(targets=[ast.Name(id=name)]):
                self._store(name, node.value)
            case ast.AnnAssign(target=ast.Name(id=name)):
                if node.value is not None:
                    self._store(name, node.value)
            case ast.AugAssign(target=ast.Name(id=name)):
                synthetic = ast.BinOp(left=ast.Name(id=name, ctx=ast.Load()),
                                      op=node.op, right=node.value)
                ast.copy_location(synthetic, node)
                # The synthetic node was never analysed, so give it the types
                # analysis recorded for its parts.
                self.info.expr_types[id(synthetic.left)] = \
                    self.info.locals[name].type
                self.info.expr_types[id(synthetic)] = self.info.locals[name].type
                self._store(name, synthetic)
            case ast.Return():
                if node.value is None:
                    self.b.ret()
                else:
                    value = self._coerce(self._expr(node.value),
                                         self._type_of(node.value), self.info.ret)
                    self.b.ret(value)
            case ast.If():
                self._if(node)
            case ast.While():
                self._while(node)
            case ast.For(target=ast.Name(id=name)):
                self._for(node, name)
            case ast.Break():
                self.b.jump(self.loops[-1][1])
            case ast.Continue():
                self.b.jump(self.loops[-1][0])
            case ast.Pass():
                pass
            case _:
                # Analysis accepted it and lowering does not know it. Silently
                # dropping the statement is the one outcome to rule out: the
                # program compiles, runs, and quietly does less than it says.
                raise AssertionError(
                    f"lowering reached statement {type(node).__name__}; "
                    f"analysis accepted something lowering does not handle")

    def _store(self, name: str, value_node) -> None:
        sym = self.info.locals[name]
        value = self._coerce(self._expr(value_node),
                             self._type_of(value_node), sym.type)
        self.b.copy(sym.register, value)

    def _if(self, node: ast.If) -> None:
        cond = self._truth(node.test)
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
        head, body, done = (self.b.new_block("while"), self.b.new_block("do"),
                            self.b.new_block("endwhile"))
        self.b.jump(head)
        self.b.switch_to(head)
        self.b.branch(self._truth(node.test), body, done)
        self.b.switch_to(body)
        self.loops.append((head, done))
        for s in node.body:
            self._stmt(s)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(head)
        self.b.switch_to(done)

    def _for(self, node: ast.For, name: str) -> None:
        call = node.iter
        args = [self._expr(a) for a in call.args]
        if len(args) == 1:
            start, stop, step = self.b.const(T.I64, 0), args[0], self.b.const(T.I64, 1)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], self.b.const(T.I64, 1)
        else:
            start, stop, step = args

        var = self.info.locals[name].register
        self.b.copy(var, start)
        # Created in the order they are emitted, and the increment gets its own
        # block: `continue` has to reach the step, not the test, or the loop
        # never advances and hangs.
        head, body, step_b, done = (self.b.new_block("for"),
                                    self.b.new_block("forbody"),
                                    self.b.new_block("forstep"),
                                    self.b.new_block("endfor"))
        self.b.jump(head)
        self.b.switch_to(head)
        # `range` counts up or down depending on the sign of the step, and the
        # test differs. With a constant step the comparison is chosen here; a
        # runtime step would need both tests and a branch, which this subset
        # does not accept.
        # Analysis guarantees a literal step, so the sign -- and therefore the
        # loop test -- is known here.
        descending = (len(args) == 3
                      and (int_literal(node.iter.args[2]) or 0) < 0)
        self.b.branch(self.b.cmp(Op.GT if descending else Op.LT, T.I64,
                                 var, stop), body, done)
        self.b.switch_to(body)
        self.loops.append((step_b, done))
        for s in node.body:
            self._stmt(s)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(step_b)
        self.b.switch_to(step_b)
        self.b.copy(var, self.b.add(T.I64, var, step))
        self.b.jump(head)
        self.b.switch_to(done)

    # ── expressions ─────────────────────────────────────────────────────────
    def _expr(self, node) -> int:
        self.b.span = self._span(node)
        match node:
            case ast.Constant(value=bool() as v):
                return self.b.const(T.I1, int(v))
            case ast.Constant(value=int() as v):
                return self.b.const(T.I64, v)
            case ast.Constant(value=float() as v):
                return self.b.const(T.F64, v)
            case ast.Name(id=name):
                return self.info.locals[name].register
            case ast.UnaryOp(op=ast.Not()):
                cond = self._truth(node.operand)
                out = self.b.reg(T.I1)
                self.b.emit(Instruction(Op.XOR, T.I1, dst=out,
                                        args=[cond, self.b.const(T.I1, 1)]))
                return out
            case ast.UnaryOp(op=ast.USub()):
                ty = TO_IR[self._type_of(node.operand)]
                out = self.b.reg(ty)
                self.b.emit(Instruction(Op.NEG, ty, dst=out,
                                        args=[self._expr(node.operand)]))
                return out
            case ast.UnaryOp(op=ast.UAdd()):
                return self._expr(node.operand)
            case ast.UnaryOp(op=ast.Invert()):
                out = self.b.reg(T.I64)
                self.b.emit(Instruction(Op.NOT, T.I64, dst=out,
                                        args=[self._expr(node.operand)]))
                return out
            case ast.BinOp():
                return self._binop(node)
            case ast.BoolOp():
                return self._boolop(node)
            case ast.Compare():
                return self._compare(node)
            case ast.Call():
                return self._call(node)
            case ast.IfExp():
                return self._ifexp(node)
        raise AssertionError(f"lowering reached {type(node).__name__}; "
                             f"analysis should have rejected it")

    def _binop(self, node: ast.BinOp) -> int:
        result = self._type_of(node)
        operand = FLOAT if FLOAT in (self._type_of(node.left),
                                     self._type_of(node.right)) else result
        if isinstance(node.op, ast.Div):
            operand = FLOAT
        ty = TO_IR[operand]
        a = self._coerce(self._expr(node.left), self._type_of(node.left), operand)
        b = self._coerce(self._expr(node.right), self._type_of(node.right), operand)

        if isinstance(node.op, ast.FloorDiv):
            return self._floor_div(a, b, ty)
        if isinstance(node.op, ast.Mod):
            return self._floor_mod(a, b, ty)
        if isinstance(node.op, ast.Pow):
            return self._pow(a, node.right.value, ty)
        op = {
            ast.Add: Op.ADD, ast.Sub: Op.SUB, ast.Mult: Op.MUL,
            ast.Div: Op.DIV, ast.BitAnd: Op.AND, ast.BitOr: Op.OR,
            ast.BitXor: Op.XOR, ast.LShift: Op.SHL, ast.RShift: Op.SHR,
        }[type(node.op)]
        out = self.b.reg(ty)
        self.b.emit(Instruction(op, ty, dst=out, args=[a, b]))
        return out

    def _pow(self, base: int, exponent: int, ty: T.Type) -> int:
        """`x ** n` for a non-negative literal n, by squaring.

        Analysis guarantees the exponent is a non-negative int literal, so
        this is a compile-time expansion: no loop, no runtime call, and no
        branch. `x ** 8` is three multiplications, not eight.

        The exponent being known is also what makes `x ** 0` correct without a
        special case at runtime -- it is simply the constant 1.
        """
        if exponent == 0:
            return self.b.const(ty, 1.0 if ty.is_float else 1)
        if exponent == 1:
            return base
        result: int | None = None
        square = base
        while exponent:
            if exponent & 1:
                if result is None:
                    result = square
                else:
                    out = self.b.reg(ty)
                    self.b.emit(Instruction(Op.MUL, ty, dst=out,
                                            args=[result, square]))
                    result = out
            exponent >>= 1
            if exponent:
                out = self.b.reg(ty)
                self.b.emit(Instruction(Op.MUL, ty, dst=out,
                                        args=[square, square]))
                square = out
        assert result is not None
        return result

    def _floor_div(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `//`. See the module docstring.

        DIV truncates toward zero; floor is one less exactly when the signs
        differ and the division is inexact.
        """
        if ty.is_float:
            out = self.b.reg(ty)
            self.b.emit(Instruction(Op.DIV, ty, dst=out, args=[a, b]))
            return out
        q = self.b.reg(ty)
        self.b.emit(Instruction(Op.DIV, ty, dst=q, args=[a, b]))
        out = self.b.reg(ty)
        self.b.copy(out, q)
        fix, join = self.b.new_block("floordiv"), self.b.new_block("endfloordiv")
        self.b.branch(self._needs_floor_fix(a, b, ty), fix, join)
        self.b.switch_to(fix)
        adjusted = self.b.reg(ty)
        self.b.emit(Instruction(Op.SUB, ty, dst=adjusted,
                                args=[q, self.b.const(ty, 1)]))
        self.b.copy(out, adjusted)
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _floor_mod(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `%` takes the sign of the DIVISOR; REM takes the dividend's."""
        r = self.b.reg(ty)
        self.b.emit(Instruction(Op.REM, ty, dst=r, args=[a, b]))
        if ty.is_float:
            return r
        out = self.b.reg(ty)
        self.b.copy(out, r)
        fix, join = self.b.new_block("floormod"), self.b.new_block("endfloormod")
        self.b.branch(self._signs_differ(r, b, ty), fix, join)
        self.b.switch_to(fix)
        adjusted = self.b.reg(ty)
        self.b.emit(Instruction(Op.ADD, ty, dst=adjusted, args=[r, b]))
        self.b.copy(out, adjusted)
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _needs_floor_fix(self, a: int, b: int, ty: T.Type) -> int:
        rem = self.b.reg(ty)
        self.b.emit(Instruction(Op.REM, ty, dst=rem, args=[a, b]))
        inexact = self.b.cmp(Op.NE, ty, rem, self.b.const(ty, 0))
        differ = self._signs_differ_raw(a, b, ty)
        out = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.AND, T.I1, dst=out, args=[differ, inexact]))
        return out

    def _signs_differ(self, r: int, b: int, ty: T.Type) -> int:
        nonzero = self.b.cmp(Op.NE, ty, r, self.b.const(ty, 0))
        differ = self._signs_differ_raw(r, b, ty)
        out = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.AND, T.I1, dst=out, args=[differ, nonzero]))
        return out

    def _signs_differ_raw(self, x: int, y: int, ty: T.Type) -> int:
        zero = self.b.const(ty, 0)
        xn = self.b.cmp(Op.LT, ty, x, zero)
        yn = self.b.cmp(Op.LT, ty, y, zero)
        out = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.XOR, T.I1, dst=out, args=[xn, yn]))
        return out

    def _boolop(self, node: ast.BoolOp) -> int:
        """`and`/`or` yield an operand and short-circuit."""
        result_ty = TO_IR[self._type_of(node)]
        out = self.b.reg(result_ty)
        first = self._coerce(self._expr(node.values[0]),
                             self._type_of(node.values[0]),
                             self._type_of(node))
        self.b.copy(out, first)
        done = self.b.new_block("endbool")
        for operand in node.values[1:]:
            cont = self.b.new_block("boolnext")
            cond = self._truth_of(out, self._type_of(node))
            if isinstance(node.op, ast.And):
                self.b.branch(cond, cont, done)
            else:
                self.b.branch(cond, done, cont)
            self.b.switch_to(cont)
            value = self._coerce(self._expr(operand), self._type_of(operand),
                                 self._type_of(node))
            self.b.copy(out, value)
            self.b.jump(done)
            self.b.switch_to(done)
            done = self.b.new_block("endbool") if operand is not node.values[-1] else done
        return out

    def _compare(self, node: ast.Compare) -> int:
        """Chained comparison, evaluating each operand once."""
        out = self.b.reg(T.I1)
        self.b.copy(out, self.b.const(T.I1, 0))
        join = self.b.new_block("endcmp")

        left_v, left_t = self._expr(node.left), self._type_of(node.left)
        for i, (op_node, right_node) in enumerate(zip(node.ops, node.comparators)):
            right_v, right_t = self._expr(right_node), self._type_of(right_node)
            operand = FLOAT if FLOAT in (left_t, right_t) else INT
            ty = TO_IR[operand]
            res = self.b.cmp(_CMP_OPS[type(op_node)], ty,
                             self._coerce(left_v, left_t, operand),
                             self._coerce(right_v, right_t, operand))
            if i == len(node.ops) - 1:
                self.b.copy(out, res)
                self.b.jump(join)
            else:
                nxt = self.b.new_block("cmpnext")
                self.b.branch(res, nxt, join)
                self.b.switch_to(nxt)
            left_v, left_t = right_v, right_t
        self.b.switch_to(join)
        return out

    def _ifexp(self, node: ast.IfExp) -> int:
        ty = TO_IR[self._type_of(node)]
        out = self.b.reg(ty)
        cond = self._truth(node.test)
        then_b, else_b, join = (self.b.new_block("ifexp"),
                                self.b.new_block("elsexp"),
                                self.b.new_block("endifexp"))
        self.b.branch(cond, then_b, else_b)
        self.b.switch_to(then_b)
        self.b.copy(out, self._coerce(self._expr(node.body),
                                      self._type_of(node.body),
                                      self._type_of(node)))
        self.b.jump(join)
        self.b.switch_to(else_b)
        self.b.copy(out, self._coerce(self._expr(node.orelse),
                                      self._type_of(node.orelse),
                                      self._type_of(node)))
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _call(self, node: ast.Call) -> int:
        name = node.func.id
        if name == "print":
            for arg in node.args:
                ty = self._type_of(arg)
                value = self._expr(arg)
                if ty is FLOAT:
                    self.b.call(T.VOID, "print_float", [value])
                else:
                    self.b.call(T.VOID, "print_int",
                                [self._coerce(value, ty, INT)])
            return self.b.const(T.I64, 0)

        if name in _CONVERSIONS and name not in self.infos:
            arg = node.args[0]
            return self._coerce(self._expr(arg), self._type_of(arg),
                                _CONVERSIONS[name])

        info = self.infos[name]
        params, ret = info.signature
        args = [self._coerce(self._expr(a), self._type_of(a), want)
                for a, want in zip(node.args, params)]
        result = self.b.call(TO_IR[ret], name, args)
        return result if result is not None else self.b.const(T.I64, 0)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _truth(self, node) -> int:
        return self._truth_of(self._expr(node), self._type_of(node))

    def _truth_of(self, value: int, ty: SemType) -> int:
        if ty is BOOL:
            return value
        ir_ty = TO_IR[ty]
        return self.b.cmp(Op.NE, ir_ty, value, self.b.const(ir_ty, 0))

    def _coerce(self, value: int, have: SemType, want: SemType) -> int:
        if have == want:
            return value
        # `bool(x)` is `x != 0`, NOT a narrowing conversion. Truncating 2 to
        # one bit gives 0, so a numeric cast here would make `bool(2)` False
        # and `bool(2.5)` whatever the low bit of the truncation happened to
        # be -- wrong, and wrong in a way that only shows on even numbers.
        if want is BOOL:
            return self._truth_of(value, have)
        src, dst = TO_IR[have], TO_IR[want]
        if src == dst:
            return value
        out = self.b.reg(dst)
        if src.is_float and dst.is_float:
            op = Op.FTOF
        elif src.is_float:
            op = Op.FTOI
        elif dst.is_float:
            op = Op.ITOF
        elif dst.bits > src.bits:
            op = Op.EXTEND
        else:
            op = Op.TRUNC
        self.b.emit(Instruction(op, dst, dst=out, args=[value]))
        return out

    def _type_of(self, node) -> SemType:
        return self.info.expr_types.get(id(node), INT)

    def _span(self, node) -> Span:
        """Where this node came from.

        Uses the same `span_of` analysis uses, so a diagnostic and the
        instruction it describes point at exactly the same bytes. An earlier
        version of this returned the builder's sticky span for everything,
        which meant every instruction in a function claimed to come from
        whatever statement was lowered last -- invisible until a backend
        reported an error and pointed at the wrong line.
        """
        return span_of(self.source, node) if hasattr(node, "lineno") \
            else self.b.span
