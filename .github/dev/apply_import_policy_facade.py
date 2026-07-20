from __future__ import annotations

from pathlib import Path


HOST_SIGNATURE_OLD = '''def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
'''

HOST_SIGNATURE_NEW = '''def main(
    argv: list[str] | None = None,
    *,
    prepare: object = None,
) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
'''

HOST_PREPARE_OLD = '''    try:
        prepared = prepare_argv(raw)
    except SitePackageImportError as error:
'''

HOST_PREPARE_NEW = '''    prepare_call = prepare_argv if prepare is None else prepare
    try:
        prepared = prepare_call(raw)
    except SitePackageImportError as error:
'''

FACADE = '''"""Public CLI facade; host-only policy code lives under `_backends`."""
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
'''


def main() -> None:
    host = Path("asmpython/_backends/host_cli.py")
    text = host.read_text(encoding="utf-8")
    if HOST_SIGNATURE_OLD in text:
        text = text.replace(HOST_SIGNATURE_OLD, HOST_SIGNATURE_NEW, 1)
    elif HOST_SIGNATURE_NEW not in text:
        raise RuntimeError("host CLI main signature changed")
    if HOST_PREPARE_OLD in text:
        text = text.replace(HOST_PREPARE_OLD, HOST_PREPARE_NEW, 1)
    elif HOST_PREPARE_NEW not in text:
        raise RuntimeError("host CLI preparation boundary changed")
    host.write_text(text, encoding="utf-8")

    Path("asmpython/_compiler/cli.py").write_text(FACADE, encoding="utf-8")


if __name__ == "__main__":
    main()
