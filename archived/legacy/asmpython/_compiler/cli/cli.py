"""Stable, patchable public facade over the host-only CLI runtime."""
from __future__ import annotations

import os
import sys

# Install shared policy extensions before plans, lockfiles, or the runtime import
# their original modules.
from .. import target_triple_ext as _target_triple_ext  # noqa: F401
from ..build import negotiation_ext as _negotiation_ext
from ..build import build_lock_ext as _build_lock_ext
from ..incremental import fast_state_ext as _fast_state_ext  # noqa: F401
from ..incremental import fastcomp_bridge as _fastcomp_bridge  # noqa: F401
from . import cli_runtime as _runtime


MANAGEMENT_COMMANDS = _runtime.MANAGEMENT_COMMANDS
_call_legacy_with_static_project_policy = _runtime._call_legacy_with_static_project_policy
_legacy_cli = _runtime._legacy_cli
prepare_argv = _runtime.prepare_argv
source_tree_uses_dynamic_import = _runtime.source_tree_uses_dynamic_import
source_uses_dynamic_import = _runtime.source_uses_dynamic_import


def _project_target_triple(argv: list[str]) -> list[str]:
    """Give the old parser a staging target while preserving the full triple."""
    result = list(argv)
    for index, token in enumerate(result[:-1]):
        if token != "--target":
            continue
        value = result[index + 1]
        parts = value.split("-")
        if len(parts) != 3:
            continue
        platform, system, _abi = parts
        os.environ["ASMPYTHON_TARGET_TRIPLE"] = value
        if system in {"windows", "linux"}:
            staging = system
        elif system in {"bios", "uefi16"}:
            staging = "freestanding16"
        elif system in {"none", "baremetal", "embedded"} or platform == "embedded":
            staging = "freestanding"
        else:
            # Registered backends receive target_triple and own final emission;
            # the old frontend only needs a valid staging OS for path defaults.
            staging = "windows" if sys.platform == "win32" else "linux"
        result[index + 1] = staging
    return result


_runtime._legacy_target_argv = _project_target_triple


def main(argv: list[str] | None = None) -> int:
    # Each invocation resolves these values from its own config/profile/CLI.
    # Clearing them prevents embedded callers from inheriting a previous build.
    os.environ.pop("ASMPYTHON_TARGET_TRIPLE", None)
    os.environ.pop("ASMPYTHON_FAST_STATE", None)

    # Preserve the existing testing/embedding contract: callers can monkeypatch
    # ``asmpython._compiler.cli.cli.prepare_argv`` and the active invocation observes
    # that replacement.
    _runtime.prepare_argv = prepare_argv
    return _runtime.main(argv)


__all__ = [
    "MANAGEMENT_COMMANDS",
    "main",
    "prepare_argv",
    "source_tree_uses_dynamic_import",
    "source_uses_dynamic_import",
]
