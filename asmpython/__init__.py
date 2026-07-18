"""asmpython: native Python -> x86-64 -> executable transpiler.

The public surface is small and deliberately mirrors what a user writes:

    from asmpython.assembly import asm_func

`@asm_func` marks a function whose body is raw NASM (the compiler emits it
verbatim). The compiler internals live under the private `_compiler`,
`_runtime`, and `_stdlib` subpackages.

Plugin authoring (compiler-syntax extensions, codegen backends, linkers) --
see `asmpython.extend` for the full contract:

    import asmpython

    asmpython.Extension(id="my_feature", ...)
    asmpython.Backend(name="my_backend", impl=...)
    asmpython.Linker(name="my_linker", impl=...)
"""

from .extend import Backend, Extension, Linker

__version__ = "3.14-preview"

__all__ = ["Backend", "Extension", "Linker", "__version__"]
