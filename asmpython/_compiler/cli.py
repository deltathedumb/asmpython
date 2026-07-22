"""Stable, patchable public facade over the host-only CLI runtime."""
from __future__ import annotations

import os

# Install shared policy extensions before plans, lockfiles, or the runtime import
# their original modules.
from . import negotiation_ext as _negotiation_ext  # noqa: F401
from . import build_lock_ext as _build_lock_ext  # noqa: F401
from . import fast_state_ext as _fast_state_ext  # noqa: F401
from . import cli_runtime as _runtime


MANAGEMENT_COMMANDS = _runtime.MANAGEMENT_COMMANDS
_call_legacy_with_static_project_policy = _runtime._call_legacy_with_static_project_policy
_legacy_cli = _runtime._legacy_cli
prepare_argv = _runtime.prepare_argv
source_tree_uses_dynamic_import = _runtime.source_tree_uses_dynamic_import
source_uses_dynamic_import = _runtime.source_uses_dynamic_import


def main(argv: list[str] | None = None) -> int:
    # Each invocation resolves these values from its own config/profile/CLI.
    # Clearing them prevents embedded callers from inheriting a previous build.
    os.environ.pop("ASMPYTHON_TARGET_TRIPLE", None)
    os.environ.pop("ASMPYTHON_FAST_STATE", None)

    # Preserve the existing testing/embedding contract: callers can monkeypatch
    # ``asmpython._compiler.cli.prepare_argv`` and the active invocation observes
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
