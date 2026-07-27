"""Object-flow compatibility for dynamic but statically recoverable Python.

Two valid Python patterns need a little whole-program help in a native compiler:

* generator methods (``obj.walk()`` containing ``yield``), and
* methods whose return class is selected by a class-object parameter
  (``services.get_service(World)``).

The implementations below are general AST lowerings and contain no knowledge of
Somnia or any particular method/class name.
"""

from __future__ import annotations

from . import ast_nodes as A
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr


def _has_yield(statements: list) -> bool:
    for statement in statements:
        if isinstance(statement, A.YieldStmt):
            return True
        for name in (
            "then",
            "orelse",
            "body",
            "handler",
            "else_body",
            "finally_body",
        ):
            nested = getattr(statement, name, None)
            if isinstance(nested, list) and _has_yield(nested):
                return True
        if isinstance(statement, A.Try):
            for _types, _binding, body in statement.extra_handlers:
                if _has_yield(body):
                    return True
    return False


def _yield_element_type(statements: list, owner_name: str) -> str:
    observed: set[str] = set()

    def visit(items: list) -> None:
        for statement in items:
            if isinstance(statement, A.YieldStmt):
                value = statement.value
                if isinstance(value, A.Name) and value.name == "self":
                    observed.add(owner_name)
                elif isinstance(value, A.IntLit):
                    observed.add("int")
                elif isinstance(value, A.FloatLit):
                    observed.add("float")
                elif isinstance(value, A.StrLit) or isinstance(value, A.FString):
                    observed.add("str")
                else:
                    observed.add("any")
            for name in (
                "then",
                "orelse",
                "body",
                "handler",
                "else_body",
                "finally_body",
            ):
                nested = getattr(statement, name, None)
                if isinstance(nested, list):
                    visit(nested)
            if isinstance(statement, A.Try):
                for _types, _binding, body in statement.extra_handlers:
                    visit(body)

    visit(statements)
    if len(observed) == 1:
        for value in observed:
            return value
    if owner_name in observed and observed.issubset({owner_name, "any"}):
        return owner_name
    return "any"


def _rewrite_generator_statements(statements: list, result_name: str, pos) -> list:
    rewritten: list = []
    for statement in statements:
        if isinstance(statement, A.YieldStmt):
            rewritten.append(
                A.ExprStmt(
                    expr=A.MethodCall(
                        obj=A.Name(name=result_name, pos=statement.pos),
                        method="append",
                        args=[statement.value],
                        pos=statement.pos,
                    ),
                    pos=statement.pos,
                )
            )
            continue

        if isinstance(statement, A.Return):
            # A generator's return value belongs to StopIteration.value, not to
            # the yielded sequence. The eager native fallback terminates by
            # returning the values accumulated so far.
            rewritten.append(
                A.Return(value=A.Name(name=result_name, pos=statement.pos), pos=statement.pos)
            )
            continue

        if isinstance(statement, A.If):
            statement.then = _rewrite_generator_statements(statement.then, result_name, pos)
            statement.orelse = _rewrite_generator_statements(
                statement.orelse or [], result_name, pos
            )
        elif isinstance(statement, A.For):
            statement.body = _rewrite_generator_statements(statement.body, result_name, pos)
            statement.orelse = _rewrite_generator_statements(
                statement.orelse or [], result_name, pos
            )
        elif isinstance(statement, A.While):
            statement.body = _rewrite_generator_statements(statement.body, result_name, pos)
            statement.orelse = _rewrite_generator_statements(
                statement.orelse or [], result_name, pos
            )
        elif isinstance(statement, A.Try):
            statement.body = _rewrite_generator_statements(statement.body, result_name, pos)
            statement.handler = _rewrite_generator_statements(
                statement.handler or [], result_name, pos
            )
            statement.else_body = _rewrite_generator_statements(
                statement.else_body or [], result_name, pos
            )
            statement.finally_body = _rewrite_generator_statements(
                statement.finally_body or [], result_name, pos
            )
            statement.extra_handlers = [
                (
                    types,
                    binding,
                    _rewrite_generator_statements(body, result_name, pos),
                )
                for types, binding, body in statement.extra_handlers
            ]
        rewritten.append(statement)
    return rewritten


def _lower_generator_methods(mod: A.Module) -> None:
    if getattr(mod, "_generator_methods_lowered", False):
        return
    mod._generator_methods_lowered = True

    counter = 0
    for owner in mod.classes:
        for method in owner.methods:
            if not _has_yield(method.body):
                continue
            counter += 1
            result_name = f"__asmpython_yields_{counter}"
            element_type = _yield_element_type(method.body, owner.name)
            method.body = [
                A.Assign(
                    target=result_name,
                    value=A.ListLit(elems=[], pos=method.pos),
                    pos=method.pos,
                )
            ] + _rewrite_generator_statements(method.body, result_name, method.pos)
            method.body.append(
                A.Return(value=A.Name(name=result_name, pos=method.pos), pos=method.pos)
            )
            method.ret_type = ("list", element_type)


