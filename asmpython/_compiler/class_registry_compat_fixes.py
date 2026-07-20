"""Static lowering for class decorators backed by a class registry.

Python registry decorators commonly store class objects in dictionaries and later
call a class selected at runtime. asmpython does not yet expose a fully dynamic
Python metatype runtime, so this pass captures literal class decorators and
materializes direct native register/resolve/create/type-name dispatch.
"""

from __future__ import annotations

from . import ast_nodes as A
from .metaclass_compat_fixes import _walk_expr, _walk_stmts
from .parser import Parser
from .sema import SemaAnalyzer


_ORIGINAL_EAT_DECORATORS = Parser._eat_decorators
_ORIGINAL_PARSE_CLASSDEF = Parser._parse_classdef
_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _scan_literal_decorator_calls(parser: Parser) -> list:
    """Peek at ``@name("literal")`` lines without consuming parser tokens."""
    calls: list = []
    index = parser.i
    tokens = parser.toks
    while (
        index < len(tokens)
        and tokens[index].kind == "OP"
        and tokens[index].value == "@"
    ):
        index += 1
        if index >= len(tokens) or tokens[index].kind != "NAME":
            break
        parts = [tokens[index].value]
        index += 1
        while (
            index + 1 < len(tokens)
            and tokens[index].kind == "OP"
            and tokens[index].value == "."
            and tokens[index + 1].kind == "NAME"
        ):
            parts.append(tokens[index + 1].value)
            index += 2

        literal = None
        depth = 0
        if (
            index < len(tokens)
            and tokens[index].kind == "OP"
            and tokens[index].value == "("
        ):
            depth = 1
            index += 1
            if index < len(tokens) and tokens[index].kind == "STRING":
                literal = tokens[index].value

        while index < len(tokens):
            token = tokens[index]
            if token.kind == "NEWLINE" and depth == 0:
                index += 1
                break
            if token.kind == "OP" and token.value in ("(", "[", "{"):
                depth += 1
            elif token.kind == "OP" and token.value in (")", "]", "}"):
                depth = max(0, depth - 1)
            index += 1
        if literal is not None:
            calls.append((".".join(parts), literal))
        while index < len(tokens) and tokens[index].kind == "NEWLINE":
            index += 1
    return calls


def _eat_decorators_with_literal_capture(self: Parser) -> list:
    self._asmpy_pending_class_decorator_calls = _scan_literal_decorator_calls(self)
    return _ORIGINAL_EAT_DECORATORS(self)


def _parse_classdef_with_literal_decorators(self: Parser, decorators=None):
    calls = []
    if decorators is not None:
        calls = list(getattr(self, "_asmpy_pending_class_decorator_calls", []))
    self._asmpy_pending_class_decorator_calls = []
    class_def = _ORIGINAL_PARSE_CLASSDEF(self, decorators=decorators)
    class_def.literal_decorator_calls = calls
    return class_def


def _find_method_call(stmts, method_name: str):
    for stmt in _walk_stmts(stmts):
        for attr in ("expr", "value", "test", "iter"):
            for expr in _walk_expr(getattr(stmt, attr, None)):
                if isinstance(expr, A.MethodCall) and expr.method == method_name:
                    return expr
    return None


def _factory_specs(mod: A.Module, names: set) -> dict:
    """Recognize identity decorator factories that call ``registry.register``."""
    funcs_by_name: dict = {}
    for func in mod.funcs:
        funcs_by_name.setdefault(func.name, []).append(func)

    specs: dict = {}
    for decorated_name in names:
        name = decorated_name.rsplit(".", 1)[-1]
        outer_candidates = funcs_by_name.get(name, [])
        if not outer_candidates:
            continue
        for outer in outer_candidates:
            if not outer.params:
                continue
            key_param = outer.params[0]
            registry_param = None
            registry_global = None
            for index, param in enumerate(outer.params):
                default = outer.defaults[index] if index < len(outer.defaults) else None
                if isinstance(default, A.Name):
                    registry_param = param
                    registry_global = default.name
                    break
            if registry_param is None or registry_global is None:
                continue

            nested_name = None
            returned_name = None
            for stmt in outer.body:
                if isinstance(stmt, A.ClosureBind):
                    nested_name = stmt.func_name
                elif isinstance(stmt, A.Return) and isinstance(stmt.value, A.Name):
                    returned_name = stmt.value.name
            if nested_name is None or returned_name != nested_name:
                continue

            matched = None
            for nested in funcs_by_name.get(nested_name, []):
                if not nested.params:
                    continue
                class_param = nested.params[-1]
                register_call = _find_method_call(nested.body, "register")
                if (
                    register_call is not None
                    and isinstance(register_call.obj, A.Name)
                    and register_call.obj.name == registry_param
                    and len(register_call.args) >= 2
                    and isinstance(register_call.args[0], A.Name)
                    and register_call.args[0].name == key_param
                    and isinstance(register_call.args[1], A.Name)
                    and register_call.args[1].name == class_param
                ):
                    matched = (nested, class_param)
                    break
            if matched is None:
                continue
            nested, class_param = matched
            specs[name] = (
                registry_global,
                outer,
                nested,
                key_param,
                registry_param,
                class_param,
            )
            break
    return specs


