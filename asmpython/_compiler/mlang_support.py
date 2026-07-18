"""Compile-time support for `asmpython.mlang`: shelling out to a configured
external compiler on a `Code(...)` source string, and (for configs opting
into it) inferring each exported function's signature via a restricted
C-family grammar scan of the source text -- see `asmpython/mlang.py`'s
module docstring for the full user-facing contract.

Entry point: `_run_mlang_code(config, source, exports)` -> `MlangResult`.
Called from `sema.py`'s mlang recognition hook once per distinct `Code(...)`
literal found in the module being compiled. Memoized by
`(config.exe, config.frontend, config.compile_args, source)` so an
unchanged snippet across repeated analysis passes (or repeated builds in
the same process) doesn't reinvoke the external compiler.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class MlangError(Exception):
    """Raised when the configured compiler fails, or a Code(...) with
    infer_signatures=True has no functions the restricted grammar can
    recognize (and no explicit `exports=` was given either)."""


@dataclass(frozen=True)
class MlangFuncSig:
    arg_types: tuple[str, ...]
    ret_type: str


@dataclass(frozen=True)
class MlangResult:
    """One compiled `Code(...)` literal's result: the object file bytes
    (already `extern "C"`-safe -- inferred functions are re-wrapped before
    compilation if not already, see `_wrap_extern_c`) plus each exported
    function's real C ABI symbol name and signature."""

    obj_bytes: bytes
    obj_ext: str  # ".o" or ".obj", matching what the configured compiler emits
    funcs: "dict[str, MlangFuncSig]"  # name -> signature, c_name == name always


# Restricted C-family top-level function-signature grammar: matches
#   [extern "C"] TYPE NAME ( [TYPE NAME[, TYPE NAME]*] ) {
# at the START of a line (mod leading whitespace) -- deliberately narrow
# (see mlang.py's docstring): no templates, no default args, no overloads,
# no multi-line signatures, no pointer/reference/const-qualified types
# beyond the fixed vocabulary below. Anything not matching this shape is
# invisible to inference and needs an explicit `exports=` entry instead.
_C_TYPE_MAP = {
    "int": "int",
    "long": "int",
    "long long": "int",
    "float": "float",
    "double": "float",
    "void": None,  # only valid as a return type
}
_TYPE_ALT = "|".join(sorted(_C_TYPE_MAP, key=len, reverse=True))
_SIG_RE = re.compile(
    r'^\s*(?:extern\s+"C"\s*)?\s*(' + _TYPE_ALT + r')\s+(\w+)\s*\(\s*([^)]*)\s*\)\s*\{',
    re.MULTILINE,
)
_PARAM_RE = re.compile(r'^\s*(' + _TYPE_ALT + r')\s+\w+\s*$')


def _infer_signatures(source: str) -> "dict[str, MlangFuncSig]":
    funcs: dict[str, MlangFuncSig] = {}
    for m in _SIG_RE.finditer(source):
        ret_c, name, params_text = m.group(1), m.group(2), m.group(3).strip()
        ret_ty = _C_TYPE_MAP[ret_c]
        if ret_ty is None:
            # `void` return isn't representable in asmpython's arg_types/
            # ret_type vocabulary (every stdlib.Func-shaped binding has a
            # real ret_type) -- skip; the caller can still declare it
            # explicitly via exports= if a void-returning call is needed.
            continue
        if not params_text or params_text == "void":
            arg_types: tuple[str, ...] = ()
        else:
            arg_types_list: list[str] = []
            ok = True
            for part in params_text.split(","):
                pm = _PARAM_RE.match(part)
                if not pm:
                    ok = False
                    break
                arg_types_list.append(_C_TYPE_MAP[pm.group(1)])
            if not ok:
                continue
            arg_types = tuple(arg_types_list)
        funcs[name] = MlangFuncSig(arg_types=arg_types, ret_type=ret_ty)
    return funcs


def _wrap_extern_c(source: str, func_names: "set[str]") -> str:
    """Re-emit `source` with every inferred top-level function wrapped in
    `extern "C"` if it isn't already, so the compiled object's symbol name
    exactly equals the parsed name -- no C++ name-mangling ambiguity to
    resolve on the calling side. A no-op for functions the regex matched
    with a leading `extern "C"` already present."""
    def _add_extern_c(m: re.Match) -> str:
        whole = m.group(0)
        if 'extern "C"' in whole:
            return whole
        return 'extern "C" ' + whole

    return _SIG_RE.sub(_add_extern_c, source)


_CACHE: "dict[str, MlangResult]" = {}


def _cache_key(exe: str, frontend: str, compile_args: tuple, source: str) -> str:
    h = hashlib.sha256()
    h.update(exe.encode("utf-8"))
    h.update(b"\0")
    h.update(frontend.encode("utf-8"))
    h.update(b"\0")
    for a in compile_args:
        h.update(a.encode("utf-8"))
        h.update(b"\0")
    h.update(source.encode("utf-8"))
    return h.hexdigest()


def _run_mlang_code(
    exe: str,
    frontend: str,
    compile_args: tuple,
    infer_signatures: bool,
    source: str,
    exports: "dict[str, MlangFuncSig]",
) -> MlangResult:
    """Compile `source` with the configured tool, returning the object
    bytes plus each exported function's resolved signature (explicit
    `exports` entries win over inferred ones for the same name)."""
    key = _cache_key(exe, frontend, compile_args, source)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    inferred: dict[str, MlangFuncSig] = {}
    if infer_signatures:
        inferred = _infer_signatures(source)
    funcs = dict(inferred)
    funcs.update(exports)
    if not funcs:
        raise MlangError(
            f"mlang Code(...) (frontend={frontend!r}) has no exported "
            f"functions: no explicit exports= given, and either "
            f"infer_signatures is off for this Config or the restricted "
            f"grammar scan found nothing to infer."
        )

    compile_source = source
    if infer_signatures and inferred:
        compile_source = _wrap_extern_c(source, set(inferred))

    with tempfile.TemporaryDirectory(prefix="asmpython_mlang_") as tmpdir:
        ext = {"cpp": ".cpp", "c": ".c"}.get(frontend, ".txt")
        src_path = Path(tmpdir) / f"code{ext}"
        src_path.write_text(compile_source, encoding="utf-8")
        obj_ext = ".obj" if os.name == "nt" else ".o"
        out_path = Path(tmpdir) / f"code{obj_ext}"

        cmd = [exe] + [
            a.replace("{src}", str(src_path)).replace("{out}", str(out_path))
            for a in compile_args
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise MlangError(
                f"mlang: {exe} failed compiling an embedded {frontend} "
                f"Code(...) snippet (exit {proc.returncode}):\n{proc.stderr}"
            )
        if not out_path.exists():
            raise MlangError(
                f"mlang: {exe} exited 0 but produced no object file at "
                f"{out_path} -- check this Config's compile_args."
            )
        obj_bytes = out_path.read_bytes()

    result = MlangResult(obj_bytes=obj_bytes, obj_ext=obj_ext, funcs=funcs)
    _CACHE[key] = result
    return result
