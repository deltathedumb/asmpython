"""Public API for embedding and calling into another language's source,
compiled by an external, configurable compiler as part of the asmpython
build.

    import asmpython.mlang as ml

    code = ml.Code(
        ml.builtins.gcc.cpp,
        "extern \"C\" int add(int a, int b) { return a + b; }",
    )
    result = code.add(1, 2)

`ml.builtins.gcc.cpp`/`ml.builtins.gcc.c` are ready-made `Config`s for the
common case (a real `g++`/`gcc` found on PATH). Configure a different
toolchain with `Config(...)` directly:

    rust = ml.Config(
        exe="rustc",
        frontend="rust",
        compile_args=["--crate-type", "staticlib", "-o", "{out}", "{src}"],
    )

`ml.Code(config, source[, exports=...])` compiles `source` with `config`'s
tool as a real step of the asmpython build (never at runtime -- there is no
runtime compiler dependency in the produced executable). `exports` declares
each callable's signature explicitly (mirrors `asmpython.stdlib.Func`'s own
`arg_types`/`ret_type` shape):

    code = ml.Code(
        my_config, source,
        exports={"add": Sig(["int", "int"], "int")},
    )

Signature INFERENCE (no `exports` needed) is supported for configs that
opt in via `Config(..., infer_signatures=True)` -- true for every built-in
`ml.builtins.*` config. Inference parses a restricted C-family function-
signature grammar directly from the source text (no real C++ parser, no
name-demangler): each top-level function is matched against
`[extern "C"] TYPE NAME(TYPE, TYPE, ...) {`, and every matched function is
wrapped in `extern "C"` before compilation (if not already) so the
compiled object's symbol name exactly equals the parsed name, with no
mangling ambiguity to resolve. This only sees plain top-level function
definitions -- templates, overloads, classes, and anything the restricted
grammar doesn't match are invisible to inference and need an explicit
`exports` entry instead.

Under CPython, `Code`/`Config` are inert data holders -- `code.add(1, 2)`
raises: the compiled artifact only exists after a real asmpython build.
The actual compiler invocation, signature inference, and call marshaling
happen in the asmpython compiler itself (`asmpython/_compiler/mlang_support.py`'s
`_run_mlang_code`), triggered when it recognizes an `asmpython.mlang`
import and a `Code(...)` construction in the source being compiled.
"""

from __future__ import annotations

from dataclasses import dataclass, field


__all__ = ["Config", "Code", "Sig", "builtins"]


@dataclass(frozen=True)
class Sig:
    """One exported function's signature: `arg_types` each `"int"`/
    `"float"`/`"str"` (same vocabulary as `asmpython.stdlib.Func`), `ret_type`
    likewise."""

    arg_types: tuple[str, ...]
    ret_type: str

    def __init__(self, arg_types, ret_type: str) -> None:
        object.__setattr__(self, "arg_types", tuple(arg_types))
        object.__setattr__(self, "ret_type", ret_type)


@dataclass(frozen=True)
class Config:
    """One compiler toolchain configuration.

    `exe`: the compiler executable (found on PATH, or an absolute path).
    `frontend`: a short label for the source language/dialect (e.g. "cpp",
    "c") -- purely descriptive plus a dispatch key for `infer_signatures`'s
    restricted-grammar parser, which currently only understands the C/C++
    family grammar. A `Config` for a language whose signature grammar isn't
    C-family-shaped must set `infer_signatures=False` and always require
    `exports=` on its `Code(...)` instances.
    `compile_args`: argv template compiling one source file to one object
    file. `{src}`/`{out}` are substituted with the real temp file paths.
    `infer_signatures`: whether `Code(...)` may omit `exports=` and rely on
    the restricted-grammar source scan instead. Only meaningful for
    C-family `frontend`s today.
    """

    exe: str
    frontend: str
    compile_args: tuple[str, ...]
    infer_signatures: bool = False

    def __init__(
        self,
        exe: str,
        frontend: str,
        compile_args,
        infer_signatures: bool = False,
    ) -> None:
        object.__setattr__(self, "exe", exe)
        object.__setattr__(self, "frontend", frontend)
        object.__setattr__(self, "compile_args", tuple(compile_args))
        object.__setattr__(self, "infer_signatures", infer_signatures)


class Code:
    """A source string compiled by `config`'s tool as a real build step,
    exposing its exported functions as callable attributes.

    Under CPython this object is inert (no compiler runs, no attribute is
    callable) -- see this module's docstring. The asmpython compiler is
    what actually shells out, infers or validates signatures, and rewires
    attribute access into real calls.
    """

    def __init__(self, config: Config, source: str, exports: "dict[str, Sig] | None" = None) -> None:
        self.config = config
        self.source = source
        self.exports = dict(exports or {})
        if not self.exports and not config.infer_signatures:
            raise ValueError(
                f"Code(...) using a Config with infer_signatures=False "
                f"(frontend={config.frontend!r}) requires an explicit "
                f"exports={{...}} mapping -- no signature can be inferred."
            )

    def __getattr__(self, name: str):
        def _uncompiled_call(*_args, **_kwargs):
            raise RuntimeError(
                f"mlang Code.{name}(...) has no compiled artifact under "
                f"plain CPython execution; it only becomes callable after "
                f"a real asmpython build."
            )

        return _uncompiled_call

    def __repr__(self) -> str:
        return f"<mlang.Code frontend={self.config.frontend!r} exports={sorted(self.exports)}>"


class _GccFrontends:
    """`ml.builtins.gcc.cpp` / `ml.builtins.gcc.c` -- ready-made `Config`s
    for a `g++`/`gcc` found on PATH. Both opt into signature inference: the
    restricted grammar parser understands plain C-shaped declarations,
    which both frontends' exported functions are required to use (see this
    module's docstring) regardless of which frontend compiles them."""

    cpp = Config(
        exe="g++",
        frontend="cpp",
        compile_args=["-c", "-x", "c++", "{src}", "-o", "{out}"],
        infer_signatures=True,
    )
    c = Config(
        exe="gcc",
        frontend="c",
        compile_args=["-c", "-x", "c", "{src}", "-o", "{out}"],
        infer_signatures=True,
    )


class _Builtins:
    gcc = _GccFrontends()


builtins = _Builtins()