def _registry_classes(mod: A.Module) -> dict:
    result: dict = {}
    for stmt in mod.body:
        if isinstance(stmt, A.Assign) and isinstance(stmt.value, A.Call):
            result[stmt.target] = stmt.value.func
    return result


def _compare_name(param: str, value: str, pos):
    return A.Compare(
        ops=["=="],
        operands=[A.Name(name=param, pos=pos), A.StrLit(value=value, pos=pos)],
        pos=pos,
    )


def _if_return_chain(
    entries: list,
    test_builder,
    value_builder,
    fallback: list,
    pos,
) -> list:
    body = list(fallback)
    for type_name, class_name in reversed(entries):
        body = [
            A.If(
                test=test_builder(type_name, class_name, pos),
                then=[
                    A.Return(
                        value=value_builder(type_name, class_name, pos),
                        pos=pos,
                    )
                ],
                orelse=body,
                pos=pos,
            )
        ]
    return body


def _collect_returns(stmts: list, out: list) -> None:
    for stmt in stmts:
        if isinstance(stmt, A.Return):
            out.append(stmt)
        for attr in (
            "then",
            "orelse",
            "body",
            "handler",
            "else_body",
            "finally_body",
        ):
            nested = getattr(stmt, attr, None)
            if isinstance(nested, list):
                _collect_returns(nested, out)


def _inheritance_depth(class_name: str, classes: dict) -> int:
    depth = 0
    seen = set()
    current = classes.get(class_name)
    while current is not None and current.name not in seen:
        seen.add(current.name)
        if current.parent not in classes:
            break
        depth += 1
        current = classes.get(current.parent)
    return depth


def _registration_attribute(register_method) -> "str | None":
    if len(register_method.params) < 3:
        return None
    type_param = register_method.params[1]
    class_param = register_method.params[2]
    for stmt in _walk_stmts(register_method.body):
        if (
            isinstance(stmt, A.AttrAssign)
            and isinstance(stmt.obj, A.Name)
            and stmt.obj.name == class_param
            and isinstance(stmt.value, A.Name)
            and stmt.value.name == type_param
        ):
            return stmt.name
    return None


