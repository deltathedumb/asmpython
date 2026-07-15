"""Lower a deliberately growing Python syntax subset into pyinbin bytecode.

The bootstrap frontend uses ``ast`` only to parse source. It never delegates
program execution to CPython: supported statements and expressions become
``CodeObject`` instructions consumed by :mod:`asmpython.pyinbin.vm`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .bytecode import CodeObject, Instruction, Op


class PyinbinUnsupportedError(Exception):
    """Raised only when source cannot be represented by current pyinbin IR."""


_BINARY_OPS = {
    ast.Add: Op.BINARY_ADD,
    ast.Sub: Op.BINARY_SUB,
    ast.Mult: Op.BINARY_MUL,
    ast.Div: Op.BINARY_DIV,
    ast.FloorDiv: Op.BINARY_FLOORDIV,
    ast.Mod: Op.BINARY_MOD,
}
_COMPARE_OPS = {
    ast.Eq: Op.COMPARE_EQ,
    ast.Lt: Op.COMPARE_LT,
    ast.LtE: Op.COMPARE_LE,
    ast.Gt: Op.COMPARE_GT,
    ast.GtE: Op.COMPARE_GE,
}


@dataclass
class _Lowerer:
    name: str
    arg_names: list[str] = field(default_factory=list)
    constants: list[object] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    instructions: list[Instruction] = field(default_factory=list)
    loop_exits: list[list[int]] = field(default_factory=list)

    def constant(self, value: object) -> int:
        self.constants.append(value)
        return len(self.constants) - 1

    def name_index(self, value: str) -> int:
        try:
            return self.names.index(value)
        except ValueError:
            self.names.append(value)
            return len(self.names) - 1

    def emit(self, op: Op, arg: int = 0) -> int:
        self.instructions.append(Instruction(op, arg))
        return len(self.instructions) - 1

    def patch(self, offset: int, target: int) -> None:
        self.instructions[offset] = Instruction(self.instructions[offset].op, target)

    def unsupported(self, node: ast.AST, detail: str | None = None) -> None:
        kind = detail or type(node).__name__
        raise PyinbinUnsupportedError(f"line {node.lineno}: pyinbin does not support {kind}")

    def expr(self, node: ast.expr) -> None:
        if isinstance(node, ast.Constant):
            self.emit(Op.LOAD_CONST, self.constant(node.value))
        elif isinstance(node, ast.Name):
            self.emit(Op.LOAD_NAME, self.name_index(node.id))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
            self.expr(node.left)
            self.expr(node.right)
            self.emit(_BINARY_OPS[type(node.op)])
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            self.expr(node.operand)
            self.emit(Op.UNARY_NEGATIVE)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self.expr(node.operand)
            self.emit(Op.UNARY_NOT)
        elif isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1 and type(node.ops[0]) in _COMPARE_OPS:
            self.expr(node.left)
            self.expr(node.comparators[0])
            self.emit(_COMPARE_OPS[type(node.ops[0])])
        elif isinstance(node, ast.Call) and not node.keywords:
            self.expr(node.func)
            for arg in node.args:
                self.expr(arg)
            self.emit(Op.CALL, len(node.args))
        elif isinstance(node, ast.List):
            for element in node.elts:
                self.expr(element)
            self.emit(Op.BUILD_LIST, len(node.elts))
        elif isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
            for key, value in zip(node.keys, node.values):
                assert key is not None
                self.expr(key)
                self.expr(value)
            self.emit(Op.BUILD_DICT, len(node.keys))
        elif isinstance(node, ast.Attribute):
            self.expr(node.value)
            self.emit(Op.GET_ATTR, self.name_index(node.attr))
        else:
            self.unsupported(node)

    def store(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.emit(Op.STORE_NAME, self.name_index(target.id))
        else:
            self.unsupported(target, "assignment target")

    def stmt(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Expr):
            self.expr(node.value)
            self.emit(Op.POP_TOP)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            if isinstance(node.targets[0], ast.Attribute):
                self.expr(node.targets[0].value)
                self.expr(node.value)
                self.emit(Op.SET_ATTR, self.name_index(node.targets[0].attr))
            else:
                self.expr(node.value)
                self.store(node.targets[0])
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Attribute):
                self.expr(node.target.value)
                self.expr(node.value)
                self.emit(Op.SET_ATTR, self.name_index(node.target.attr))
            else:
                self.expr(node.value)
                self.store(node.target)
        elif isinstance(node, ast.Return):
            if node.value is None:
                self.emit(Op.RETURN)
            else:
                self.expr(node.value)
                self.emit(Op.RETURN)
        elif isinstance(node, ast.FunctionDef) and not node.decorator_list and not node.args.defaults:
            if node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.posonlyargs:
                self.unsupported(node, "function argument form")
            nested = _Lowerer(node.name, [arg.arg for arg in node.args.args])
            for statement in node.body:
                nested.stmt(statement)
            nested.emit(Op.RETURN)
            self.emit(Op.MAKE_FUNCTION, self.constant(nested.finish()))
            self.emit(Op.STORE_NAME, self.name_index(node.name))
        elif isinstance(node, ast.If):
            self.expr(node.test)
            otherwise = self.emit(Op.JUMP_IF_FALSE)
            for statement in node.body:
                self.stmt(statement)
            end = self.emit(Op.JUMP) if node.orelse else None
            self.patch(otherwise, len(self.instructions))
            for statement in node.orelse:
                self.stmt(statement)
            if end is not None:
                self.patch(end, len(self.instructions))
        elif isinstance(node, ast.While) and not node.orelse:
            start = len(self.instructions)
            self.expr(node.test)
            exit_jump = self.emit(Op.JUMP_IF_FALSE)
            self.loop_exits.append([])
            for statement in node.body:
                self.stmt(statement)
            self.emit(Op.JUMP, start)
            end = len(self.instructions)
            self.patch(exit_jump, end)
            for jump in self.loop_exits.pop():
                self.patch(jump, end)
        elif isinstance(node, ast.Break) and self.loop_exits:
            self.loop_exits[-1].append(self.emit(Op.JUMP))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                op = Op.IMPORT_NAME if alias.asname or "." not in alias.name else Op.IMPORT_ROOT
                self.emit(op, self.name_index(alias.name))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                if alias.name == "*":
                    self.unsupported(node, "star import")
                self.emit(Op.IMPORT_NAME, self.name_index(node.module))
                self.emit(Op.IMPORT_FROM, self.name_index(alias.name))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name))
        else:
            self.unsupported(node)

    def finish(self) -> CodeObject:
        return CodeObject(self.name, self.instructions, self.constants, self.names, self.arg_names)


def compile_source(source: str, filename: str = "<pyinbin>") -> CodeObject:
    """Parse and lower source for the portable pyinbin VM."""
    try:
        module = ast.parse(source, filename=filename, mode="exec")
    except SyntaxError as exc:
        raise PyinbinUnsupportedError(f"{filename}:{exc.lineno}: invalid Python syntax: {exc.msg}") from exc
    lowerer = _Lowerer(filename)
    for statement in module.body:
        lowerer.stmt(statement)
    return lowerer.finish()
