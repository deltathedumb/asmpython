"""Access declarations; prefer importing these from :mod:`asmpython`."""
from ._api import (
    AccessObject,
    Public,
    Module,
    Package,
    Subclass,
    Class,
    Instance,
    NoAccess,
    access,
)

__all__ = [
    "AccessObject", "Public", "Module", "Package", "Subclass", "Class",
    "Instance", "NoAccess", "access",
]
