"""ABI declarations; prefer importing these from :mod:`asmpython`."""
from ._api import ABIObject, AutoABI, C, System, ASMPython, abi, packed, aligned, transparent, opaque

__all__ = [
    "ABIObject", "AutoABI", "C", "System", "ASMPython", "abi",
    "packed", "aligned", "transparent", "opaque",
]