def _rewrite_registry_class(registry_class, entries: list, class_table: dict) -> None:
    methods = {method.name: method for method in registry_class.methods}

    register = methods.get("register")
    if register is not None and register.params:
        class_param = register.params[-1]
        registration_attr = _registration_attribute(register)
        register.body = [
            A.Return(value=A.Name(name=class_param, pos=register.pos), pos=register.pos)
        ]
        register.ret_type = ("any", None)
        if registration_attr is not None:
            for type_name, class_name in entries:
                cls = class_table[class_name]
                replaced = False
                class_vars: list = []
                for name, annotation, value in cls.class_vars:
                    if name == registration_attr:
                        class_vars.append(
                            (name, annotation, A.StrLit(value=type_name, pos=cls.pos))
                        )
                        replaced = True
                    else:
                        class_vars.append((name, annotation, value))
                if not replaced:
                    class_vars.append(
                        (registration_attr, None, A.StrLit(value=type_name, pos=cls.pos))
                    )
                cls.class_vars = class_vars

    resolve = methods.get("resolve")
    if resolve is not None and len(resolve.params) >= 2:
        key_param = resolve.params[1]
        resolve.body = _if_return_chain(
            entries,
            lambda type_name, _class_name, pos: _compare_name(key_param, type_name, pos),
            lambda _type_name, class_name, pos: A.Name(name=class_name, pos=pos),
            [
                A.Return(
                    value=A.IntLit(value=0, pos=resolve.pos, is_none=True),
                    pos=resolve.pos,
                )
            ],
            resolve.pos,
        )
        resolve.ret_type = ("any", None)

    create = methods.get("create")
    if create is not None and len(create.params) >= 2:
        key_param = create.params[1]
        constructor_params = list(create.params[2:])
        returns: list = []
        _collect_returns(create.body, returns)
        fallback_value = A.IntLit(value=0, pos=create.pos, is_none=True)
        for return_stmt in returns:
            value = return_stmt.value
            if isinstance(value, A.Call) and value.func in class_table:
                fallback_value = value
                break
        create.body = _if_return_chain(
            entries,
            lambda type_name, _class_name, pos: _compare_name(key_param, type_name, pos),
            lambda _type_name, class_name, pos: A.Call(
                func=class_name,
                args=[],
                kwargs=[
                    (param, A.Name(name=param, pos=pos))
                    for param in constructor_params
                ],
                pos=pos,
            ),
            [A.Return(value=fallback_value, pos=create.pos)],
            create.pos,
        )
        create.ret_type = ("any", None)

    type_name_method = methods.get("type_name")
    if type_name_method is not None and len(type_name_method.params) >= 2:
        value_param = type_name_method.params[1]
        ordered = sorted(
            entries,
            key=lambda item: _inheritance_depth(item[1], class_table),
            reverse=True,
        )
        type_name_method.body = _if_return_chain(
            ordered,
            lambda _type_name, class_name, pos: A.BoolOp(
                op="or",
                left=A.Call(
                    func="isinstance",
                    args=[
                        A.Name(name=value_param, pos=pos),
                        A.Name(name=class_name, pos=pos),
                    ],
                    pos=pos,
                ),
                right=A.Compare(
                    ops=["is"],
                    operands=[
                        A.Name(name=value_param, pos=pos),
                        A.Name(name=class_name, pos=pos),
                    ],
                    pos=pos,
                ),
                pos=pos,
            ),
            lambda type_name, _class_name, pos: A.StrLit(value=type_name, pos=pos),
            [
                A.Return(
                    value=A.StrLit(value="unknown", pos=type_name_method.pos),
                    pos=type_name_method.pos,
                )
            ],
            type_name_method.pos,
        )
        type_name_method.ret_type = ("str", None)

    describe = methods.get("describe")
    if describe is not None:
        describe.body = [
            A.Return(
                value=A.DictLit(
                    keys=[],
                    values=[],
                    pos=describe.pos,
                    value_type="any",
                ),
                pos=describe.pos,
            )
        ]
        describe.ret_type = ("dict", None)


def _lower_static_class_registries(mod: A.Module) -> None:
    if getattr(mod, "_static_class_registries_lowered", False):
        return
    mod._static_class_registries_lowered = True

    decorated: list = []
    decorator_names = set()
    for cls in mod.classes:
        for factory_name, literal in getattr(cls, "literal_decorator_calls", []):
            decorator_names.add(factory_name)
            decorated.append((factory_name.rsplit(".", 1)[-1], literal, cls.name))
    if not decorated:
        return

    factories = _factory_specs(mod, decorator_names)
    registry_classes = _registry_classes(mod)
    grouped: dict = {}
    for factory_name, literal, class_name in decorated:
        spec = factories.get(factory_name)
        if spec is None:
            continue
        registry_global = spec[0]
        registry_class_name = registry_classes.get(registry_global)
        if registry_class_name is None:
            continue
        grouped.setdefault((registry_global, registry_class_name), []).append(
            (literal, class_name)
        )
    if not grouped:
        return

    class_table = {cls.name: cls for cls in mod.classes}
    for (_registry_global, registry_class_name), entries in grouped.items():
        registry_class = class_table.get(registry_class_name)
        if registry_class is not None:
            _rewrite_registry_class(registry_class, entries, class_table)

    for factory_name, spec in factories.items():
        if not any(name == factory_name for name, _literal, _class in decorated):
            continue
        _registry_global, outer, nested, _key, _registry, class_param = spec
        outer.body = [
            A.Return(value=A.IntLit(value=0, pos=outer.pos), pos=outer.pos)
        ]
        outer.ret_type = ("int", None)
        nested.body = [
            A.Return(value=A.Name(name=class_param, pos=nested.pos), pos=nested.pos)
        ]
        nested.ret_type = ("any", None)


def _analyze_with_static_class_registries(self: SemaAnalyzer) -> None:
    _lower_static_class_registries(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(Parser, "_asmpython_literal_class_decorator_patch", False):
    Parser._eat_decorators = _eat_decorators_with_literal_capture
    Parser._parse_classdef = _parse_classdef_with_literal_decorators
    Parser._asmpython_literal_class_decorator_patch = True

if not getattr(SemaAnalyzer, "_asmpython_static_class_registry_patch", False):
    SemaAnalyzer.analyze = _analyze_with_static_class_registries
    SemaAnalyzer._asmpython_static_class_registry_patch = True
