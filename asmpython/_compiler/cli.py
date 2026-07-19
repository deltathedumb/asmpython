"""Public CLI facade; host-only policy code lives under `_backends`."""
from asmpython._backends.host_cli import (
    _call_legacy_with_static_project_policy,
    _legacy_cli,
    main,
    prepare_argv,
    source_tree_uses_dynamic_import,
    source_uses_dynamic_import,
)

__all__ = [
    "main",
    "prepare_argv",
    "source_tree_uses_dynamic_import",
    "source_uses_dynamic_import",
]
