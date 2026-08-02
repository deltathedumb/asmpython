"""asmpython compiler front-end: lex -> parse -> sema -> NASM codegen.

This is the private implementation package. The user-facing API lives in
`asmpython` (the parent package) and `asmpython.assembly`.
"""

from .. import __version__  # re-export the single source of truth
# `linux_runtime_fixes` is applied by _targets/target_linux.py, at the end of
# the module that defines the class it patches -- not here. Importing it here
# meant importing a target to import the compiler, which is the dependency the
# target registry exists to remove, and it made `_targets.target_linux` a
# circular import when reached first. Applying it where the class is defined
# also means every route to LinuxCodegen gets the patched class.
from .compat import language_compat_fixes as _language_compat_fixes
from .compat import metaclass_compat_fixes as _metaclass_compat_fixes
from .compat import class_registry_compat_fixes as _class_registry_compat_fixes
from .compat import program_compat_fixes as _program_compat_fixes
from .compat import analysis_compat_fixes as _analysis_compat_fixes
from .compat import object_flow_compat_fixes as _object_flow_compat_fixes
from .compat import dynamic_parameter_compat_fixes as _dynamic_parameter_compat_fixes
from .compat import type_parameter_compat_fixes as _type_parameter_compat_fixes
from .compat import field_flow_compat_fixes as _field_flow_compat_fixes
from .compat import container_field_compat_fixes as _container_field_compat_fixes
from .compat import live_definition_compat_fixes as _live_definition_compat_fixes
from .compat import empty_collection_compat_fixes as _empty_collection_compat_fixes
from .compat import ordered_flow_compat_fixes as _ordered_flow_compat_fixes
from .compat import descriptor_precedence_compat_fixes as _descriptor_precedence_compat_fixes
from .compat import return_annotation_precedence_compat_fixes as _return_annotation_precedence_compat_fixes
from .compat import class_value_compat_fixes as _class_value_compat_fixes
from .compat import class_string_compat_fixes as _class_string_compat_fixes
from .compat import global_return_flow_compat_fixes as _global_return_flow_compat_fixes
from .compat import iterable_element_compat_fixes as _iterable_element_compat_fixes
from .compat import chained_receiver_compat_fixes as _chained_receiver_compat_fixes
from .compat import boolop_value_compat_fixes as _boolop_value_compat_fixes
from .compat import dynamic_index_assignment_compat_fixes as _dynamic_index_assignment_compat_fixes
from .compat import issubclass_compat_fixes as _issubclass_compat_fixes
from .compat import inherited_classmethod_compat_fixes as _inherited_classmethod_compat_fixes
from .compat import dynamic_classvar_compat_fixes as _dynamic_classvar_compat_fixes
from .compat import iter_next_compat_fixes as _iter_next_compat_fixes
from . import public_abi_exports as _public_abi_exports

__all__ = ["__version__"]
