"""Declared surface and source ordering of the experimental ARM64 runtime.

Each freestanding assembly slice owns an explicit export set. The derived flat
constants remain the public compatibility surface used by the linker, while the
per-slice records let the builder reject source/manifest drift before invoking
any external tool.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSlice:
    filename: str
    exports: frozenset[str]


RUNTIME_SLICES = (
    RuntimeSlice(
        "abi_shims_linux_arm64.S",
        frozenset({"_abi_int_to_base", "printf", "strlen"}),
    ),
    RuntimeSlice(
        "abi_lists_linux_arm64.S",
        frozenset(
            {
                "_abi_list_append",
                "_abi_list_extend",
                "_abi_list_insert",
                "_abi_list_pop",
                "_abi_list_repeat",
                "_abi_list_reverse",
                "_abi_new_list",
            }
        ),
    ),
    RuntimeSlice(
        "abi_float_scalar_linux_arm64.S",
        frozenset(
            {
                "ceil",
                "copysign",
                "fabs",
                "fdim",
                "floor",
                "nearbyint",
                "nextafter",
                "trunc",
            }
        ),
    ),
    RuntimeSlice(
        "abi_float_ulp_linux_arm64.S",
        frozenset({"_math_ulp"}),
    ),
    RuntimeSlice(
        "abi_float_classify_linux_arm64.S",
        frozenset({"_math_isfinite", "_math_isinf", "_math_isnan"}),
    ),
    RuntimeSlice(
        "abi_float_angles_linux_arm64.S",
        frozenset({"_math_degrees", "_math_radians"}),
    ),
    RuntimeSlice(
        "abi_float_modf_linux_arm64.S",
        frozenset({"_math_modf_frac", "_math_modf_int"}),
    ),
    RuntimeSlice(
        "abi_float_frexp_linux_arm64.S",
        frozenset({"_math_frexp_e", "_math_frexp_m"}),
    ),
    RuntimeSlice(
        "abi_int_math_linux_arm64.S",
        frozenset({"_math_gcd", "_math_lcm"}),
    ),
    RuntimeSlice(
        "abi_strings_linux_arm64.S",
        frozenset(
            {
                "_abi_hash_string",
                "_abi_str_cmp",
                "_abi_str_concat",
                "_abi_str_concat_dup",
                "_abi_str_eq",
                "_abi_str_removeprefix",
                "_abi_str_removesuffix",
                "_abi_str_repeat",
                "labs",
            }
        ),
    ),
    RuntimeSlice(
        "abi_string_search_linux_arm64.S",
        frozenset(
            {
                "_abi_str_count",
                "_abi_str_ends_with",
                "_abi_str_index_of",
                "_abi_str_index_of_start",
                "_abi_str_rindex_of",
                "_abi_str_starts_with",
            }
        ),
    ),
    RuntimeSlice(
        "abi_string_replace_linux_arm64.S",
        frozenset({"_abi_str_replace"}),
    ),
    RuntimeSlice(
        "abi_string_slice_linux_arm64.S",
        frozenset({"_abi_str_slice"}),
    ),
    RuntimeSlice(
        "abi_string_padding_linux_arm64.S",
        frozenset({"_abi_str_zfill"}),
    ),
)

RUNTIME_SOURCE_NAMES = tuple(runtime_slice.filename for runtime_slice in RUNTIME_SLICES)
RUNTIME_EXPORTS = frozenset(
    symbol
    for runtime_slice in RUNTIME_SLICES
    for symbol in runtime_slice.exports
)


def declared_global_symbols(source: str) -> frozenset[str]:
    declared: set[str] = set()
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not (line.startswith(".global ") or line.startswith(".globl ")):
            continue
        payload = line.split(None, 1)[1]
        for symbol in payload.replace(",", " ").split():
            declared.add(symbol)
    return frozenset(declared)


def validate_manifest_shape() -> None:
    filenames: set[str] = set()
    owners: dict[str, str] = {}
    for runtime_slice in RUNTIME_SLICES:
        if runtime_slice.filename in filenames:
            raise ValueError(
                f"duplicate ARM64 runtime source {runtime_slice.filename!r}"
            )
        filenames.add(runtime_slice.filename)
        for symbol in runtime_slice.exports:
            previous = owners.get(symbol)
            if previous is not None:
                raise ValueError(
                    f"ARM64 runtime export {symbol!r} is assigned to both "
                    f"{previous!r} and {runtime_slice.filename!r}"
                )
            owners[symbol] = runtime_slice.filename


def validate_slice_source(runtime_slice: RuntimeSlice, source: str) -> None:
    declared = declared_global_symbols(source)
    missing = runtime_slice.exports - declared
    unexpected = declared - runtime_slice.exports
    if not missing and not unexpected:
        return
    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(sorted(missing)))
    if unexpected:
        details.append("unexpected " + ", ".join(sorted(unexpected)))
    raise ValueError(
        f"ARM64 runtime source {runtime_slice.filename!r} does not match its "
        f"manifest ({'; '.join(details)})"
    )


validate_manifest_shape()
