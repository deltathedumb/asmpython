"""Compatibility patches for valid Python forms not yet folded into core passes.

This module is imported by :mod:`asmpython._compiler` before the driver imports
the parser and semantic analyzer. Keep fixes general and covered by native
regressions; once their implementations are moved into the owning pass, remove
the corresponding patch here.
"""

from __future__ import annotations

from . import ast_nodes as A
from .parser import Parser
from .sema import SemaAnalyzer


_ORIGINAL_PARSE_STMT = Parser._parse_stmt
_ORIGINAL_PARSE_TRAILERS = Parser._parse_trailers
_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr
_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _parse_stmt_with_yield_from(self: Parser):
    """Lower ``yield from iterable`` through the existing for/yield machinery."""
    token = self._peek()
    next_token = self._peek(1)
    if (
        token.kind == "KEYWORD"
        and token.value == "yield"
        and next_token.kind == "KEYWORD"
        and next_token.value == "from"
    ):
        pos = self._eat().pos
        self._eat()  # from
        iterable = self._parse_expr()
        self._expect("NEWLINE")
        counter = getattr(self, "_yield_from_counter", 0) + 1
        self._yield_from_counter = counter
        item_name = f"__yield_from_{counter}"
        return A.For(
            var=item_name,
            range_args=[],
            iter=iterable,
            body=[A.YieldStmt(value=A.Name(name=item_name, pos=pos), pos=pos)],
            pos=pos,
        )
    return _ORIGINAL_PARSE_STMT(self)


def _parse_trailers_with_expression_calls(self: Parser, atom):
    """Parse calls on any primary result, reusing callable-instance dispatch."""
    while True:
        atom = _ORIGINAL_PARSE_TRAILERS(self, atom)
        if not self._check("OP", "("):
            return atom
        lpar = self._eat()
        args, kwargs = self._parse_call_args()
        self._expect("OP", ")")
        atom = A.MethodCall(
            obj=atom,
            method="__call__",
            args=args,
            kwargs=kwargs,
            pos=lpar.pos,
        )


def _check_expr_with_type_constructor(self: SemaAnalyzer, expr, scope) -> None:
    """Resolve ``type(name)(...)`` to the statically known user constructor."""
    if (
        isinstance(expr, A.MethodCall)
        and expr.method == "__call__"
        and isinstance(expr.obj, A.Call)
        and expr.obj.func == "type"
        and len(expr.obj.args) == 1
        and isinstance(expr.obj.args[0], A.Name)
    ):
        source = expr.obj.args[0]
        _ORIGINAL_CHECK_EXPR(self, source, scope)
        source_type = A.expr_type(source)
        if source_type.startswith("instance:"):
            class_name = source_type.split(":", 1)[1]
            expr.__class__ = A.Call
            expr.func = class_name
            expr.dstar = None
            expr.resolved_overload_symbol = None
            self._check_call(expr, scope)
            return
    _ORIGINAL_CHECK_EXPR(self, expr, scope)


def _descriptor_value_annotation(initializer) -> "tuple | None":
    """Infer a descriptor's exposed value type from its static constructor."""
    if not isinstance(initializer, A.Call):
        return None
    for name, value in initializer.kwargs:
        if name == "value_type" and isinstance(value, A.Name):
            return (value.name, None)
    if not initializer.args:
        return None
    default = initializer.args[0]
    if isinstance(default, A.StrLit):
        return ("str", None)
    if isinstance(default, A.FloatLit):
        return ("float", None)
    if isinstance(default, A.IntLit):
        return ("bool" if default.is_bool else "int", None)
    if isinstance(default, A.ListLit):
        return ("list", getattr(default, "el_type", None))
    if isinstance(default, A.DictLit):
        return ("dict", None)
    if isinstance(default, A.TupleLit):
        return ("tuple", None)
    if isinstance(default, A.SetLit):
        return ("set", None)
    if isinstance(default, A.Call):
        return (default.func, None)
    if isinstance(default, A.MethodCall) and isinstance(default.obj, A.Name):
        return (default.obj.name, None)
    return None


