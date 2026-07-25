"""Conservative live-definition analysis for merged project modules.

Importing a package executes its top-level code but does not execute every
function and method body re-exported by that package. The native compiler merges
all source modules, so it must preserve that distinction. This pass identifies
classes constructed by the entry graph, direct function dependencies, and the
method names actually dispatched by reachable code. Bodies outside that graph
are replaced by inert native stubs before semantic analysis and code generation.
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
) -> tuple[set[str], set[str], set[str]]:
    functions: set[str] = set()
    classes: set[str] = set()
    methods: set[str] = set()

    for node in _walk_statements(body):
        expressions = []
        for name in ("expr", "value", "test", "iter", "target", "obj"):
            expression = getattr(node, name, None)
            if expression is not None and not isinstance(expression, str):
                expressions.extend(_walk_expression(expression))
        for name in ("values",):
            values = getattr(node, name, None)
            if isinstance(values, list):
                for expression in values:
                    if expression is not None:
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
                methods.add(expression.method)
                # `ClassName.method(...)` -- a @classmethod/@staticmethod called
                # directly on the class -- keeps that class live. Its receiver is
                # a bare class name, not a constructed instance, so the `Call`
                # branch above (which only sees `ClassName(...)` construction and
                # class-name arguments) never marked it live. Without this, a
                # class whose ONLY use is such a call had every one of its method
                # bodies neutralized to `return 0` (the owner-not-live arm of the
                # dead-body check below), so e.g. `Config.get_version()` returned
                # 0 instead of reading its class var. Confirmed via
                # `Config.version` classmethod repro.
                if (
                    isinstance(expression.obj, A.Name)
                    and expression.obj.name in class_names
                ):
                    classes.add(expression.obj.name)
                for argument in expression.args:
                    if isinstance(argument, A.Name) and argument.name in class_names:
                        classes.add(argument.name)
                for _keyword, argument in expression.kwargs:
                    if isinstance(argument, A.Name) and argument.name in class_names:
                        classes.add(argument.name)
            elif isinstance(expression, A.Name):
                # A bare function reference used as a first-class VALUE
                # (passed as a callback argument, assigned to a variable,
                # stored in a list/dict, returned, ...) rather than called
                # by name -- e.g. `apply(f, 3, 4)`, `handlers = [on_ok,
                # on_err]`. `expression.func in function_names` above only
                # catches the "called by its own name" case; a function
                # only ever reached this way still gets compiled (real
                # code calls it indirectly through the parameter/variable
                # it was passed into), so treating it as dead and
                # replacing its body with `return 0` (see _neutral_body)
                # silently miscompiled it. Confirmed via a minimal repro:
                # `def apply(target, a, b): return target(a, b)` called as
                # `apply(f, 3, 4)` ran `f`'s stub instead of `f` itself.
                if expression.name in function_names:
                    functions.add(expression.name)
                # A bare CLASS reference used as a first-class value -- stored in
                # a tuple/list, returned, assigned, passed positionally -- keeps
                # that class live even though it is never constructed here. Its
                # methods (e.g. a @classmethod dispatched later through the
                # variable the class flowed into, `for root in (Server, Client):
                # root.supports(...)`) must survive dead-code neutralization.
                # Mirrors the function-value case just above; without it the
                # whole class was treated as dead and every method body stubbed
                # to `return 0`.
                if expression.name in class_names:
                    classes.add(expression.name)
    return functions, classes, methods


def _method_is_implicit_runtime_surface(method) -> bool:
    name = method.name
    decorators = list(getattr(method, "decorators", []) or [])
    return (
        name == "__init__"
        or (name.startswith("__") and name.endswith("__"))
        or "property" in decorators
        or any(decorator.endswith(".setter") for decorator in decorators)
    )


def _live_definitions(mod: A.Module) -> tuple[set[str], set[str], set[str]]:
    functions = {definition.name: definition for definition in mod.funcs}
    classes = {definition.name: definition for definition in mod.classes}
    function_names = set(functions)
    class_names = set(classes)

    live_functions, live_classes, live_methods = _scan_body(
        mod.body,
        function_names,
        class_names,
    )
    # Two more root sets `mod.body`'s own statements can't capture:
    #  - `main`, the process entry point when one is declared. Nothing in
    #    mod.body calls it in the common (no explicit `if __name__ ==
    #    "__main__": main()` guard) case -- ir_lower.py's own
    #    `_reachable_callables` special-cases it the same way, for the
    #    same reason. Without this, a program whose only top-level
    #    statement is `def main(): ...` had ITS OWN ENTRY POINT stubbed
    #    to `return 0` by this pass, before ir_lower/codegen ever ran --
    #    confirmed via a real regression: `def main() -> int: return 5`
    #    compiled and ran, but always exited 0.
    #  - native-library exports (`@access(Public)` / `@abi(...)`), called
    #    only from OUTSIDE the compiled program (a host resolving the
    #    symbol at runtime), never from anything in mod.body.
    if "main" in function_names:
        live_functions.add("main")
    for name, definition in functions.items():
        if getattr(definition, "is_public_export", False):
            live_functions.add(name)
    for class_name, owner in classes.items():
        class_public = getattr(owner, "is_public_export", False)
        if class_public:
            live_classes.add(class_name)
        for method in owner.methods:
            if class_public or getattr(method, "is_public_export", False):
                live_methods.add(method.name)
    queued_functions = list(live_functions)
    processed_functions: set[str] = set()
    processed_method_keys: set[tuple[str, str]] = set()

    changed = True
    while changed:
        changed = False

        while queued_functions:
            name = queued_functions.pop()
            if name in processed_functions or name not in functions:
                continue
            processed_functions.add(name)
            found_functions, found_classes, found_methods = _scan_body(
                functions[name].body,
                function_names,
                class_names,
            )
            for found in found_functions:
                if found not in live_functions:
                    live_functions.add(found)
                    queued_functions.append(found)
                    changed = True
            for found in found_classes:
                if found not in live_classes:
                    live_classes.add(found)
                    changed = True
            for found in found_methods:
                if found not in live_methods:
                    live_methods.add(found)
                    changed = True

        # A constructed class brings its ancestor initializers/descriptor surface
        # into play, but ordinary methods are retained only when their name is
        # dispatched by reachable code.
        for name in list(live_classes):
            owner = classes.get(name)
            if owner is None:
                continue
            if owner.parent in classes and owner.parent not in live_classes:
                live_classes.add(owner.parent)
                changed = True

        for name in list(live_classes):
            owner = classes.get(name)
            if owner is None:
                continue
            for method in owner.methods:
                if not (
                    method.name in live_methods
                    or _method_is_implicit_runtime_surface(method)
                ):
                    continue
                key = (owner.name, method.name)
                if key in processed_method_keys:
                    continue
                processed_method_keys.add(key)
                found_functions, found_classes, found_methods = _scan_body(
                    method.body,
                    function_names,
                    class_names,
                )
                for found in found_functions:
                    if found not in live_functions:
                        live_functions.add(found)
                        queued_functions.append(found)
                        changed = True
                for found in found_classes:
                    if found not in live_classes:
                        live_classes.add(found)
                        changed = True
                for found in found_methods:
                    if found not in live_methods:
                        live_methods.add(found)
                        changed = True

    return live_functions, live_classes, live_methods


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
    live_functions, live_classes, live_methods = _live_definitions(self.mod)

    for function in self.mod.funcs:
        if function.name not in live_functions:
            _neutral_body(function)

    for owner in self.mod.classes:
        for method in owner.methods:
            if owner.name not in live_classes or not (
                method.name in live_methods
                or _method_is_implicit_runtime_surface(method)
            ):
                _neutral_body(method)

    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_live_definition_patch", False):
    SemaAnalyzer.analyze = _analyze_live_project_definitions
    SemaAnalyzer._asmpython_live_definition_patch = True