def _walk_expression(expression):
    if expression is None:
        return
    yield expression
    for name in (
        "left",
        "right",
        "operand",
        "func_expr",
        "obj",
        "index",
        "test",
        "body",
        "orelse",
        "value",
        "elt",
        "key",
        "iter",
    ):
        child = getattr(expression, name, None)
        if child is not None and not isinstance(child, list):
            yield from _walk_expression(child)
    for name in (
        "args",
        "elems",
        "operands",
        "values",
        "keys",
        "segments",
        "extra_for_iters",
        "extra_for_conds",
    ):
        children = getattr(expression, name, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    yield from _walk_expression(child)
    kwargs = getattr(expression, "kwargs", None)
    if isinstance(kwargs, list):
        for _name, child in kwargs:
            yield from _walk_expression(child)


def _walk_statements(statements: list):
    for statement in statements:
        yield statement
        for name in ("expr", "value", "test", "iter", "target", "obj"):
            expression = getattr(statement, name, None)
            if expression is not None and not isinstance(expression, str):
                yield from _walk_expression(expression)
        for name in (
            "then",
            "orelse",
            "body",
            "handler",
            "else_body",
            "finally_body",
        ):
            nested = getattr(statement, name, None)
            if isinstance(nested, list):
                yield from _walk_statements(nested)
        if isinstance(statement, A.Try):
            for _types, _binding, body in statement.extra_handlers:
                yield from _walk_statements(body)


def _method_has_return(method) -> bool:
    return any(isinstance(node, A.Return) for node in _walk_statements(method.body))


def _parameter_selects_class(method, parameter: str) -> bool:
    for node in _walk_statements(method.body):
        if isinstance(node, A.Call):
            if node.func == parameter:
                return True
            if (
                node.func == "isinstance"
                and len(node.args) >= 2
                and isinstance(node.args[1], A.Name)
                and node.args[1].name == parameter
            ):
                return True
    return False


def _mark_class_parameter_factories(mod: A.Module) -> None:
    if getattr(mod, "_class_parameter_factories_marked", False):
        return
    mod._class_parameter_factories_marked = True

    for owner in mod.classes:
        for method in owner.methods:
            if not _method_has_return(method):
                continue
            decorators = list(getattr(method, "decorators", []) or [])
            start = 0 if "staticmethod" in decorators else 1
            for index in range(start, len(method.params)):
                parameter = method.params[index]
                if _parameter_selects_class(method, parameter):
                    method.class_return_param_index = index
                    break


def _method_definition(analyzer: SemaAnalyzer, receiver_type: str, method_name: str):
    classes = {owner.name: owner for owner in analyzer.mod.classes}
    if receiver_type.startswith("instance:"):
        current = receiver_type.split(":", 1)[1]
        seen: set[str] = set()
        while current in classes and current not in seen:
            seen.add(current)
            owner = classes[current]
            for method in owner.methods:
                if method.name == method_name:
                    return method
            current = owner.parent

    matches = []
    for owner in analyzer.mod.classes:
        for method in owner.methods:
            if (
                method.name == method_name
                and getattr(method, "class_return_param_index", None) is not None
            ):
                matches.append(method)
    return matches[0] if len(matches) == 1 else None


def _check_expr_with_class_parameter_return(self: SemaAnalyzer, expression, scope) -> None:
    _ORIGINAL_CHECK_EXPR(self, expression, scope)
    if not isinstance(expression, A.MethodCall):
        return

    receiver_type = A.expr_type(expression.obj)
    method = _method_definition(self, receiver_type, expression.method)
    if method is None:
        return

    parameter_index = getattr(method, "class_return_param_index", None)
    if parameter_index is None:
        return
    decorators = list(getattr(method, "decorators", []) or [])
    implicit = 0 if "staticmethod" in decorators else 1
    argument_index = parameter_index - implicit
    if argument_index < 0 or argument_index >= len(expression.args):
        return

    argument = expression.args[argument_index]
    if isinstance(argument, A.Name) and argument.name in self.classes:
        expression.inferred_type = f"instance:{argument.name}"


def _analyze_with_object_flow(self: SemaAnalyzer) -> None:
    _lower_generator_methods(self.mod)
    _mark_class_parameter_factories(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_object_flow_check_patch", False):
    SemaAnalyzer._check_expr = _check_expr_with_class_parameter_return
    SemaAnalyzer._asmpython_object_flow_check_patch = True

if not getattr(SemaAnalyzer, "_asmpython_object_flow_analyze_patch", False):
    SemaAnalyzer.analyze = _analyze_with_object_flow
    SemaAnalyzer._asmpython_object_flow_analyze_patch = True