def _walk_expr(expr):
    """Yield an expression tree without relying on CPython's ``ast`` module."""
    if expr is None:
        return
    yield expr
    for attr in (
        "obj",
        "value",
        "left",
        "right",
        "operand",
        "test",
        "body",
        "orelse",
        "iter",
        "cond",
        "index",
        "elt",
        "key",
        "start",
        "stop",
        "step",
    ):
        child = getattr(expr, attr, None)
        if child is not None and not isinstance(child, (str, int, float, list, tuple)):
            yield from _walk_expr(child)
    for attr in ("args", "operands", "elems", "keys", "values", "segments"):
        children = getattr(expr, attr, None) or []
        for child in children:
            if child is not None:
                yield from _walk_expr(child)
    for _name, child in getattr(expr, "kwargs", []) or []:
        yield from _walk_expr(child)


def _walk_stmts(stmts):
    for stmt in stmts:
        yield stmt
        for attr in (
            "expr",
            "value",
            "test",
            "iter",
            "target",
            "subject",
        ):
            expr = getattr(stmt, attr, None)
            if expr is not None and not isinstance(expr, str):
                yield from _walk_expr(expr)
        for attr in (
            "body",
            "then",
            "orelse",
            "handler",
            "else_body",
            "finally_body",
        ):
            nested = getattr(stmt, attr, None) or []
            if isinstance(nested, list):
                yield from _walk_stmts(nested)


def _lower_static_data_descriptors(mod: A.Module) -> None:
    """Lower statically declared descriptors to shared objects + properties."""
    if getattr(mod, "_static_descriptors_lowered", False):
        return
    mod._static_descriptors_lowered = True

    descriptor_methods = {
        cls.name: {method.name for method in cls.methods}
        for cls in mod.classes
        if any(method.name in ("__get__", "__set__") for method in cls.methods)
    }
    mod._static_descriptor_methods = descriptor_methods
    if not descriptor_methods:
        mod._static_descriptor_bindings = {}
        mod._static_descriptor_init_count = 0
        return

    descriptor_bindings: dict = {}
    module_init: list = []
    for owner in mod.classes:
        existing_methods = {method.name for method in owner.methods}
        rewritten_vars: list = []
        for field_name, annotation, initializer in owner.class_vars:
            descriptor_name = None
            if isinstance(initializer, A.Call) and initializer.func in descriptor_methods:
                descriptor_name = initializer.func
            if descriptor_name is None:
                rewritten_vars.append((field_name, annotation, initializer))
                continue

            methods = descriptor_methods[descriptor_name]
            exposed_type = _descriptor_value_annotation(initializer)
            global_name = f"__asmpy_descriptor_{owner.name}_{field_name}"
            descriptor_bindings[(owner.name, field_name)] = (
                global_name,
                descriptor_name,
            )
            module_init.append(A.Assign(target=global_name, value=initializer, pos=owner.pos))
            if "__set_name__" in methods:
                module_init.append(
                    A.ExprStmt(
                        expr=A.MethodCall(
                            obj=A.Name(name=global_name, pos=owner.pos),
                            method="__set_name__",
                            args=[
                                A.StrLit(value=owner.name, pos=owner.pos),
                                A.StrLit(value=field_name, pos=owner.pos),
                            ],
                            pos=owner.pos,
                        ),
                        pos=owner.pos,
                    )
                )

            if "__get__" in methods and field_name not in existing_methods:
                owner.methods.append(
                    A.FuncDef(
                        name=field_name,
                        params=["self"],
                        body=[
                            A.Return(
                                value=A.MethodCall(
                                    obj=A.Name(name=global_name, pos=owner.pos),
                                    method="__get__",
                                    args=[
                                        A.Name(name="self", pos=owner.pos),
                                        A.StrLit(value=owner.name, pos=owner.pos),
                                    ],
                                    pos=owner.pos,
                                ),
                                pos=owner.pos,
                            )
                        ],
                        pos=owner.pos,
                        defaults=[None],
                        param_types=[None],
                        ret_type=exposed_type,
                        decorators=["property"],
                    )
                )
                existing_methods.add(field_name)

            if "__set__" in methods:
                owner.methods.append(
                    A.FuncDef(
                        name=field_name,
                        params=["self", "value"],
                        body=[
                            A.ExprStmt(
                                expr=A.MethodCall(
                                    obj=A.Name(name=global_name, pos=owner.pos),
                                    method="__set__",
                                    args=[
                                        A.Name(name="self", pos=owner.pos),
                                        A.Name(name="value", pos=owner.pos),
                                    ],
                                    pos=owner.pos,
                                ),
                                pos=owner.pos,
                            )
                        ],
                        pos=owner.pos,
                        defaults=[None, None],
                        param_types=[None, exposed_type],
                        decorators=[f"{field_name}.setter"],
                    )
                )

            rewritten_vars.append(
                (field_name, annotation, A.Name(name=global_name, pos=owner.pos))
            )
        owner.class_vars = rewritten_vars

    mod._static_descriptor_bindings = descriptor_bindings
    mod._static_descriptor_init_count = len(module_init)
    if module_init:
        mod.body = module_init + list(mod.body)


