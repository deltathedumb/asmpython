"""Central ASMPython release identity.

Global release identifiers use ``<python-language-version>-<asmpython-semver>``.
The Python prefix declares language compatibility; the ASMPython portion follows
ordinary semantic versioning and does not reset when the Python target changes.
"""

from __future__ import annotations

PYTHON_LANGUAGE_VERSION = "3.14"
ASMPYTHON_VERSION = "2.0.0"

# Canonical public release spelling used by GitHub releases, tags, branches,
# downloadable artifacts, manifests, checksums, provenance, and documentation.
FULL_VERSION = f"{PYTHON_LANGUAGE_VERSION}-{ASMPYTHON_VERSION}"
RELEASE_VERSION = FULL_VERSION

# Python package indexes require the project version itself to remain a normal
# PEP 440 / semantic version. The Python compatibility prefix is published as
# separate metadata and combined into FULL_VERSION on public release surfaces.
PACKAGING_VERSION = ASMPYTHON_VERSION

# ``__version__`` follows the installed ASMPython package version. Use
# ``FULL_VERSION`` when displaying or recording the complete release identity.
__version__ = ASMPYTHON_VERSION

PYTHON_VERSION_INFO = (3, 14)
VERSION_INFO = (2, 0, 0)
FULL_VERSION_INFO = (*PYTHON_VERSION_INFO, *VERSION_INFO)

__all__ = [
    "ASMPYTHON_VERSION",
    "FULL_VERSION",
    "FULL_VERSION_INFO",
    "PACKAGING_VERSION",
    "PYTHON_LANGUAGE_VERSION",
    "PYTHON_VERSION_INFO",
    "RELEASE_VERSION",
    "VERSION_INFO",
    "__version__",
]
