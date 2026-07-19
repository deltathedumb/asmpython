"""Compatibility surface for the removed asmpython-private PyPI installer.

Python packages now belong to the active interpreter environment and must be
installed with ``python -m pip install``.  The compiler resolves pure-Python
modules directly from that interpreter's site-packages after checking the
bundled asmpython stdlib.

The old functions remain only so older private callers get a precise migration
error instead of an ImportError.  They no longer download, extract, track, list,
or uninstall packages themselves.
"""
from __future__ import annotations

from pathlib import Path

from .site_packages import install_native_import_resolution


class PypiError(Exception):
    pass


def _removed(action: str, name: str | None = None) -> PypiError:
    package = f" {name!r}" if name else ""
    return PypiError(
        f"asmpython pypi {action}{package} was removed; use the active "
        "interpreter's pip instead (`python -m pip install <package>` or "
        "`python -m pip uninstall <package>`)"
    )


def install_pypi_package(
    name: str,
    dest_dir: Path,
    *,
    version: str | None = None,
) -> tuple[str, list[str]]:
    _unused = dest_dir
    _unused = version
    raise _removed("install", name)


def uninstall_pypi_package(name: str, dest_dir: Path) -> list[str]:
    _unused = dest_dir
    raise _removed("uninstall", name)


def list_pypi_packages(dest_dir: Path) -> dict:
    _unused = dest_dir
    raise _removed("list")


# ``_compiler.__main__`` imports this module for backwards-compatible command
# dispatch.  Under normal CPython-hosted compiler execution that import is the
# earliest common point at which program.py is loaded, so install the resolver
# extension here.  The operation is idempotent.
install_native_import_resolution()
