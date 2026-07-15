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
    ast.Pow: Op.BINARY_POW,
    ast.BitAnd: Op.BINARY_BITAND,
    ast.BitOr: Op.BINARY_BITOR,
    ast.BitXor: Op.BINARY_BITXOR,
    ast.LShift: Op.BINARY_LSHIFT,
    ast.RShift: Op.BINARY_RSHIFT,
}
_COMPARE_OPS = {
    ast.Eq: Op.COMPARE_EQ,
    ast.Lt: Op.COMPARE_LT,
    ast.LtE: Op.COMPARE_LE,
    ast.Gt: Op.COMPARE_GT,
    ast.GtE: Op.COMPARE_GE,
    ast.NotEq: Op.COMPARE_NE,
    ast.Is: Op.COMPARE_IS,
    ast.IsNot: Op.COMPARE_IS_NOT,
    ast.In: Op.COMPARE_IN,
    ast.NotIn: Op.COMPARE_NOT_IN,
}


@dataclass
class _Lowerer:
    name: str
    arg_names: list[str] = field(default_factory=list)
    constants: list[object] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    instructions: list[Instruction] = field(default_factory=list)
    loop_exits: list[list[int]] = field(default_factory=list)
    loop_starts: list[int] = field(default_factory=list)
    is_generator: bool = False
    global_names: set[str] = field(default_factory=set)

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
        raise PyinbinUnsupportedError(f"{self.name}:{node.lineno}: pyinbin does not support {kind}")

    def comprehension(self, node: ast.ListComp | ast.DictComp | ast.GeneratorExp) -> None:
        if any(generator.is_async for generator in node.generators):
            self.unsupported(node, "async comprehension")
        is_dict = isinstance(node, ast.DictComp)
        temp_name = f"__pyinbin_comp_{len(self.constants)}"
        self.emit(Op.BUILD_DICT if is_dict else Op.BUILD_LIST, 0)
        self.emit(Op.STORE_NAME, self.name_index(temp_name))

        def emit_generator(index: int) -> None:
            generator = node.generators[index]
            self.expr(generator.iter)
            self.emit(Op.GET_ITER)
            start = len(self.instructions)
            exit_jump = self.emit(Op.FOR_ITER)
            self.store_sequence(generator.target)
            filter_jumps: list[int] = []
            for condition in generator.ifs:
                self.expr(condition)
                filter_jumps.append(self.emit(Op.JUMP_IF_FALSE))
            if index + 1 < len(node.generators):
                emit_generator(index + 1)
            elif is_dict:
                self.emit(Op.LOAD_NAME, self.name_index(temp_name))
                self.expr(node.key)
                self.expr(node.value)
                self.emit(Op.SET_ITEM)
            else:
                self.emit(Op.LOAD_NAME, self.name_index(temp_name))
                self.expr(node.elt)
                self.emit(Op.LIST_APPEND)
                self.emit(Op.POP_TOP)
            continue_target = len(self.instructions)
            for jump in filter_jumps:
                self.patch(jump, continue_target)
            self.emit(Op.JUMP, start)
            self.patch(exit_jump, len(self.instructions))

        emit_generator(0)
        self.emit(Op.LOAD_NAME, self.name_index(temp_name))

    def expr(self, node: ast.expr) -> None:
        if isinstance(node, ast.Constant):
            self.emit(Op.LOAD_CONST, self.constant(node.value))
        elif isinstance(node, ast.Name):
            self.emit(Op.LOAD_NAME, self.name_index(node.id))
        elif isinstance(node, ast.NamedExpr):
            self.expr(node.value)
            self.emit(Op.DUP_TOP)
            self.store(node.target)
        elif isinstance(node, ast.Yield):
            self.is_generator = True
            if node.value is None:
                self.emit(Op.LOAD_CONST, self.constant(None))
            else:
                self.expr(node.value)
            self.emit(Op.YIELD_VALUE)
        elif isinstance(node, ast.YieldFrom):
            self.unsupported(node, "yield from")
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
        elif isinstance(node, ast.BoolOp) and len(node.values) >= 2:
            exits: list[int] = []
            for value in node.values[:-1]:
                self.expr(value)
                self.emit(Op.DUP_TOP)
                exits.append(self.emit(Op.JUMP_IF_FALSE_KEEP if isinstance(node.op, ast.And) else Op.JUMP_IF_TRUE_KEEP))
            self.expr(node.values[-1])
            for exit_jump in exits:
                self.patch(exit_jump, len(self.instructions))
        elif isinstance(node, ast.IfExp):
            self.expr(node.test)
            otherwise = self.emit(Op.JUMP_IF_FALSE)
            self.expr(node.body)
            end = self.emit(Op.JUMP)
            self.patch(otherwise, len(self.instructions))
            self.expr(node.orelse)
            self.patch(end, len(self.instructions))
        elif isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) and all(type(op) in _COMPARE_OPS for op in node.ops):
            operands = [node.left, *node.comparators]
            for index, op in enumerate(node.ops):
                self.expr(operands[index])
                self.expr(operands[index + 1])
                self.emit(_COMPARE_OPS[type(op)])
                if index:
                    self.emit(Op.BINARY_BOOL_AND)
        elif isinstance(node, ast.Call) and not node.keywords and all(not isinstance(arg, ast.Starred) for arg in node.args):
            self.expr(node.func)
            for arg in node.args:
                self.expr(arg)
            self.emit(Op.CALL, len(node.args))
        elif isinstance(node, ast.Call):
            self.expr(node.func)
            arg_specs: list[bool] = []
            for arg in node.args:
                if isinstance(arg, ast.Starred):
                    self.expr(arg.value)
                    arg_specs.append(True)
                else:
                    self.expr(arg)
                    arg_specs.append(False)
            for keyword in node.keywords:
                self.expr(keyword.value)
            names = tuple(keyword.arg for keyword in node.keywords)
            self.emit(Op.CALL_KW, self.constant((tuple(arg_specs), names)))
        elif isinstance(node, ast.Lambda):
            nested = _Lowerer("<lambda>", [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]])
            nested.posonly_names = [arg.arg for arg in node.args.posonlyargs]
            nested.kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
            nested.vararg_name = node.args.vararg.arg if node.args.vararg else None
            nested.kwarg_name = node.args.kwarg.arg if node.args.kwarg else None
            nested.expr(node.body)
            nested.emit(Op.RETURN)
            for default in node.args.defaults:
                self.expr(default)
            self.emit(Op.MAKE_FUNCTION, self.constant((nested.finish(), len(node.args.defaults), 0)))
        elif isinstance(node, ast.List):
            for element in node.elts:
                self.expr(element)
            self.emit(Op.BUILD_LIST, len(node.elts))
        elif isinstance(node, (ast.ListComp, ast.DictComp, ast.GeneratorExp)):
            self.comprehension(node)
        elif isinstance(node, ast.Tuple):
            for element in node.elts:
                self.expr(element)
            self.emit(Op.BUILD_TUPLE, len(node.elts))
        elif isinstance(node, ast.Set):
            for element in node.elts:
                self.expr(element)
            self.emit(Op.BUILD_SET, len(node.elts))
        elif isinstance(node, ast.Dict) and all(key is not None for key in node.keys):
            for key, value in zip(node.keys, node.values):
                assert key is not None
                self.expr(key)
                self.expr(value)
            self.emit(Op.BUILD_DICT, len(node.keys))
        elif isinstance(node, ast.JoinedStr):
            if not node.values:
                self.emit(Op.LOAD_CONST, self.constant(""))
            else:
                first = True
                for value in node.values:
                    if isinstance(value, ast.Constant):
                        self.emit(Op.LOAD_CONST, self.constant(value.value))
                    elif isinstance(value, ast.FormattedValue):
                        if value.format_spec is None:
                            self.emit(Op.LOAD_NAME, self.name_index("repr" if value.conversion == 114 else "str"))
                            self.expr(value.value)
                            self.emit(Op.CALL, 1)
                        else:
                            self.emit(Op.LOAD_NAME, self.name_index("format"))
                            if value.conversion in (97, 114, 115):
                                self.emit(Op.LOAD_NAME, self.name_index("repr" if value.conversion == 114 else "str"))
                                self.expr(value.value)
                                self.emit(Op.CALL, 1)
                            else:
                                self.expr(value.value)
                            self.expr(value.format_spec)
                            self.emit(Op.CALL, 2)
                    else:
                        self.unsupported(value, "formatted string value")
                    if not first:
                        self.emit(Op.BINARY_ADD)
                    first = False
        elif isinstance(node, ast.Subscript):
            self.expr(node.value)
            if isinstance(node.slice, ast.Slice):
                for bound in (node.slice.lower, node.slice.upper, node.slice.step):
                    if bound is None: self.emit(Op.LOAD_CONST, self.constant(None))
                    else: self.expr(bound)
                self.emit(Op.BUILD_SLICE)
            else:
                self.expr(node.slice)
            self.emit(Op.GET_ITEM)
        elif isinstance(node, ast.Attribute):
            self.expr(node.value)
            self.emit(Op.GET_ATTR, self.name_index(node.attr))
        else:
            self.unsupported(node)

    def store(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            op = Op.STORE_GLOBAL if target.id in self.global_names else Op.STORE_NAME
            self.emit(op, self.name_index(target.id))
        else:
            self.unsupported(target, "assignment target")

    def store_sequence(self, target: ast.expr) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            self.emit(Op.UNPACK_SEQUENCE, len(target.elts))
            for element in target.elts:
                self.store_sequence(element)
        else:
            self.store(target)

    def stmt(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Expr):
            self.expr(node.value)
            if not isinstance(node.value, ast.Yield):
                self.emit(Op.POP_TOP)
        elif isinstance(node, ast.Global):
            self.global_names.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            self.unsupported(node, "nonlocal")
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.emit(Op.DELETE_NAME, self.name_index(target.id))
                elif isinstance(target, ast.Attribute):
                    self.expr(target.value)
                    self.emit(Op.DELETE_ATTR, self.name_index(target.attr))
                elif isinstance(target, ast.Subscript) and not isinstance(target.slice, ast.Slice):
                    self.expr(target.value)
                    self.expr(target.slice)
                    self.emit(Op.DELETE_ITEM)
                else:
                    self.unsupported(target, "delete target")
        elif isinstance(node, ast.Assert):
            self.expr(node.test)
            if node.msg is not None:
                self.expr(node.msg)
                self.emit(Op.ASSERT, 1)
            else:
                self.emit(Op.ASSERT)
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
                self.expr(node.value)
                self.store_sequence(node.targets[0])
            elif len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute):
                target = node.targets[0]
                self.expr(target.value)
                self.expr(node.value)
                self.emit(Op.SET_ATTR, self.name_index(target.attr))
            elif len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript) and not isinstance(node.targets[0].slice, ast.Slice):
                target = node.targets[0]
                self.expr(target.value)
                self.expr(target.slice)
                self.expr(node.value)
                self.emit(Op.SET_ITEM)
            else:
                if any(not isinstance(target, ast.Name) for target in node.targets):
                    self.unsupported(node, "chained assignment target")
                self.expr(node.value)
                for target in node.targets[:-1]:
                    self.emit(Op.DUP_TOP)
                    self.store(target)
                self.store(node.targets[-1])
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Attribute):
                self.expr(node.target.value)
                self.expr(node.value)
                self.emit(Op.SET_ATTR, self.name_index(node.target.attr))
            else:
                self.expr(node.value)
                self.store(node.target)
        elif isinstance(node, ast.AugAssign):
            if type(node.op) not in _BINARY_OPS:
                self.unsupported(node, "augmented operator")
            if isinstance(node.target, ast.Name):
                self.expr(node.target)
                self.expr(node.value)
                self.emit(_BINARY_OPS[type(node.op)])
                self.store(node.target)
            elif isinstance(node.target, ast.Attribute):
                self.expr(node.target.value)
                self.emit(Op.DUP_TOP)
                self.emit(Op.GET_ATTR, self.name_index(node.target.attr))
                self.expr(node.value)
                self.emit(_BINARY_OPS[type(node.op)])
                self.emit(Op.SET_ATTR, self.name_index(node.target.attr))
            else:
                self.unsupported(node, "augmented assignment target")
        elif isinstance(node, ast.Return):
            if node.value is None:
                self.emit(Op.RETURN)
            else:
                self.expr(node.value)
                self.emit(Op.RETURN)
        elif isinstance(node, ast.Raise) and node.exc is not None:
            self.expr(node.exc)
            self.emit(Op.RAISE)
        elif isinstance(node, ast.Raise) and node.exc is None:
            self.emit(Op.RAISE)
        elif isinstance(node, ast.FunctionDef):
            nested = _Lowerer(node.name, [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]])
            nested.posonly_names = [arg.arg for arg in node.args.posonlyargs]
            nested.kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
            nested.vararg_name = node.args.vararg.arg if node.args.vararg else None
            nested.kwarg_name = node.args.kwarg.arg if node.args.kwarg else None
            for statement in node.body:
                nested.stmt(statement)
            nested.emit(Op.RETURN)
            for default in node.args.defaults:
                self.expr(default)
            kw_default_count = 0
            for default in node.args.kw_defaults:
                if default is None:
                    self.unsupported(node, "required keyword-only argument")
                self.expr(default)
                kw_default_count += 1
            self.emit(Op.MAKE_FUNCTION, self.constant((nested.finish(), len(node.args.defaults), kw_default_count)))
            for decorator in reversed(node.decorator_list):
                self.expr(decorator)
                self.emit(Op.SWAP)
                self.emit(Op.CALL, 1)
            self.emit(Op.STORE_NAME, self.name_index(node.name))
        elif isinstance(node, ast.ClassDef) and all(keyword.arg == "metaclass" for keyword in node.keywords):
            body = _Lowerer(f"{self.name}.{node.name}")
            for statement in node.body:
                body.stmt(statement)
            body.emit(Op.RETURN)
            for base in node.bases:
                self.expr(base)
            spec = (node.name, body.finish(), len(node.bases))
            self.emit(Op.MAKE_CLASS, self.constant(spec))
            for decorator in reversed(node.decorator_list):
                self.expr(decorator)
                self.emit(Op.SWAP)
                self.emit(Op.CALL, 1)
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
        elif isinstance(node, ast.While):
            start = len(self.instructions)
            self.expr(node.test)
            exit_jump = self.emit(Op.JUMP_IF_FALSE)
            self.loop_exits.append([])
            self.loop_starts.append(start)
            for statement in node.body:
                self.stmt(statement)
            self.loop_starts.pop()
            self.emit(Op.JUMP, start)
            else_start = len(self.instructions)
            self.patch(exit_jump, else_start)
            for statement in node.orelse:
                self.stmt(statement)
            end = len(self.instructions)
            for jump in self.loop_exits.pop():
                self.patch(jump, end)
        elif isinstance(node, ast.For):
            self.expr(node.iter)
            self.emit(Op.GET_ITER)
            start = len(self.instructions)
            exit_jump = self.emit(Op.FOR_ITER)
            self.store_sequence(node.target)
            self.loop_exits.append([])
            self.loop_starts.append(start)
            for statement in node.body:
                self.stmt(statement)
            self.loop_starts.pop()
            self.emit(Op.JUMP, start)
            else_start = len(self.instructions)
            self.patch(exit_jump, else_start)
            for statement in node.orelse:
                self.stmt(statement)
            end = len(self.instructions)
            for jump in self.loop_exits.pop():
                self.patch(jump, end)
        elif isinstance(node, ast.Try) and not node.handlers and node.finalbody and not node.orelse:
            for statement in node.body:
                self.stmt(statement)
            for statement in node.finalbody:
                self.stmt(statement)
        elif isinstance(node, ast.Try) and not node.finalbody and len(node.handlers) == 1:
            handler_jump = self.emit(Op.TRY_BEGIN)
            for statement in node.body:
                self.stmt(statement)
            self.emit(Op.TRY_END)
            for statement in node.orelse:
                self.stmt(statement)
            end_jump = self.emit(Op.JUMP)
            handler = node.handlers[0]
            self.patch(handler_jump, len(self.instructions))
            if handler.type is not None:
                if isinstance(handler.type, ast.Name):
                    expected = self.name_index(handler.type.id)
                elif isinstance(handler.type, ast.Tuple) and all(isinstance(element, ast.Name) for element in handler.type.elts):
                    expected = tuple(element.id for element in handler.type.elts)
                else:
                    self.unsupported(handler, "exception type")
                self.emit(Op.MATCH_EXCEPTION, self.constant(expected))
                # Resolve the class object from the surrounding namespace for
                # the VM's MATCH_EXCEPTION operation.
                if isinstance(handler.type, ast.Name):
                    self.constants[-1] = expected
            if handler.name:
                self.emit(Op.STORE_NAME, self.name_index(handler.name))
            else:
                self.emit(Op.POP_TOP)
            for statement in handler.body:
                self.stmt(statement)
            self.patch(end_jump, len(self.instructions))
        elif isinstance(node, ast.Pass):
            return
        elif isinstance(node, ast.Continue) and self.loop_starts:
            self.emit(Op.JUMP, self.loop_starts[-1])
        elif isinstance(node, ast.With) and len(node.items) == 1:
            item = node.items[0]
            self.expr(item.context_expr)
            self.emit(Op.WITH_ENTER)
            if item.optional_vars is not None:
                self.store_sequence(item.optional_vars)
            else:
                self.emit(Op.POP_TOP)
            for statement in node.body:
                self.stmt(statement)
            self.emit(Op.WITH_EXIT)
        elif isinstance(node, ast.Break) and self.loop_exits:
            self.loop_exits[-1].append(self.emit(Op.JUMP))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                op = Op.IMPORT_NAME if alias.asname or "." not in alias.name else Op.IMPORT_ROOT
                self.emit(op, self.name_index(alias.name))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if len(node.names) == 1 and node.names[0].name == "*":
                self.emit(Op.IMPORT_NAME, self.name_index(node.module))
                self.emit(Op.IMPORT_STAR)
                return
            for alias in node.names:
                if alias.name == "*":
                    self.unsupported(node, "star import")
                self.emit(Op.IMPORT_NAME, self.name_index(node.module))
                self.emit(Op.IMPORT_FROM, self.name_index(alias.name))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level > 0 and node.module is not None:
            for alias in node.names:
                if alias.name == "*":
                    self.unsupported(node, "star import")
                spec = (node.module, node.level, alias.name)
                self.emit(Op.IMPORT_RELATIVE_FROM, self.constant(spec))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level > 0 and node.module is None:
            for alias in node.names:
                if alias.name == "*":
                    self.unsupported(node, "star import")
                spec = ("", node.level, alias.name)
                self.emit(Op.IMPORT_RELATIVE_FROM, self.constant(spec))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name))
        else:
            self.unsupported(node)

    def finish(self) -> CodeObject:
        return CodeObject(
            self.name,
            self.instructions,
            self.constants,
            self.names,
            self.arg_names,
            list(getattr(self, "kwonly_names", [])),
            getattr(self, "vararg_name", None),
            getattr(self, "kwarg_name", None),
            list(getattr(self, "posonly_names", [])),
            self.is_generator,
        )


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
