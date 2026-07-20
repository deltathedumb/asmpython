"""Compatibility patches for valid Python forms not yet folded into core passes.

This module is imported by :mod:`asmpython._compiler` before the driver imports
the parser and semantic analyzer. Keep fixes general and covered by native
regressions; once their implementations are moved into the owning pass, remove
the corresponding patch here.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

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
            body=[
                A.YieldStmt(
                    value=A.Name(name=item_name, pos=pos),
                    pos=pos,
                )
            ],
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
    """Resolve ``type(name)(...)`` to the statically known user constructor.

    Restrict the source to a plain name so the lowering never removes evaluation
    side effects from an arbitrary expression.
    """
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


def _rename_identifier(value, old: str, new: str) -> None:
    """Rename one local identifier throughout an asmpython AST subtree.

    Attribute names, keyword names, imported symbols, class names, and string
    literals are deliberately untouched. Only expression references and binding
    positions which can denote the method receiver are rewritten.
    """
    if value is None:
        return
    if isinstance(value, A.Name):
        if value.name == old:
            value.name = new
        return
    if isinstance(value, A.StarTarget):
        if value.name == old:
            value.name = new
        return
    if isinstance(value, list):
        for item in value:
            _rename_identifier(item, old, new)
        return
    if isinstance(value, tuple):
        for item in value:
            _rename_identifier(item, old, new)
        return
    if not is_dataclass(value):
        return

    for descriptor in fields(value):
        name = descriptor.name
        child = getattr(value, name)

        if name in ("target", "var") and isinstance(child, str):
            if child == old:
                setattr(value, name, new)
            continue

        if name == "func" and isinstance(value, A.Call) and isinstance(child, str):
            if child == old:
                value.func = new
            continue

        if name in ("free_vars", "names") and isinstance(child, list):
            for index, item in enumerate(child):
                if item == old:
                    child[index] = new
            continue

        if name == "targets" and isinstance(child, list):
            for index, item in enumerate(child):
                if item == old:
                    child[index] = new
                else:
                    _rename_identifier(item, old, new)
            continue

        _rename_identifier(child, old, new)


def _subtree_contains_identifier(value, identifier: str) -> bool:
    if value is None:
        return False
    if isinstance(value, A.Name):
        return value.name == identifier
    if isinstance(value, A.StarTarget):
        return value.name == identifier
    if isinstance(value, list) or isinstance(value, tuple):
        return any(_subtree_contains_identifier(item, identifier) for item in value)
    if not is_dataclass(value):
        return False

    for descriptor in fields(value):
        name = descriptor.name
        child = getattr(value, name)
        if name in ("target", "var") and child == identifier:
            return True
        if name == "func" and isinstance(value, A.Call) and child == identifier:
            return True
        if name in ("free_vars", "names", "targets") and isinstance(child, list):
            if identifier in child:
                return True
        if _subtree_contains_identifier(child, identifier):
            return True
    return False


def _normalize_method_receivers(mod: A.Module) -> None:
    """Canonicalize arbitrary instance-method receiver names to ``self``.

    Python assigns no semantic meaning to the spelling of the first method
    parameter. asmpython's older field inference and code generation use a
    canonical internal receiver named ``self``. Normalize the AST before those
    passes run so source may use ``this``, ``receiver``, ``mcls``, or any other
    valid identifier without duplicating receiver-name handling throughout the
    compiler.
    """
    if getattr(mod, "_method_receivers_normalized", False):
        return
    mod._method_receivers_normalized = True

    collision_counter = 0
    for owner in mod.classes:
        for method in owner.methods:
            decorators = list(getattr(method, "decorators", []) or [])
            if "staticmethod" in decorators or "classmethod" in decorators:
                continue
            if not method.params:
                # Preserve the existing missing-receiver diagnostic.
                continue

            receiver = method.params[0]
            if receiver == "self":
                continue

            # A method using a separate local/global identifier literally named
            # ``self`` would collide with the compiler's internal canonical name.
            # Alpha-rename those references first so source semantics are kept.
            if _subtree_contains_identifier(method.body, "self"):
                collision_counter += 1
                displaced = f"__asmpython_displaced_self_{collision_counter}"
                _rename_identifier(method.body, "self", displaced)

            method.params[0] = "self"
            _rename_identifier(method.body, receiver, "self")


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


def _lower_static_data_descriptors(mod: A.Module) -> None:
    """Lower statically declared descriptors to shared objects + properties.

    A declaration such as ``value = Descriptor(...)`` is recognized when the
    descriptor class is part of the compiled program and defines ``__get__`` or
    ``__set__``. One module-global descriptor instance is created, ``__set_name__``
    is invoked once when present, and instance reads/writes are routed through the
    compiler's existing ``@property`` getter/setter machinery.

    Native class objects do not exist yet, so the owner argument is represented by
    the class's qualified name string. Descriptors that ignore the owner (the usual
    data-descriptor pattern) behave exactly as expected; no per-instance descriptor
    copies are made.
    """
    if getattr(mod, "_static_descriptors_lowered", False):
        return
    mod._static_descriptors_lowered = True

    descriptor_methods = {
        cls.name: {method.name for method in cls.methods}
        for cls in mod.classes
        if any(method.name in ("__get__", "__set__") for method in cls.methods)
    }
    if not descriptor_methods:
        return

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
            module_init.append(
                A.Assign(target=global_name, value=initializer, pos=owner.pos)
            )
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

            # Preserve class-level identity through the shared module binding.
            rewritten_vars.append(
                (
                    field_name,
                    annotation,
                    A.Name(name=global_name, pos=owner.pos),
                )
            )
        owner.class_vars = rewritten_vars

    if module_init:
        mod.body = module_init + list(mod.body)


def _analyze_with_static_descriptors(self: SemaAnalyzer) -> None:
    _normalize_method_receivers(self.mod)
    _lower_static_data_descriptors(self.mod)
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
