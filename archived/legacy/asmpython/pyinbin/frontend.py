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
    ast.MatMult: Op.BINARY_MATMUL,
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


def _defer_annotation(node: ast.AST, force: bool = False) -> bool:
    """Keep annotations referring to type-checking-only names as strings."""
    return force or any(
        isinstance(item, ast.Name) and item.id in {"ClassVar", "Self", "IO"}
        for item in ast.walk(node)
    )


@dataclass
class _Lowerer:
    name: str
    arg_names: list[str] = field(default_factory=list)
    constants: list[object] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    instructions: list[Instruction] = field(default_factory=list)
    loop_exits: list[list[int]] = field(default_factory=list)
    loop_starts: list[int] = field(default_factory=list)
    # Whether each active loop (parallel stack to loop_exits/loop_starts)
    # left an iterator on the value stack that a ``break`` needs to pop
    # before jumping out -- ``for`` loops keep their GET_ITER result on the
    # stack across iterations (FOR_ITER's own normal-exhaustion path pops
    # it before falling through), but ``break``'s raw JUMP skipped that pop
    # entirely, leaving a stale iterator on the stack that the next FOR_ITER
    # up the nesting (if any) would then wrongly treat as its own.
    loop_needs_pop: list[bool] = field(default_factory=list)
    is_generator: bool = False
    is_coroutine: bool = False
    is_async_generator: bool = False
    global_names: set[str] = field(default_factory=set)
    nonlocal_names: set[str] = field(default_factory=set)
    bound_names: set[str] = field(default_factory=set)
    free_names: set[str] = field(default_factory=set)
    is_function: bool = False
    defer_annotations: bool = False
    interactive: bool = False
    _comp_counter: list[int] = field(default_factory=lambda: [0])

    def __post_init__(self) -> None:
        self.bound_names.update(self.arg_names)

    def next_comp_temp(self) -> str:
        """A name guaranteed unique across nested comprehensions in this
        function body, even when compiling one comprehension's later
        ``for`` clause's iterable expression recursively compiles another
        comprehension (e.g. ``[(i, s) for i in nums for s in [f for f in
        strs]]``). Deriving the temp name from ``len(self.constants)``
        instead (the previous approach) could produce the exact same name
        for both the outer and inner comprehension whenever no new
        constant happened to be added in between, since both share the
        same constants list -- the inner's accumulator writes then landed
        on the outer's own accumulator variable mid-iteration, corrupting
        it in a way that could keep ``FOR_ITER`` from ever terminating.
        """
        self._comp_counter[0] += 1
        return f"__pyinbin_comp_{self._comp_counter[0]}"

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
        if op is Op.STORE_NAME and 0 <= arg < len(self.names):
            self.bound_names.add(self.names[arg])
        return len(self.instructions) - 1

    def patch(self, offset: int, target: int) -> None:
        self.instructions[offset] = Instruction(self.instructions[offset].op, target)

    def unsupported(self, node: ast.AST, detail: str | None = None) -> None:
        kind = detail or type(node).__name__
        raise PyinbinUnsupportedError(f"{self.name}:{node.lineno}: pyinbin does not support {kind}")

    def comprehension(self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp) -> None:
        # Async comprehensions share the same lowering surface during bootstrap;
        # the native VM will supply awaitable iteration semantics later.
        if isinstance(node, ast.GeneratorExp):
            nested = _Lowerer(f"{self.name}.<genexpr>", ["__pyinbin_iter"])
            nested.is_function = True
            nested.is_generator = True

            def emit_nested(index: int) -> None:
                generator = node.generators[index]
                nested.expr(generator.iter)
                nested.emit(Op.GET_ITER)
                start = len(nested.instructions)
                exit_jump = nested.emit(Op.FOR_ITER)
                nested.store_sequence(generator.target)
                filter_jumps: list[int] = []
                for condition in generator.ifs:
                    nested.expr(condition)
                    filter_jumps.append(nested.emit(Op.JUMP_IF_FALSE))
                if index + 1 < len(node.generators):
                    emit_nested(index + 1)
                else:
                    nested.expr(node.elt)
                    nested.emit(Op.YIELD_VALUE)
                continue_target = len(nested.instructions)
                for jump in filter_jumps:
                    nested.patch(jump, continue_target)
                nested.emit(Op.JUMP, start)
                nested.patch(exit_jump, len(nested.instructions))

            nested.emit(Op.LOAD_NAME, nested.name_index("__pyinbin_iter"))
            nested.emit(Op.GET_ITER)
            start = len(nested.instructions)
            exit_jump = nested.emit(Op.FOR_ITER)
            nested.store_sequence(node.generators[0].target)
            filter_jumps: list[int] = []
            for condition in node.generators[0].ifs:
                nested.expr(condition)
                filter_jumps.append(nested.emit(Op.JUMP_IF_FALSE))
            if len(node.generators) > 1:
                emit_nested(1)
            else:
                nested.expr(node.elt)
                nested.emit(Op.YIELD_VALUE)
            continue_target = len(nested.instructions)
            for jump in filter_jumps:
                nested.patch(jump, continue_target)
            nested.emit(Op.JUMP, start)
            nested.patch(exit_jump, len(nested.instructions))
            nested.emit(Op.RETURN)

            if self.is_function:
                outer_bound = (
                    self.bound_names
                    | set(self.arg_names)
                    | set(getattr(self, "kwonly_names", []))
                    | ({getattr(self, "vararg_name")} if getattr(self, "vararg_name", None) else set())
                    | ({getattr(self, "kwarg_name")} if getattr(self, "kwarg_name", None) else set())
                )
                nested.free_names.update(
                    (set(nested.names) - nested.bound_names - nested.global_names)
                    & outer_bound
                )

            self.expr(node.generators[0].iter)
            self.emit(Op.GET_ITER)
            self.emit(Op.MAKE_FUNCTION, self.constant(nested.finish()))
            self.emit(Op.SWAP)
            self.emit(Op.CALL, 1)
            return
        is_dict = isinstance(node, ast.DictComp)
        is_set = isinstance(node, ast.SetComp)
        temp_name = self.next_comp_temp()
        self.emit(Op.BUILD_DICT if is_dict else Op.BUILD_SET if is_set else Op.BUILD_LIST, 0)
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
            elif is_set:
                self.emit(Op.LOAD_NAME, self.name_index(temp_name))
                self.expr(node.elt)
                self.emit(Op.SET_ADD)
                self.emit(Op.POP_TOP)
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
        elif isinstance(node, ast.Slice):
            self.slice_expr(node)
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
            self.is_generator = True
            self.expr(node.value)
            self.emit(Op.GET_ITER)
            start = len(self.instructions)
            exit_jump = self.emit(Op.FOR_ITER)
            self.emit(Op.YIELD_VALUE)
            self.emit(Op.JUMP, start)
            self.patch(exit_jump, len(self.instructions))
            self.emit(Op.LOAD_CONST, self.constant(None))
        elif isinstance(node, ast.Await):
            self.expr(node.value)
            self.emit(Op.AWAIT)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
            self.expr(node.left)
            self.expr(node.right)
            self.emit(_BINARY_OPS[type(node.op)])
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            self.expr(node.operand)
            self.emit(Op.UNARY_NEGATIVE)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            self.expr(node.operand)
            self.emit(Op.UNARY_POSITIVE)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            self.expr(node.operand)
            self.emit(Op.UNARY_INVERT)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self.expr(node.operand)
            self.emit(Op.UNARY_NOT)
        elif isinstance(node, ast.BoolOp) and len(node.values) >= 2:
            exits: list[int] = []
            for value in node.values[:-1]:
                self.expr(value)
                self.emit(Op.DUP_TOP)
                exits.append(self.emit(Op.JUMP_IF_FALSE_KEEP if isinstance(node.op, ast.And) else Op.JUMP_IF_TRUE_KEEP))
                # Discard the prior operand when evaluation continues; the
                # jump path keeps the short-circuit result on the stack.
                self.emit(Op.POP_TOP)
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
            nested.is_function = True
            nested.parent_bound_names = (
                set(getattr(self, "parent_bound_names", set()))
                | set(self.bound_names)
                | set(self.arg_names)
                | set(getattr(self, "kwonly_names", []))
                | ({getattr(self, "vararg_name")} if getattr(self, "vararg_name", None) else set())
                | ({getattr(self, "kwarg_name")} if getattr(self, "kwarg_name", None) else set())
            )
            nested.posonly_names = [arg.arg for arg in node.args.posonlyargs]
            nested.kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
            nested.vararg_name = node.args.vararg.arg if node.args.vararg else None
            nested.kwarg_name = node.args.kwarg.arg if node.args.kwarg else None
            nested.bound_names.update(nested.kwonly_names)
            if nested.vararg_name:
                nested.bound_names.add(nested.vararg_name)
            if nested.kwarg_name:
                nested.bound_names.add(nested.kwarg_name)
            nested.expr(node.body)
            outer_bound = set(getattr(nested, "parent_bound_names", set()))
            nested.free_names.update(
                (set(nested.names) - nested.bound_names - nested.global_names) & outer_bound
            )
            if self.is_function:
                self.free_names.update(
                    name for name in nested.free_names
                    if name not in self.bound_names and name not in self.global_names
                )
            nested.emit(Op.RETURN)
            for default in node.args.defaults:
                self.expr(default)
            self.emit(Op.MAKE_FUNCTION, self.constant((nested.finish(), len(node.args.defaults), 0)))
        elif isinstance(node, ast.List):
            for element in node.elts:
                self.expr(element.value if isinstance(element, ast.Starred) else element)
            if any(isinstance(element, ast.Starred) for element in node.elts):
                flags = sum((1 << index) for index, element in enumerate(node.elts) if isinstance(element, ast.Starred))
                self.emit(Op.BUILD_LIST_UNPACK, len(node.elts) | (flags << 16))
            else:
                self.emit(Op.BUILD_LIST, len(node.elts))
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            self.comprehension(node)
        elif isinstance(node, ast.Tuple):
            for element in node.elts:
                self.expr(element.value if isinstance(element, ast.Starred) else element)
            if any(isinstance(element, ast.Starred) for element in node.elts):
                flags = sum((1 << index) for index, element in enumerate(node.elts) if isinstance(element, ast.Starred))
                self.emit(Op.BUILD_TUPLE_UNPACK, len(node.elts) | (flags << 16))
            else:
                self.emit(Op.BUILD_TUPLE, len(node.elts))
        elif isinstance(node, ast.Set):
            for element in node.elts:
                self.expr(element.value if isinstance(element, ast.Starred) else element)
            if any(isinstance(element, ast.Starred) for element in node.elts):
                flags = sum((1 << index) for index, element in enumerate(node.elts) if isinstance(element, ast.Starred))
                self.emit(Op.BUILD_SET_UNPACK, len(node.elts) | (flags << 16))
            else:
                self.emit(Op.BUILD_SET, len(node.elts))
        elif isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                flags = 0
                for index, (key, value) in enumerate(zip(node.keys, node.values)):
                    if key is None:
                        flags |= 1 << index
                        self.expr(value)
                    else:
                        self.expr(key)
                        self.expr(value)
                        self.emit(Op.BUILD_TUPLE, 2)
                self.emit(Op.BUILD_DICT_UNPACK, len(node.keys) | (flags << 16))
            else:
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
                            converter = ascii if value.conversion == 97 else repr if value.conversion == 114 else str
                            self.emit(Op.LOAD_CONST, self.constant(converter))
                            self.expr(value.value)
                            self.emit(Op.CALL, 1)
                        else:
                            self.emit(Op.LOAD_NAME, self.name_index("format"))
                            if value.conversion in (97, 114, 115):
                                converter = ascii if value.conversion == 97 else repr if value.conversion == 114 else str
                                self.emit(Op.LOAD_CONST, self.constant(converter))
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
        elif hasattr(ast, "TemplateStr") and isinstance(node, ast.TemplateStr):
            self.emit(Op.LOAD_NAME, self.name_index("Template"))
            for value in node.values:
                if isinstance(value, ast.Constant):
                    self.emit(Op.LOAD_CONST, self.constant(value.value))
                elif isinstance(value, ast.Interpolation):
                    self.emit(Op.LOAD_NAME, self.name_index("Interpolation"))
                    self.expr(value.value)
                    expression = getattr(value, "str", None) or ast.unparse(value.value)
                    self.emit(Op.LOAD_CONST, self.constant(expression))
                    conversion = {97: "a", 114: "r", 115: "s"}.get(value.conversion)
                    self.emit(Op.LOAD_CONST, self.constant(conversion))
                    if value.format_spec is None:
                        self.emit(Op.LOAD_CONST, self.constant(""))
                    else:
                        self.emit(Op.LOAD_NAME, self.name_index("str"))
                        self.expr(value.format_spec)
                        self.emit(Op.CALL, 1)
                    self.emit(Op.CALL, 4)
                else:
                    self.unsupported(value, "template string value")
            self.emit(Op.CALL, len(node.values))
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

    def slice_expr(self, node: ast.expr) -> None:
        if isinstance(node, ast.Slice):
            for bound in (node.lower, node.upper, node.step):
                if bound is None:
                    self.emit(Op.LOAD_CONST, self.constant(None))
                else:
                    self.expr(bound)
            self.emit(Op.BUILD_SLICE)
        else:
            self.expr(node)

    def store_sequence(self, target: ast.expr) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            starred = [index for index, element in enumerate(target.elts) if isinstance(element, ast.Starred)]
            if len(starred) > 1:
                self.unsupported(target, "multiple starred assignment targets")
            if starred:
                index = starred[0]
                self.emit(Op.UNPACK_EX, index | ((len(target.elts) - index - 1) << 16))
            else:
                self.emit(Op.UNPACK_SEQUENCE, len(target.elts))
            for element in target.elts:
                self.store_sequence(element.value if isinstance(element, ast.Starred) else element)
        else:
            self.store_value_target(target)

    def store_value_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.store(target)
        elif isinstance(target, ast.Attribute):
            temp_name = f"__pyinbin_store_{len(self.instructions)}"
            self.emit(Op.STORE_NAME, self.name_index(temp_name))
            self.expr(target.value)
            self.emit(Op.LOAD_NAME, self.name_index(temp_name))
            self.emit(Op.SET_ATTR, self.name_index(target.attr))
        elif isinstance(target, ast.Subscript):
            temp_name = f"__pyinbin_store_{len(self.instructions)}"
            self.emit(Op.STORE_NAME, self.name_index(temp_name))
            object_name = f"__pyinbin_object_{len(self.instructions)}"
            self.expr(target.value)
            self.emit(Op.STORE_NAME, self.name_index(object_name))
            index_name = f"__pyinbin_index_{len(self.instructions)}"
            self.slice_expr(target.slice)
            self.emit(Op.STORE_NAME, self.name_index(index_name))
            self.emit(Op.LOAD_NAME, self.name_index(object_name))
            self.emit(Op.LOAD_NAME, self.name_index(index_name))
            self.emit(Op.LOAD_NAME, self.name_index(temp_name))
            self.emit(Op.SET_ITEM)
        else:
            self.unsupported(target, "assignment target")

    def exception_spec(self, node: ast.expr) -> object:
        if isinstance(node, ast.Constant):
            # Keep invalid except/except* operands until runtime so CPython's
            # TypeError contract is preserved instead of rejecting valid test
            # programs during lowering.
            return ("literal", node.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "type" and len(node.args) == 1
                and isinstance(node.args[0], ast.Name) and not node.keywords):
            return ("type_of", self.name_index(node.args[0].id))
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "Signals" and isinstance(node.slice, ast.Attribute)
                and isinstance(node.slice.value, ast.Name)):
            return ("subscript_attr", self.name_index(node.value.id),
                    self.name_index(node.slice.value.id), node.slice.attr)
        if isinstance(node, ast.Name):
            return self.name_index(node.id)
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, (ast.Name, ast.Attribute)):
                self.unsupported(node, "exception type")
            return ("attr", self.exception_spec(node.value), node.attr)
        if isinstance(node, ast.Tuple):
            return tuple(self.exception_spec(element) for element in node.elts)
        # Any other expression (e.g. ``except ftperrors():`` -- a function
        # call whose return value is the actual exception type/tuple to
        # match, evaluated fresh each time the handler is reached, exactly
        # like real Python) -- compile it as a tiny nested code object
        # sharing this frame's globals/closure, evaluated by
        # _resolve_exception_spec at runtime instead of requiring the type
        # expression to be statically resolvable at compile time.
        nested = _Lowerer(f"{self.name}.<except-expr>")
        nested.expr(node)
        nested.emit(Op.RETURN)
        return ("expr", nested.finish())

    def pattern_spec(self, node: ast.pattern) -> object:
        if isinstance(node, ast.MatchAs):
            inner = self.pattern_spec(node.pattern) if node.pattern is not None else None
            return ("bind", inner, node.name)
        if isinstance(node, ast.MatchStar):
            return ("star", self.pattern_spec(ast.MatchAs(name=node.name)))
        if isinstance(node, ast.MatchValue):
            if isinstance(node.value, ast.Constant):
                # Must wrap as ("literal", value), not the bare value --
                # _resolve_exception_spec (which _match_pattern's "value"
                # case delegates to) treats any plain int as an index into
                # frame.code.names, since that function's original purpose
                # was resolving *named* exception-type specs. A bare literal
                # like `case 1:` was being silently reinterpreted as "look
                # up code.names[1]" instead of the literal 1, matching
                # whatever name happened to sit at that index (which could
                # easily be the match subject's own temp variable).
                return ("value", ("literal", node.value.value))
            return ("value", self.exception_spec(node.value))
        if isinstance(node, ast.MatchSingleton):
            return ("singleton", node.value)
        if isinstance(node, ast.MatchOr):
            return ("or", tuple(self.pattern_spec(pattern) for pattern in node.patterns))
        if isinstance(node, ast.MatchSequence):
            return ("sequence", tuple(self.pattern_spec(pattern) for pattern in node.patterns))
        if isinstance(node, ast.MatchMapping):
            pairs = []
            for key, pattern in zip(node.keys, node.patterns):
                if isinstance(key, ast.Constant):
                    key_spec = ("literal", key.value)
                else:
                    # A mapping-pattern key need not be a bare literal (e.g.
                    # ``case {-0-0j: ...}:`` -- a unary-negated complex
                    # literal parses as a BinOp/UnaryOp, not ast.Constant).
                    # Real Python evaluates the key expression at match time;
                    # do the same via a tiny nested code object instead of
                    # rejecting anything not already a plain constant.
                    nested = _Lowerer(f"{self.name}.<pattern-key>")
                    nested.expr(key)
                    nested.emit(Op.RETURN)
                    key_spec = ("expr", nested.finish())
                pairs.append((key_spec, self.pattern_spec(pattern)))
            return ("mapping", tuple(pairs), node.rest)
        if isinstance(node, ast.MatchClass):
            cls = self.exception_spec(node.cls)
            positional = tuple(self.pattern_spec(pattern) for pattern in node.patterns)
            keywords = tuple((name, self.pattern_spec(pattern)) for name, pattern in zip(node.kwd_attrs, node.kwd_patterns))
            return ("class", cls, positional, keywords)
        self.unsupported(node, "match pattern")
        return ("wildcard",)

    def stmt(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Expr):
            self.expr(node.value)
            if not self.interactive and not isinstance(node.value, ast.Yield):
                self.emit(Op.POP_TOP)
        elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            self.expr(node.value)
            self.store(node.name)
        elif isinstance(node, ast.Global):
            self.global_names.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            self.nonlocal_names.update(node.names)
            self.free_names.update(node.names)
        elif isinstance(node, ast.Delete):
            def delete_target(target: ast.expr) -> None:
                if isinstance(target, ast.Name):
                    self.emit(Op.DELETE_NAME, self.name_index(target.id))
                elif isinstance(target, ast.Attribute):
                    self.expr(target.value)
                    self.emit(Op.DELETE_ATTR, self.name_index(target.attr))
                elif isinstance(target, ast.Subscript):
                    self.expr(target.value)
                    self.slice_expr(target.slice)
                    self.emit(Op.DELETE_ITEM)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for item in target.elts:
                        delete_target(item)
                else:
                    self.unsupported(target, "delete target")
            for target in node.targets:
                delete_target(target)
        elif isinstance(node, ast.Assert):
            self.expr(node.test)
            if node.msg is not None:
                self.expr(node.msg)
                self.emit(Op.ASSERT, 1)
            else:
                self.emit(Op.ASSERT)
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                # The common case: a single plain-name target. Store directly
                # instead of going through the multi-target fallback below,
                # which stashes the value under a synthetic
                # ``__pyinbin_assign_N`` name first -- harmless for ordinary
                # code, but that extra binding leaks into ``cls.__dict__``
                # for class-body assignments and confuses anything that
                # enumerates it (e.g. ``enum``'s member collection).
                self.expr(node.value)
                self.store(node.targets[0])
            elif len(node.targets) == 1 and isinstance(node.targets[0], (ast.Tuple, ast.List)):
                self.expr(node.value)
                self.store_sequence(node.targets[0])
            elif len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute):
                target = node.targets[0]
                self.expr(target.value)
                self.expr(node.value)
                self.emit(Op.SET_ATTR, self.name_index(target.attr))
            elif len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript):
                target = node.targets[0]
                self.expr(target.value)
                self.slice_expr(target.slice)
                self.expr(node.value)
                self.emit(Op.SET_ITEM)
            else:
                temp_name = f"__pyinbin_assign_{len(self.instructions)}"
                self.expr(node.value)
                self.emit(Op.STORE_NAME, self.name_index(temp_name))
                for target in node.targets:
                    self.emit(Op.LOAD_NAME, self.name_index(temp_name))
                    if isinstance(target, ast.Name):
                        self.store(target)
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        self.store_sequence(target)
                    elif isinstance(target, ast.Attribute):
                        self.expr(target.value)
                        self.emit(Op.SWAP)
                        self.emit(Op.SET_ATTR, self.name_index(target.attr))
                    elif isinstance(target, ast.Subscript):
                        object_name = f"__pyinbin_object_{len(self.instructions)}"
                        index_name = f"__pyinbin_index_{len(self.instructions)}"
                        self.expr(target.value)
                        self.emit(Op.STORE_NAME, self.name_index(object_name))
                        self.slice_expr(target.slice)
                        self.emit(Op.STORE_NAME, self.name_index(index_name))
                        self.emit(Op.LOAD_NAME, self.name_index(object_name))
                        self.emit(Op.LOAD_NAME, self.name_index(index_name))
                        self.emit(Op.LOAD_NAME, self.name_index(temp_name))
                        self.emit(Op.SET_ITEM)
                    else:
                        self.unsupported(target, "assignment target")
        elif isinstance(node, ast.AnnAssign) and node.value is None:
            if isinstance(node.target, ast.Name) and not self.is_function:
                self.emit(Op.LOAD_NAME, self.name_index("__annotations__"))
                self.emit(Op.LOAD_CONST, self.constant(node.target.id))
                if _defer_annotation(node.annotation, self.defer_annotations):
                    self.emit(Op.LOAD_CONST, self.constant(ast.unparse(node.annotation)))
                else:
                    self.expr(node.annotation)
                self.emit(Op.SET_ITEM)
            return
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and not self.is_function:
                self.emit(Op.LOAD_NAME, self.name_index("__annotations__"))
                self.emit(Op.LOAD_CONST, self.constant(node.target.id))
                if _defer_annotation(node.annotation, self.defer_annotations):
                    self.emit(Op.LOAD_CONST, self.constant(ast.unparse(node.annotation)))
                else:
                    self.expr(node.annotation)
                self.emit(Op.SET_ITEM)
            if isinstance(node.target, ast.Attribute):
                self.expr(node.target.value)
                self.expr(node.value)
                self.emit(Op.SET_ATTR, self.name_index(node.target.attr))
            elif isinstance(node.target, ast.Subscript):
                self.expr(node.target.value)
                self.slice_expr(node.target.slice)
                self.expr(node.value)
                self.emit(Op.SET_ITEM)
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
            elif isinstance(node.target, ast.Subscript):
                object_name = f"__pyinbin_aug_object_{len(self.instructions)}"
                index_name = f"__pyinbin_aug_index_{len(self.instructions)}"
                value_name = f"__pyinbin_aug_value_{len(self.instructions)}"
                self.expr(node.target.value)
                self.emit(Op.STORE_NAME, self.name_index(object_name))
                self.slice_expr(node.target.slice)
                self.emit(Op.STORE_NAME, self.name_index(index_name))
                self.emit(Op.LOAD_NAME, self.name_index(object_name))
                self.emit(Op.LOAD_NAME, self.name_index(index_name))
                self.emit(Op.GET_ITEM)
                self.expr(node.value)
                self.emit(_BINARY_OPS[type(node.op)])
                self.emit(Op.STORE_NAME, self.name_index(value_name))
                self.emit(Op.LOAD_NAME, self.name_index(object_name))
                self.emit(Op.LOAD_NAME, self.name_index(index_name))
                self.emit(Op.LOAD_NAME, self.name_index(value_name))
                self.emit(Op.SET_ITEM)
            else:
                self.unsupported(node, "augmented assignment target")
        elif isinstance(node, ast.Return):
            if node.value is None:
                self.emit(Op.RETURN)
            else:
                self.expr(node.value)
                self.emit(Op.RETURN)
        elif isinstance(node, ast.Raise) and node.exc is not None and node.cause is not None:
            self.expr(node.exc)
            self.expr(node.cause)
            self.emit(Op.RAISE_FROM)
        elif isinstance(node, ast.Raise) and node.exc is not None:
            self.expr(node.exc)
            self.emit(Op.RAISE)
        elif isinstance(node, ast.Raise) and node.exc is None:
            self.emit(Op.RAISE)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nested = _Lowerer(node.name, [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]])
            nested.parent_bound_names = (
                set(getattr(self, "parent_bound_names", set()))
                | set(self.bound_names)
                | set(self.arg_names)
                | set(getattr(self, "kwonly_names", []))
                | ({getattr(self, "vararg_name")} if getattr(self, "vararg_name", None) else set())
                | ({getattr(self, "kwarg_name")} if getattr(self, "kwarg_name", None) else set())
            )
            nested.defer_annotations = self.defer_annotations
            nested.is_function = True
            nested.is_coroutine = isinstance(node, ast.AsyncFunctionDef)
            nested.is_async_generator = (
                nested.is_coroutine
                and any(isinstance(item, (ast.Yield, ast.YieldFrom)) for item in ast.walk(node))
            )
            nested.posonly_names = [arg.arg for arg in node.args.posonlyargs]
            nested.kwonly_names = [arg.arg for arg in node.args.kwonlyargs]
            nested.vararg_name = node.args.vararg.arg if node.args.vararg else None
            nested.kwarg_name = node.args.kwarg.arg if node.args.kwarg else None
            for statement in node.body:
                nested.stmt(statement)
            if self.is_function:
                outer_bound = (
                    self.bound_names
                    | set(self.arg_names)
                    | set(getattr(self, "kwonly_names", []))
                    | ({getattr(self, "vararg_name")} if getattr(self, "vararg_name", None) else set())
                    | ({getattr(self, "kwarg_name")} if getattr(self, "kwarg_name", None) else set())
                    | set(getattr(self, "parent_bound_names", set()))
                )
                nested.free_names.update(
                    (set(nested.names) - nested.bound_names - nested.global_names)
                    & outer_bound
                )
                self.free_names.update(
                    name for name in nested.free_names
                    if name not in self.bound_names and name not in self.global_names
                )
            nested.emit(Op.RETURN)
            for default in node.args.defaults:
                self.expr(default)
            kw_default_count = 0
            for default in node.args.kw_defaults:
                if default is None:
                    continue
                self.expr(default)
                kw_default_count += 1
            # Parameter/return annotations. Stored as unparsed source
            # strings (a compile-time constant, no bytecode needed) rather
            # than evaluated eagerly -- a self-referential annotation like
            # `def copy(self) -> deque:` inside `class deque:` itself would
            # break under eager evaluation, since the class's own name
            # isn't bound yet while its body (and the methods within it)
            # are still being defined; this is exactly the problem PEP 649
            # laziness solves for real Python. Deferring to strings matches
            # that same real-Python behavior and gives interpreted
            # functions a real __annotations__/__annotate__ (needed by
            # e.g. functools.singledispatch's bare ``@register``, which
            # otherwise rejects the function outright).
            annotated_args = [
                *node.args.posonlyargs, *node.args.args,
                *([node.args.vararg] if node.args.vararg else []),
                *node.args.kwonlyargs,
                *([node.args.kwarg] if node.args.kwarg else []),
            ]
            annotations = {
                arg.arg: ast.unparse(arg.annotation)
                for arg in annotated_args if arg.annotation is not None
            }
            if node.returns is not None:
                annotations["return"] = ast.unparse(node.returns)
            self.emit(
                Op.MAKE_FUNCTION,
                self.constant((nested.finish(), len(node.args.defaults), kw_default_count, annotations)),
            )
            for decorator in reversed(node.decorator_list):
                self.expr(decorator)
                self.emit(Op.SWAP)
                self.emit(Op.CALL, 1)
            self.emit(Op.STORE_NAME, self.name_index(node.name))
        elif isinstance(node, ast.ClassDef):
            body = _Lowerer(f"{self.name}.{node.name}")
            body.defer_annotations = self.defer_annotations
            for type_param in getattr(node, "type_params", []):
                param_name = getattr(type_param, "name", "")
                if not param_name:
                    continue
                constructor = "TypeVar"
                if isinstance(type_param, getattr(ast, "ParamSpec", ())):
                    constructor = "ParamSpec"
                elif isinstance(type_param, getattr(ast, "TypeVarTuple", ())):
                    constructor = "TypeVarTuple"
                body.emit(Op.LOAD_NAME, body.name_index(constructor))
                body.emit(Op.LOAD_CONST, body.constant(param_name))
                body.emit(Op.CALL, 1)
                body.emit(Op.STORE_NAME, body.name_index(param_name))
            for statement in node.body:
                body.stmt(statement)
            body.emit(Op.RETURN)
            base_flags = sum((1 << index) for index, base in enumerate(node.bases)
                             if isinstance(base, ast.Starred))
            for base in node.bases:
                self.expr(base.value if isinstance(base, ast.Starred) else base)
            if base_flags:
                self.emit(Op.BUILD_TUPLE_UNPACK, len(node.bases) | (base_flags << 16))
                base_count = 1
            else:
                base_count = len(node.bases)
            has_keywords = bool(node.keywords)
            if has_keywords:
                for keyword in node.keywords:
                    if keyword.arg is None:
                        self.expr(keyword.value)
                    else:
                        self.emit(Op.LOAD_CONST, self.constant(keyword.arg))
                        self.expr(keyword.value)
                        self.emit(Op.BUILD_TUPLE, 2)
                self.emit(Op.BUILD_DICT_UNPACK, len(node.keywords) |
                          (sum((1 << index) for index, keyword in enumerate(node.keywords)
                               if keyword.arg is None) << 16))
            spec = (node.name, body.finish(), base_count, has_keywords)
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
            self.loop_needs_pop.append(False)
            for statement in node.body:
                self.stmt(statement)
            self.loop_starts.pop()
            self.loop_needs_pop.pop()
            self.emit(Op.JUMP, start)
            else_start = len(self.instructions)
            self.patch(exit_jump, else_start)
            for statement in node.orelse:
                self.stmt(statement)
            end = len(self.instructions)
            for jump in self.loop_exits.pop():
                self.patch(jump, end)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.expr(node.iter)
            self.emit(Op.GET_ITER)
            start = len(self.instructions)
            exit_jump = self.emit(Op.FOR_ITER)
            self.store_sequence(node.target)
            self.loop_exits.append([])
            self.loop_starts.append(start)
            self.loop_needs_pop.append(True)
            for statement in node.body:
                self.stmt(statement)
            self.loop_starts.pop()
            self.loop_needs_pop.pop()
            self.emit(Op.JUMP, start)
            else_start = len(self.instructions)
            self.patch(exit_jump, else_start)
            for statement in node.orelse:
                self.stmt(statement)
            end = len(self.instructions)
            for jump in self.loop_exits.pop():
                self.patch(jump, end)
        elif isinstance(node, ast.Match):
            subject_name = f"__pyinbin_match_{len(self.instructions)}"
            self.expr(node.subject)
            self.emit(Op.STORE_NAME, self.name_index(subject_name))
            end_jumps: list[int] = []
            for case in node.cases:
                self.emit(Op.LOAD_NAME, self.name_index(subject_name))
                self.emit(Op.MATCH_PATTERN, self.constant(self.pattern_spec(case.pattern)))
                next_case = self.emit(Op.JUMP_IF_FALSE)
                guard_jump: int | None = None
                if case.guard is not None:
                    self.expr(case.guard)
                    guard_jump = self.emit(Op.JUMP_IF_FALSE)
                for statement in case.body:
                    self.stmt(statement)
                end_jumps.append(self.emit(Op.JUMP))
                next_offset = len(self.instructions)
                self.patch(next_case, next_offset)
                if guard_jump is not None:
                    self.patch(guard_jump, next_offset)
            end = len(self.instructions)
            for jump in end_jumps:
                self.patch(jump, end)
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))) and not node.handlers and node.finalbody and not node.orelse:
            # A bare ``try/finally`` (no ``except``) still needs real
            # exception protection: without a TRY_BEGIN/TRY_END pair, an
            # exception raised in the body (including one injected from
            # outside via ``generator.close()``/``throw()``) skips the
            # finally block entirely instead of running cleanup before
            # propagating -- purely sequential body-then-finally lowering
            # only covers the no-exception fallthrough case.
            handler_jump = self.emit(Op.TRY_BEGIN)
            for statement in node.body:
                self.stmt(statement)
            self.emit(Op.TRY_END)
            for statement in node.finalbody:
                self.stmt(statement)
            normal_jump = self.emit(Op.JUMP)
            self.patch(handler_jump, len(self.instructions))
            for statement in node.finalbody:
                self.stmt(statement)
            self.emit(Op.RAISE)
            self.patch(normal_jump, len(self.instructions))
        elif isinstance(node, (ast.Try, getattr(ast, "TryStar", ast.Try))) and node.handlers:
            handler_jump = self.emit(Op.TRY_BEGIN)
            for statement in node.body:
                self.stmt(statement)
            self.emit(Op.TRY_END)
            for statement in node.orelse:
                self.stmt(statement)
            for statement in node.finalbody:
                self.stmt(statement)
            normal_jump = self.emit(Op.JUMP)
            self.patch(handler_jump, len(self.instructions))
            end_jumps: list[int] = []
            for handler in node.handlers:
                next_handler: int | None = None
                if handler.type is not None:
                    expected = self.exception_spec(handler.type)
                    self.emit(Op.MATCH_EXCEPTION_CHECK, self.constant(expected))
                    next_handler = self.emit(Op.JUMP_IF_FALSE)
                if handler.name:
                    self.emit(Op.STORE_NAME, self.name_index(handler.name))
                else:
                    self.emit(Op.POP_TOP)
                for statement in handler.body:
                    self.stmt(statement)
                for statement in node.finalbody:
                    self.stmt(statement)
                end_jumps.append(self.emit(Op.JUMP))
                if next_handler is not None:
                    self.patch(next_handler, len(self.instructions))
            self.emit(Op.RAISE)
            end = len(self.instructions)
            self.patch(normal_jump, end)
            for jump in end_jumps:
                self.patch(jump, end)
        elif isinstance(node, ast.Pass):
            return
        elif isinstance(node, ast.Continue) and self.loop_starts:
            self.emit(Op.JUMP, self.loop_starts[-1])
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.expr(item.context_expr)
                self.emit(Op.WITH_ENTER)
                if item.optional_vars is not None:
                    self.store_sequence(item.optional_vars)
                else:
                    self.emit(Op.POP_TOP)
            for statement in node.body:
                self.stmt(statement)
            for _ in reversed(node.items):
                self.emit(Op.WITH_EXIT)
        elif isinstance(node, ast.Break) and self.loop_exits:
            if self.loop_needs_pop and self.loop_needs_pop[-1]:
                self.emit(Op.POP_TOP)
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
                    self.emit(Op.IMPORT_RELATIVE_FROM, self.constant((node.module, node.level, "*")))
                    self.emit(Op.IMPORT_STAR)
                    continue
                spec = (node.module, node.level, alias.name)
                self.emit(Op.IMPORT_RELATIVE_FROM, self.constant(spec))
                self.emit(Op.STORE_NAME, self.name_index(alias.asname or alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level > 0 and node.module is None:
            for alias in node.names:
                if alias.name == "*":
                    self.emit(Op.IMPORT_RELATIVE_FROM, self.constant(("", node.level, "*")))
                    self.emit(Op.IMPORT_STAR)
                    continue
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
            self.is_coroutine,
            sorted(self.free_names | self.nonlocal_names),
            getattr(self, "interactive", False),
            getattr(self, "is_async_generator", False),
        )


def compile_source(source: str, filename: str = "<pyinbin>", mode: str = "exec") -> CodeObject:
    """Parse and lower source for the portable pyinbin VM."""
    try:
        module = ast.parse(source, filename=filename, mode=mode)
    except SyntaxError as exc:
        raise PyinbinUnsupportedError(f"{filename}:{exc.lineno}: invalid Python syntax: {exc.msg}") from exc
    lowerer = _Lowerer(filename)
    lowerer.interactive = mode == "single"
    # CPython 3.14 (PEP 649/749) evaluates module/class-level annotations
    # lazily by default -- not just under ``from __future__ import
    # annotations`` (that future-import is now a no-op kept for backward
    # compatibility). Real code widely relies on this to write annotations
    # like ``Callable[..., None] | None`` that would otherwise need every
    # referenced name to already support subscription/``|`` at module
    # exec time. Defer unconditionally to match; ``not self.is_function``
    # at each call site already keeps this scoped to module/class-level
    # bare annotated assignments, not function parameter annotations.
    lowerer.defer_annotations = True
    for statement in module.body:
        lowerer.stmt(statement)
    if mode == "single":
        lowerer.emit(Op.RETURN)
    return lowerer.finish()
