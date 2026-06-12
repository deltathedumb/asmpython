"""asmpython: native Python -> x86-64 -> executable transpiler.

The public surface is small and deliberately mirrors what a user writes:

    from asmpython.assembly import assembly_func, include

`assembly_func` marks a function whose body is raw NASM (the compiler emits it
verbatim); `include` pulls in a custom assembly package (`.asmpkg`). The
compiler internals live under the private `_compiler`, `_runtime`, and
`_stdlib` subpackages.
"""

__version__ = "1.0.0"
