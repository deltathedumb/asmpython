"""Bootstrap PortaPy embedding surface.

The interpreter semantics remain in Python source.  This package is the in-tree
reference used to stabilize the public API before the separately versioned
PortaPy project is cut from pyinbin's reusable core.
"""
from .reference_api import (
    ErrorInfo,
    Runtime,
    Status,
    ValueKind,
)

__all__ = ["ErrorInfo", "Runtime", "Status", "ValueKind"]
