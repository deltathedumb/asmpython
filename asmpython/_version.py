"""Central ASMPython release identity.

Public versions use ``<python-language-version>-<asmpython-build>``.  The build
number is a single monotonically increasing counter for public ASMPython
releases; it does not reset when the supported Python language version changes.
"""

from __future__ import annotations

PYTHON_LANGUAGE_VERSION = "3.14"
ASMPYTHON_BUILD = 1

# Canonical user-facing spelling used by the CLI, tags, manifests, and docs.
__version__ = f"{PYTHON_LANGUAGE_VERSION}-{ASMPYTHON_BUILD}"

# PEP 440 accepts the canonical spelling and normalizes it to 3.14.post1 in
# installed-package metadata. Keep the source spelling here so release tooling
# can verify that every surface identifies the same public release.
PACKAGING_VERSION = __version__

VERSION_INFO = (3, 14, ASMPYTHON_BUILD)

__all__ = [
    "ASMPYTHON_BUILD",
    "PACKAGING_VERSION",
    "PYTHON_LANGUAGE_VERSION",
    "VERSION_INFO",
    "__version__",
]
