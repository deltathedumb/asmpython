"""asmpython: native Python -> x86-64 -> executable transpiler.

The public surface is small and deliberately mirrors what a user writes:

    from asmpython.assembly import asm_func

`@asm_func` marks a function whose body is raw NASM (the compiler emits it
verbatim). The compiler internals live under the private `_compiler`,
`_runtime`, and `_stdlib` subpackages.

Plugin authoring (compiler-syntax extensions, codegen backends, linkers,
and embedded other-language source via `mlang`) is organized as one
namespace submodule per concern -- each exposes its own registration
class(es), accessed off the top-level package:

    import asmpython

    asmpython.extend.Extension(id="my_feature", ...)
    asmpython.backend.Backend(name="my_backend", impl=...)
    asmpython.linker.Linker(name="my_linker", impl=...)
    asmpython.mlang.Config(...)  # embed/compile another language's source

Each submodule (`asmpython.extend`, `asmpython.backend`, `asmpython.linker`,
`asmpython.mlang`) is importable on its own (`import asmpython.backend`) or
reached as an attribute after `import asmpython`, matching ordinary Python
package semantics -- there is no flat top-level `asmpython.Backend`/
`asmpython.Linker`/`asmpython.Extension` shorthand.
"""

from . import backend, extend, linker, mlang

__version__ = "3.14-preview"

__all__ = ["backend", "extend", "linker", "mlang", "__version__"]
