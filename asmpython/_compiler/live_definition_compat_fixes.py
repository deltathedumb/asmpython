"""Conservative live-definition analysis for merged project modules.

Importing a package executes its top-level code but does not execute every
function and method body re-exported by that package. The native compiler merges
all source modules, so it must preserve that distinction. This pass identifies
classes constructed by the entry graph, their ancestors and methods, plus direct
function dependencies. Bodies outside that graph are replaced by inert native
stubs before semantic analysis and code generation.
"""

from __future__ import annotations

from . import ast_nodes as A
from .object_flow_compat_fixes import _walk_expression, _walk_statements
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _scan_body(
    body: list,
    function_names: set,
    class_names: set,
) -> tuple[set[str], set[str]]:
    functions: set[str] = set()
    classes: set[str] = set()

    for node in _walk_statements(body):
        expressions = []
        for name in ("expr", "value", "test", "iter", "target", "obj"):
            expression = getattr(node, name, None)
            if expression is not None and not isinstance(expression, str):
                expressions.extend(_walk_expression(expression))

        for expression in expressions:
            if isinstance(expression, A.Call):
                if expression.func in function_names:
                    functions.add(expression.func)
                if expression.func in class_names:
                    classes.add(expression.func)
                for argument in expression.args:
                    if isinstance(argument, A.Name) and argument.name in class_names:
                        classes.add(argument.name)
                for _keyword, argument in expression.kwargs:
                    if isinstance(argument, A.Name) and argument.name in class_names:
                        classes.add(argument.name)
            elif isinstance(expression, A.MethodCall):
                for argument in expression.args:
                    if isinstance(argument, A.Name) and argument.name in class_names:
                        classes.add(argument.name)
                for _keyword, argument in expression.kwargs:
                    if isinstance(argument, A.Name) and argument.name in class_names:
                        classes.add(argument.name)
    return functions, classes


def _live_definitions(mod: A.Module) -> tuple[set[str], set[str]]:
    functions = {definition.name: definition for definition in mod.funcs}
    classes = {definition.name: definition for definition in mod.classes}
    function_names = set(functions)
    class_names = set(classes)

    live_functions, live_classes = _scan_body(mod.body, function_names, class_names)
    queued_functions = list(live_functions)
    queued_classes = list(live_classes)
    processed_functions: set[str] = set()
    processed_classes: set[str] = set()

    while queued_functions or queued_classes:
        while queued_functions:
            name = queued_functions.pop()
            if name in processed_functions or name not in functions:
                continue
            processed_functions.add(name)
            found_functions, found_classes = _scan_body(
                functions[name].body,
                function_names,
                class_names,
            )
            for found in found_functions:
                if found not in live_functions:
                    live_functions.add(found)
                    queued_functions.append(found)
            for found in found_classes:
                if found not in live_classes:
                    live_classes.add(found)
                    queued_classes.append(found)

        while queued_classes:
            name = queued_classes.pop()
            if name in processed_classes or name not in classes:
                continue
            processed_classes.add(name)
            owner = classes[name]
            if owner.parent in classes and owner.parent not in live_classes:
                live_classes.add(owner.parent)
                queued_classes.append(owner.parent)

            # Once an instance exists, any of its methods may be selected through
            # Python's dynamic dispatch. Check all methods on that live class,
            # while excluding entire classes never instantiated/referenced.
            for method in owner.methods:
                found_functions, found_classes = _scan_body(
                    method.body,
                    function_names,
                    class_names,
                )
                for found in found_functions:
                    if found not in live_functions:
                        live_functions.add(found)
                        queued_functions.append(found)
                for found in found_classes:
                    if found not in live_classes:
                        live_classes.add(found)
                        queued_classes.append(found)

    return live_functions, live_classes


def _neutral_body(definition) -> None:
    if getattr(definition, "_dead_body_neutralized", False):
        return
    definition._dead_body_neutralized = True
    definition.body = [
        A.Return(
            value=A.IntLit(value=0, pos=definition.pos),
            pos=definition.pos,
        )
    ]
    definition.ret_type = ("int", None)
    definition.ret_tuple = None
    definition.ret_list_tuple_types = []


def _analyze_live_project_definitions(self: SemaAnalyzer) -> None:
    live_functions, live_classes = _live_definitions(self.mod)

    for function in self.mod.funcs:
        if function.name not in live_functions:
            _neutral_body(function)

    for owner in self.mod.classes:
        if owner.name in live_classes:
            continue
        for method in owner.methods:
            _neutral_body(method)

    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_live_definition_patch", False):
    SemaAnalyzer.analyze = _analyze_live_project_definitions
    SemaAnalyzer._asmpython_live_definition_patch = True
