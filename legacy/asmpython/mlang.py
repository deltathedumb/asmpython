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
common case (a real `g++`/`gcc` found on PATH). Also built in:
`ml.builtins.rust` (`rustc`, staticlib output, signature inference
supported) and `ml.builtins.nasm.win64`/`ml.builtins.nasm.elf64` (raw
NASM, one `Config` per object format -- see their own docstring for why
there's no single `ml.builtins.nasm`). C# and Java are NOT built in --
see the note at the bottom of this file for why (a real, structural
toolchain-interop gap, not an oversight). Configure a different toolchain
with `Config(...)` directly:

    zig = ml.Config(
        exe="zig",
        frontend="zig",
        compile_args=["build-obj", "-femit-bin={out}", "{src}"],
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


class _RustFrontend:
    """`ml.builtins.rust` -- a ready-made `Config` for `rustc` found on
    PATH, compiling to a `staticlib` (a real linkable object, not a `.rlib`
    -- `crate-type=staticlib` is what makes `#[no_mangle] pub extern "C"
    fn ...` exports resolvable by the same C ABI/linker step every other
    mlang frontend uses). Opts into signature inference: functions matching
    `#[no_mangle] pub extern "C" fn NAME(...) -> TYPE {` are recognized
    directly from source text (see `mlang_support.py`'s `_RUST_SIG_RE`) --
    a separate grammar from the C-family one, since Rust's syntax doesn't
    match it (`fn`/`->` instead of a leading return type, `pub`/`#[...]`
    attribute lines, etc.).

    `-C panic=abort -C opt-level=2`: without these, even trivial arithmetic
    (`a * b` at debug-mode overflow-check settings) references Rust's
    panic-unwinding runtime (`core::panicking::panic_const_mul_overflow`,
    `core::panicking::panic_cannot_unwind`, ...), which a bare
    `--crate-type staticlib` build never defines -- confirmed via a real
    link failure with these flags absent. `panic=abort` removes the
    unwind-runtime dependency entirely (a panic becomes a hard process
    abort, which is the right semantics for code embedded in a non-Rust
    host binary anyway); `opt-level=2` additionally optimizes away the
    overflow-check branches themselves in the common case."""

    def __new__(cls):
        return Config(
            exe="rustc",
            frontend="rust",
            compile_args=[
                "--crate-type", "staticlib", "--emit", "obj",
                "-C", "panic=abort", "-C", "opt-level=2",
                "-o", "{out}", "{src}",
            ],
            infer_signatures=True,
        )


class _NasmFrontends:
    """`ml.builtins.nasm.win64` / `ml.builtins.nasm.elf64` -- ready-made
    `Config`s for `nasm` found on PATH, targeting the two object formats
    asmpython itself builds for (Windows PE64/COFF and Linux ELF64). Unlike
    every other built-in config, there is no single `ml.builtins.nasm` --
    NASM's object format is a real, unpapered-over target choice (`-f
    win64` vs `-f elf64` produce genuinely different, non-interchangeable
    object files), and `Config`/`Code` resolve fully at compile time with
    no notion of the eventual build's target OS to pick one automatically
    (sema's mlang recognition pass is explicitly target-independent -- see
    `_compiler/sema.py`'s `_inject_mlang_if_needed`). Pick the one matching
    your `--target`, same as you already have to know your target for
    anything else target-specific.

    Signature inference is NOT supported (`infer_signatures=False`): raw
    NASM has no function-signature syntax at all to scan for -- every
    `Code(...)` using either of these needs an explicit `exports=`."""

    win64 = Config(
        exe="nasm",
        frontend="asm",
        compile_args=["-f", "win64", "{src}", "-o", "{out}"],
        infer_signatures=False,
    )
    elf64 = Config(
        exe="nasm",
        frontend="asm",
        compile_args=["-f", "elf64", "{src}", "-o", "{out}"],
        infer_signatures=False,
    )


class _Builtins:
    gcc = _GccFrontends()
    rust = _RustFrontend()
    nasm = _NasmFrontends()


builtins = _Builtins()

# NOTE on C# and Java: neither is a built-in config, and this is a real
# architecture gap, not an oversight.
#
# Both only produce a genuinely linkable object file via ahead-of-time
# native compilation (.NET Native AOT / GraalVM native-image) rather than
# their ordinary managed-runtime compilers (csc/javac output isn't native
# machine code at all, so it can never be linked into a native binary).
#
# GraalVM's native-image has no flag that stops short of a final, fully
# linked executable or shared library -- confirmed via its own
# --help-extra, no object-only output mode exists. There is nothing for
# mlang to extract.
#
# .NET Native AOT DOES leave a real linkable .obj behind (confirmed via a
# real `dotnet publish -p:PublishAot=true` run) -- but that object has
# real external dependencies on ~15 of the AOT runtime's own MSVC-format
# static libraries (GC, PInvoke thunks, exception handling). Cross-linking
# those with asmpython's actual toolchain (GNU ld, via --linker gcc, or
# asmpython's own from-scratch --linker builtin) was tested directly and
# fails on real symbol-resolution errors -- MSVC-produced .lib archives
# and GNU binutils don't reliably interoperate. The only linker that
# reliably links this object is real MSVC link.exe, which asmpython has no
# wrapper for today (only gcc.py and its own builtin PE linker). Adding
# one is a legitimate, scoped follow-up (a new `--linker msvc` backend
# shelling to real link.exe, discoverable via vswhere.exe) -- not
# attempted here, since it's a new linker backend, not an mlang config.
