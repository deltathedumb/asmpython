"""Declared surface and source ordering of the experimental ARM64 runtime.

Keeping this in one small module makes each runtime expansion an auditable
manifest change instead of requiring edits throughout the linker implementation.
The assembled merged runtime is still independently checked against this set.
"""
from __future__ import annotations


RUNTIME_EXPORTS = frozenset(
    {
        "_abi_int_to_base",
        "_abi_str_cmp",
        "_abi_str_concat",
        "_abi_str_concat_dup",
        "_abi_str_count",
        "_abi_str_ends_with",
        "_abi_str_eq",
        "_abi_str_repeat",
        "_abi_str_starts_with",
        "labs",
        "printf",
        "strlen",
    }
)

RUNTIME_SOURCE_NAMES = (
    "abi_shims_linux_arm64.S",
    "abi_strings_linux_arm64.S",
    "abi_string_search_linux_arm64.S",
)
