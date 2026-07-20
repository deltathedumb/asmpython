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

__all__ = ["__version__"]