def _find_static_descriptor_metaclasses(mod: A.Module) -> dict:
    """Return ``metaclass -> (metadata_attr, descriptor_type)`` patterns."""
    descriptor_types = set(getattr(mod, "_static_descriptor_methods", {}))
    specs: dict = {}
    for meta in mod.classes:
        new_method = None
        for method in meta.methods:
            if method.name == "__new__":
                new_method = method
                break
        if new_method is None:
            continue

        dict_locals = set()
        metadata_attr = None
        descriptor_type = None
        for stmt in _walk_stmts(new_method.body):
            if isinstance(stmt, A.Assign) and isinstance(stmt.value, A.DictLit):
                dict_locals.add(stmt.target)
            if (
                isinstance(stmt, A.AttrAssign)
                and isinstance(stmt.value, A.Name)
                and stmt.value.name in dict_locals
            ):
                metadata_attr = stmt.name
            for expr in _walk_expr(getattr(stmt, "test", None)):
                if (
                    isinstance(expr, A.Call)
                    and expr.func == "isinstance"
                    and len(expr.args) == 2
                    and isinstance(expr.args[1], A.Name)
                    and expr.args[1].name in descriptor_types
                ):
                    descriptor_type = expr.args[1].name
        if metadata_attr is not None and descriptor_type is not None:
            specs[meta.name] = (metadata_attr, descriptor_type)
    return specs


def _metadata_reflection_method(method, metadata_attr: str) -> bool:
    """Recognize ``@classmethod def f(cls): return dict(cls.<metadata>)``."""
    if "classmethod" not in getattr(method, "decorators", []):
        return False
    if not method.params or len(method.body) != 1:
        return False
    stmt = method.body[0]
    if not isinstance(stmt, A.Return) or not isinstance(stmt.value, A.Call):
        return False
    call = stmt.value
    if call.func != "dict" or len(call.args) != 1:
        return False
    arg = call.args[0]
    return (
        isinstance(arg, A.Attr)
        and arg.name == metadata_attr
        and isinstance(arg.obj, A.Name)
        and arg.obj.name == method.params[0]
    )


