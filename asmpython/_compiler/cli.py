"""Public CLI facade; host-only policy code lives under `_backends`."""
from __future__ import annotations

from asmpython._backends import host_cli as _host_cli


_call_legacy_with_static_project_policy = (
    _host_cli._call_legacy_with_static_project_policy
)
_legacy_cli = _host_cli._legacy_cli
prepare_argv = _host_cli.prepare_argv
source_tree_uses_dynamic_import = _host_cli.source_tree_uses_dynamic_import
source_uses_dynamic_import = _host_cli.source_uses_dynamic_import


def main(argv: list[str] | None = None) -> int:
    """Run the host CLI through the facade's current policy bindings.

    Keeping this as a wrapper, rather than a copied function object, means
    embedders and tests can replace ``prepare_argv`` on the public facade and
    the active invocation observes that replacement. The host controller still
    defaults to its own resolver when called directly.
    """
    return _host_cli.main(argv, prepare=prepare_argv)


__all__ = [
    "main",
    "prepare_argv",
    "source_tree_uses_dynamic_import",
    "source_uses_dynamic_import",
]
