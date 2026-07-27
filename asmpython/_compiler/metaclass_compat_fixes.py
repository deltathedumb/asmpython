"""Compile-time lowering for deterministic descriptor-collecting metaclasses.

asmpython has no runtime class objects, so arbitrary metaclass execution remains
out of scope.  This pass recognizes a narrow, common static pattern:

* ``__new__`` builds a dictionary,
* values satisfying ``isinstance(value, DescriptorType)`` are collected,
* the dictionary is assigned to ``cls.<metadata_attr>``, and
* a classmethod returns ``dict(cls.<metadata_attr>)``.

The equivalent inherited metadata dictionaries and receiver-specific reflection
methods are materialized before ordinary semantic analysis.  The recognized
``__new__`` body is then replaced with a harmless stub because its supported
semantics have already been executed by this pass.
"""

from __future__ import annotations

from . import ast_nodes as A
from .language_compat_fixes import _lower_static_data_descriptors
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _walk_expr(expr):
    if expr is None:
        return
    yield expr
    for attr in (
        "obj",
        "value",
        "left",
        "right",
        "operand",
        "func_expr",
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
        for child in getattr(expr, attr, None) or []:
            if child is not None:
                yield from _walk_expr(child)
    for _name, child in getattr(expr, "kwargs", None) or []:
        yield from _walk_expr(child)


def _walk_stmts(stmts):
    for stmt in stmts:
        yield stmt
        for attr in ("expr", "value", "test", "iter", "target", "subject"):
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


def _descriptor_bindings(mod: A.Module) -> tuple:
    """Return ``(bindings, descriptor_globals, descriptor_types, init_offset)``.

    The descriptor pass has already rewritten every descriptor class variable to
    a module-global ``A.Name``.  Recover the original descriptor class from the
    corresponding top-level constructor assignment.
    """
    global_types: dict = {}
    for stmt in mod.body:
        if (
            isinstance(stmt, A.Assign)
            and stmt.target.startswith("__asmpy_descriptor_")
            and isinstance(stmt.value, A.Call)
        ):
            global_types[stmt.target] = stmt.value.func

    bindings: dict = {}
    for cls in mod.classes:
        for field_name, _annotation, value in cls.class_vars:
            if isinstance(value, A.Name) and value.name in global_types:
                bindings[(cls.name, field_name)] = (
                    value.name,
                    global_types[value.name],
                )
    # descriptor_precedence_compat_fixes strips descriptor class vars once they
    # become @property shadows, so the class_vars scan above misses any owner
    # whose fields were all removed -- leaving `bindings` empty and silently
    # disabling metaclass metadata materialization (the reflected classmethod
    # then reads a null class attribute and faults). _lower_static_data_
    # descriptors records the authoritative (owner, field) -> (global, type)
    # map as it creates each descriptor global; fold in any binding the
    # class_vars scan didn't already recover.
    for (owner_name, field_name), (global_name, type_name) in getattr(
        mod, "_descriptor_field_bindings", {}
    ).items():
        bindings.setdefault((owner_name, field_name), (global_name, type_name))

    descriptor_globals = set(global_types)
    init_offset = 0
    for index, stmt in enumerate(mod.body):
        owns_descriptor = (
            isinstance(stmt, A.Assign) and stmt.target in descriptor_globals
        )
        calls_set_name = (
            isinstance(stmt, A.ExprStmt)
            and isinstance(stmt.expr, A.MethodCall)
            and stmt.expr.method == "__set_name__"
            and isinstance(stmt.expr.obj, A.Name)
            and stmt.expr.obj.name in descriptor_globals
        )
        if owns_descriptor or calls_set_name:
            init_offset = index + 1

    return bindings, descriptor_globals, set(global_types.values()), init_offset


def _find_specs(mod: A.Module, descriptor_types: set) -> dict:
    """Find ``metaclass -> (metadata_attribute, descriptor_type)`` patterns."""
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
            test = getattr(stmt, "test", None)
            for expr in _walk_expr(test):
                if (
                    isinstance(expr, A.Call)
                    and expr.func == "isinstance"
                    and len(expr.args) == 2
                    and isinstance(expr.args[1], A.Name)
                    and expr.args[1].name in descriptor_types
                ):
                    descriptor_type = expr.args[1].name

        if metadata_attr is not None and descriptor_type is not None:
            specs[meta.name] = (metadata_attr, descriptor_type, new_method)
    return specs


def _is_reflection_method(method, metadata_attr: str) -> bool:
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
    source = call.args[0]
    return (
        isinstance(source, A.Attr)
        and source.name == metadata_attr
        and isinstance(source.obj, A.Name)
        and source.obj.name == method.params[0]
    )


def _generated_reflection_method(source, metadata_global: str, pos):
    return A.FuncDef(
        name=source.name,
        params=list(source.params),
        body=[
            A.Return(
                value=A.Call(
                    func="dict",
                    args=[A.Name(name=metadata_global, pos=pos)],
                    pos=pos,
                ),
                pos=pos,
            )
        ],
        pos=pos,
        defaults=list(source.defaults),
        param_types=list(source.param_types),
        ret_type=("dict", None),
        vararg=source.vararg,
        kwarg=source.kwarg,
        decorators=["classmethod"],
    )


def _lower_static_metaclasses(mod: A.Module) -> None:
    if getattr(mod, "_static_metaclass_metadata_lowered", False):
        return
    mod._static_metaclass_metadata_lowered = True

    bindings, _globals, descriptor_types, init_offset = _descriptor_bindings(mod)
    if not bindings:
        return
    specs = _find_specs(mod, descriptor_types)
    if not specs:
        return

    class_table = {cls.name: cls for cls in mod.classes}
    original_methods = {cls.name: list(cls.methods) for cls in mod.classes}

    participants: dict = {}
    changed = True
    while changed:
        changed = False
        for cls in mod.classes:
            spec = None
            if cls.metaclass in specs:
                spec = specs[cls.metaclass][:2]
            elif cls.parent in participants:
                spec = participants[cls.parent]
            if spec is not None and cls.name not in participants:
                participants[cls.name] = spec
                changed = True
    if not participants:
        return

    # The recognized metaclass body has been statically evaluated.  Leaving its
    # dynamic namespace/type machinery in the runtime method would either fail
    # semantic analysis or duplicate the compile-time work.
    for _meta_name, (_attr, _descriptor_type, new_method) in specs.items():
        new_method.body = [
            A.Return(value=A.IntLit(value=0, pos=new_method.pos), pos=new_method.pos)
        ]
        new_method.ret_type = ("int", None)
        if "classmethod" not in new_method.decorators:
            new_method.decorators = list(new_method.decorators) + ["classmethod"]

    cache: dict = {}

    def inherited_bindings(class_name: str, active: set) -> list:
        if class_name in cache:
            return list(cache[class_name])
        if class_name in active:
            return []
        active = set(active)
        active.add(class_name)
        cls = class_table[class_name]
        values: list = []
        if cls.parent in participants:
            values.extend(inherited_bindings(cls.parent, active))
        for (owner_name, field_name), binding in bindings.items():
            if owner_name == class_name:
                values = [item for item in values if item[0] != field_name]
                values.append((field_name, binding[0], binding[1]))
        cache[class_name] = list(values)
        return values

    metadata_init: list = []
    metadata_attrs = {spec[0] for spec in participants.values()}
    reflection_names: dict = {}
    for metadata_attr in metadata_attrs:
        names = set()
        for methods in original_methods.values():
            for method in methods:
                if _is_reflection_method(method, metadata_attr):
                    names.add(method.name)
        reflection_names[metadata_attr] = names

    for cls in mod.classes:
        if cls.name not in participants:
            continue
        metadata_attr, descriptor_type = participants[cls.name]
        entries = inherited_bindings(cls.name, set())
        suffix = metadata_attr.strip("_") or "metadata"
        metadata_global = f"__asmpy_metadata_{cls.name}_{suffix}"
        metadata_init.append(
            A.Assign(
                target=metadata_global,
                value=A.DictLit(
                    keys=[
                        A.StrLit(value=field_name, pos=cls.pos)
                        for field_name, _global_name, _type_name in entries
                    ],
                    values=[
                        A.Name(name=global_name, pos=cls.pos)
                        for _field_name, global_name, _type_name in entries
                    ],
                    pos=cls.pos,
                    value_type=f"instance:{descriptor_type}",
                ),
                pos=cls.pos,
            )
        )

        replaced_attr = False
        class_vars: list = []
        for name, annotation, value in cls.class_vars:
            if name == metadata_attr:
                class_vars.append(
                    (name, annotation, A.Name(name=metadata_global, pos=cls.pos))
                )
                replaced_attr = True
            else:
                class_vars.append((name, annotation, value))
        if not replaced_attr:
            class_vars.append(
                (metadata_attr, None, A.Name(name=metadata_global, pos=cls.pos))
            )
        cls.class_vars = class_vars

        for method_name in reflection_names.get(metadata_attr, set()):
            source = None
            current = cls
            seen = set()
            while current is not None and current.name not in seen:
                seen.add(current.name)
                declared = None
                for method in original_methods.get(current.name, []):
                    if method.name == method_name:
                        declared = method
                        break
                if declared is not None:
                    if _is_reflection_method(declared, metadata_attr):
                        source = declared
                    break
                current = class_table.get(current.parent)
            if source is None:
                continue

            generated = _generated_reflection_method(source, metadata_global, cls.pos)
            replaced = False
            for index, method in enumerate(cls.methods):
                if method.name == method_name:
                    cls.methods[index] = generated
                    replaced = True
                    break
            if not replaced:
                cls.methods.append(generated)

    if metadata_init:
        mod.body = (
            list(mod.body[:init_offset])
            + metadata_init
            + list(mod.body[init_offset:])
        )


def _analyze_with_static_metaclasses(self: SemaAnalyzer) -> None:
    _lower_static_data_descriptors(self.mod)
    _lower_static_metaclasses(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_static_metaclass_patch", False):
    SemaAnalyzer.analyze = _analyze_with_static_metaclasses
    SemaAnalyzer._asmpython_static_metaclass_patch = True
