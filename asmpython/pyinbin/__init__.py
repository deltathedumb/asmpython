"""PyinBin bytecode, source frontend, runtime loader, and runtime hooks."""
from __future__ import annotations

import os
from pathlib import Path

from .bytecode import CodeObject, Instruction, Op
from .frontend import PyinbinUnsupportedError, compile_source
from .loader import PyinbinImportError, SourceLoader
from .loader import run_source as _loader_run_source
from . import vm as _vm
from .vm import VirtualMachine, VMError
from asmpython._runtime.mixed_traceback import (
    MixedTracebackError,
    format_mixed_exception,
    get_mixed_traceback,
    install_pyinbin_hooks,
)

# Idempotent: importing PyinBin installs ownership tracking and interpreted-frame
# capture once, while leaving ordinary caught exception semantics unchanged.
install_pyinbin_hooks(_vm)


def run_source(
    path: Path,
    *,
    bundle: Path | None = None,
    import_roots: list[Path] | None = None,
) -> object:
    """Run source with mixed-traceback metadata attached to uncaught errors.

    Library callers receive the original exception type. The public CLI sets
    ``ASMPYTHON_CLI_MIXED_TRACEBACK=1`` so its older fallback/reporting layer
    receives a printable carrier containing the coherent mixed traceback.
    """

    try:
        return _loader_run_source(path, bundle=bundle, import_roots=import_roots)
    except MixedTracebackError:
        raise
    except BaseException as exception:
        get_mixed_traceback(exception, include_host=True)
        if os.environ.get("ASMPYTHON_CLI_MIXED_TRACEBACK") == "1":
            raise MixedTracebackError(exception) from exception
        raise


__all__ = [
    "CodeObject", "Instruction", "MixedTracebackError", "Op",
    "PyinbinImportError", "PyinbinUnsupportedError", "SourceLoader",
    "VirtualMachine", "VMError", "compile_source", "format_mixed_exception",
    "get_mixed_traceback", "run_source",
]
