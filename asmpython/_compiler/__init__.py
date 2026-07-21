"""asmpython compiler front-end: lex -> parse -> sema -> NASM codegen.

This is the private implementation package. The user-facing API lives in
`asmpython` (the parent package) and `asmpython.assembly`.
"""

from .. import __version__  # re-export the single source of truth
from . import linux_runtime_fixes as _linux_runtime_fixes
from . import language_compat_fixes as _language_compat_fixes
from . import metaclass_compat_fixes as _metaclass_compat_fixes
from . import class_registry_compat_fixes as _class_registry_compat_fixes
from . import program_compat_fixes as _program_compat_fixes
from . import analysis_compat_fixes as _analysis_compat_fixes
from . import object_flow_compat_fixes as _object_flow_compat_fixes
from . import dynamic_parameter_compat_fixes as _dynamic_parameter_compat_fixes
from . import type_parameter_compat_fixes as _type_parameter_compat_fixes
from . import field_flow_compat_fixes as _field_flow_compat_fixes
from . import container_field_compat_fixes as _container_field_compat_fixes
from . import live_definition_compat_fixes as _live_definition_compat_fixes
from . import empty_collection_compat_fixes as _empty_collection_compat_fixes
from . import ordered_flow_compat_fixes as _ordered_flow_compat_fixes
from . import descriptor_precedence_compat_fixes as _descriptor_precedence_compat_fixes
from . import return_annotation_precedence_compat_fixes as _return_annotation_precedence_compat_fixes
from . import class_value_compat_fixes as _class_value_compat_fixes
from . import class_string_compat_fixes as _class_string_compat_fixes
from . import global_return_flow_compat_fixes as _global_return_flow_compat_fixes
from . import iterable_element_compat_fixes as _iterable_element_compat_fixes
from . import chained_receiver_compat_fixes as _chained_receiver_compat_fixes
from . import boolop_value_compat_fixes as _boolop_value_compat_fixes
from . import dynamic_index_assignment_compat_fixes as _dynamic_index_assignment_compat_fixes
from . import issubclass_compat_fixes as _issubclass_compat_fixes

__all__ = ["__version__"]
