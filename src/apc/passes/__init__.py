"""IR -> IR transforms and the manager that sequences them.

    from apc.passes import PassManager
    pm = PassManager.from_names(["constfold", "copyprop", "dce"],
                                verify_each=True)
    pm.run(module)

A pass declares what it requires, provides and invalidates; the manager
rejects an impossible ORDER before running anything, so a bad pipeline is a
startup error naming both passes rather than a crash halfway through.
"""
from .manager import (
    KNOWN_TAGS, Pass, PassManager, PassResult, available, get, register,
)
from . import transforms  # noqa: F401  -- registers the built-in passes

__all__ = [
    "KNOWN_TAGS", "Pass", "PassManager", "PassResult", "available", "get",
    "register",
]
