"""asmpython: native Python -> x86-64 -> executable transpiler.

The public surface is small and deliberately mirrors what a user writes:

    from asmpython.assembly import asm_func

`@asm_func` marks a function whose body is raw NASM (the compiler emits it
verbatim). The compiler internals live under the private `_compiler`,
`_runtime`, and `_stdlib` subpackages.

Plugin authoring (codegen backends, linkers, and embedded other-language
source via `mlang`) is organized as one namespace submodule per concern --
each exposes its own registration class(es), accessed off the top-level
package:

    import asmpython

    asmpython.backend.Backend(name="my_backend", impl=...)
    asmpython.linker.Linker(name="my_linker", impl=...)
    asmpython.mlang.Config(...)  # embed/compile another language's source

Each submodule (`asmpython.backend`, `asmpython.linker`, `asmpython.mlang`)
is importable on its own (`import asmpython.backend`) or reached as an
attribute after `import asmpython`, matching ordinary Python package
semantics -- there is no flat top-level `asmpython.Backend`/
`asmpython.Linker` shorthand.

(Compiler-syntax extensions -- `asmpython.extend.Extension(...)` -- were
withdrawn: asmpython's goal is mirroring CPython's language with only tiny,
necessary differences, and letting the grammar itself be extended cut
against that. The withdrawn implementation is preserved for reference under
`archived/extensions/`.)
"""

from . import backend, linker, mlang

__version__ = "3.14-preview"

__all__ = ["backend", "linker", "mlang", "__version__"]