def _lower_static_descriptor_metaclasses(mod: A.Module) -> None:
    """Materialize inherited descriptor metadata for static metaclass patterns."""
    if getattr(mod, "_static_metaclasses_lowered", False):
        return
    mod._static_metaclasses_lowered = True

    specs = _find_static_descriptor_metaclasses(mod)
    if not specs:
        return
    class_table = {cls.name: cls for cls in mod.classes}
    bindings = getattr(mod, "_static_descriptor_bindings", {})

    participants: dict = {}
    changed = True
    while changed:
        changed = False
        for cls in mod.classes:
            spec = None
            meta_name = getattr(cls, "metaclass", None)
            if meta_name in specs:
                spec = specs[meta_name]
            elif cls.parent in participants:
                spec = participants[cls.parent]
            if spec is not None and cls.name not in participants:
                participants[cls.name] = spec
                changed = True
    if not participants:
        return

    def inherited_bindings(class_name: str) -> list:
        cls = class_table[class_name]
        values: list = []
        if cls.parent in participants:
            values.extend(inherited_bindings(cls.parent))
        for (owner_name, field_name), binding in bindings.items():
            if owner_name == class_name:
                values = [item for item in values if item[0] != field_name]
                values.append((field_name, binding[0], binding[1]))
        return values

    metadata_init: list = []
    for cls in mod.classes:
        if cls.name not in participants:
            continue
        metadata_attr, descriptor_type = participants[cls.name]
        entries = inherited_bindings(cls.name)
        metadata_global = f"__asmpy_metadata_{cls.name}_{metadata_attr.strip('_')}"
        keys = [A.StrLit(value=name, pos=cls.pos) for name, _global, _type in entries]
        values = [A.Name(name=global_name, pos=cls.pos) for _name, global_name, _type in entries]
        metadata_init.append(
            A.Assign(
                target=metadata_global,
                value=A.DictLit(
                    keys=keys,
                    values=values,
                    pos=cls.pos,
                    value_type=f"instance:{descriptor_type}",
                ),
                pos=cls.pos,
            )
        )

        replaced = False
        rewritten_vars: list = []
        for name, annotation, value in cls.class_vars:
            if name == metadata_attr:
                rewritten_vars.append(
                    (name, annotation, A.Name(name=metadata_global, pos=cls.pos))
                )
                replaced = True
            else:
                rewritten_vars.append((name, annotation, value))
        if not replaced:
            rewritten_vars.append(
                (metadata_attr, None, A.Name(name=metadata_global, pos=cls.pos))
            )
        cls.class_vars = rewritten_vars

        source_methods: list = []
        current = cls
        seen = set()
        while current is not None and current.name not in seen:
            seen.add(current.name)
            for method in current.methods:
                if _metadata_reflection_method(method, metadata_attr):
                    source_methods.append(method)
            current = class_table.get(current.parent)
        for source in source_methods:
            generated = A.FuncDef(
                name=source.name,
                params=list(source.params),
                body=[
                    A.Return(
                        value=A.Call(
                            func="dict",
                            args=[A.Name(name=metadata_global, pos=cls.pos)],
                            pos=cls.pos,
                        ),
                        pos=cls.pos,
                    )
                ],
                pos=cls.pos,
                defaults=list(source.defaults),
                param_types=list(source.param_types),
                ret_type=("dict", None),
                decorators=["classmethod"],
            )
            replaced_method = False
            for index, method in enumerate(cls.methods):
                if method.name == source.name:
                    cls.methods[index] = generated
                    replaced_method = True
                    break
            if not replaced_method:
                cls.methods.append(generated)
            break

    if metadata_init:
        offset = getattr(mod, "_static_descriptor_init_count", 0)
        mod.body = list(mod.body[:offset]) + metadata_init + list(mod.body[offset:])


def _analyze_with_static_descriptors(self: SemaAnalyzer) -> None:
    _lower_static_data_descriptors(self.mod)
    _lower_static_descriptor_metaclasses(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(Parser, "_asmpython_yield_from_patch", False):
    Parser._parse_stmt = _parse_stmt_with_yield_from
    Parser._asmpython_yield_from_patch = True

if not getattr(Parser, "_asmpython_expression_call_patch", False):
    Parser._parse_trailers = _parse_trailers_with_expression_calls
    Parser._asmpython_expression_call_patch = True

if not getattr(SemaAnalyzer, "_asmpython_type_constructor_patch", False):
    SemaAnalyzer._check_expr = _check_expr_with_type_constructor
    SemaAnalyzer._asmpython_type_constructor_patch = True

if not getattr(SemaAnalyzer, "_asmpython_static_descriptor_patch", False):
    SemaAnalyzer.analyze = _analyze_with_static_descriptors
    SemaAnalyzer._asmpython_static_descriptor_patch = True
