"""Compatibility support for ``str()`` on finite class values.

Python code commonly accepts either a class object or a string name::

    def resolve(value):
        if isinstance(value, type):
            return value
        return lookup(str(value))

Whole-program inference may correctly identify ``value`` as ``type`` at a
class-valued call site.  The fallback string conversion is still valid Python
and may be unreachable for that specialization, so semantic analysis must not
reject the function body merely because class objects use the compiler's
opaque RTTI representation.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..sema import SemaAnalyzer


_ORIGINAL_CHECK_CALL = SemaAnalyzer._check_call


def _check_call_with_class_strings(self: SemaAnalyzer, call: A.Call, scope) -> None:
    if call.func == "str" and len(call.args) == 1:
        self._check_expr(call.args[0], scope)
        if A.expr_type(call.args[0]) == "type":
            # A class value is an opaque RTTI-sized scalar in the native
            # runtime.  Accept the conversion so guarded fallback branches can
            # be compiled; direct class literals are separately lowered by the
            # finite-class-value pass where their identity is statically known.
            call.inferred_type = "str"
            call._str_class_value = True  # type: ignore[attr-defined]
            return
    _ORIGINAL_CHECK_CALL(self, call, scope)


if not getattr(SemaAnalyzer, "_asmpython_class_string_patch", False):
    SemaAnalyzer._check_call = _check_call_with_class_strings
    SemaAnalyzer._asmpython_class_string_patch = True
