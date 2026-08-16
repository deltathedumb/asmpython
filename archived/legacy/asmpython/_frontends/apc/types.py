"""APC type names -> neutral IR types.

``bool`` and ``int`` map to ``i64`` deliberately: that is what
``ir.ir_type_for`` already does for the Python frontend, so both frontends
agree on the width a boolean or a plain integer occupies.
"""

from __future__ import annotations

from ..._compiler.ssa.ir import (
    F32, F64, I8, I16, I32, I64, PTR, U8, U16, U32, U64, IRType,
)

SCALARS: dict[str, IRType] = {
    "i8": I8, "i16": I16, "i32": I32, "i64": I64,
    "u8": U8, "u16": U16, "u32": U32, "u64": U64,
    "f32": F32, "f64": F64,
    "int": I64, "uint": U64, "float": F64, "bool": I64,
    "ptr": PTR,
    # A `string` is a pointer to NUL-terminated bytes -- literals live in
    # .rodata, so nothing owns them and no allocation is implied.
    "string": PTR, "bytes": PTR,
}

VOID_NAMES = frozenset({"none", "void"})

# Width -> the unsigned type a raw layout field reads back as. Interpretation
# is the use site's job (`as`), so the default is the widest-safe unsigned read.
_UNSIGNED_BY_SIZE: dict[int, IRType] = {1: U8, 2: U16, 4: U32, 8: U64}


def scalar(name: str) -> IRType | None:
    return SCALARS.get(name)


def is_void(name: str | None) -> bool:
    return name is None or name in VOID_NAMES


def unsigned_of_size(nbytes: int) -> IRType | None:
    return _UNSIGNED_BY_SIZE.get(nbytes)


def layout_field_size(type_name: str) -> int | None:
    """Byte size of a layout field's declared type, or None if it has none."""
    if type_name.startswith("bytes[") and type_name.endswith("]"):
        inner = type_name[len("bytes["):-1].strip()
        if not inner:
            return None
        try:
            return int(inner, 0)
        except ValueError:
            return None
    ty = SCALARS.get(type_name)
    if ty is not None and ty.name != "ptr":
        return ty.size_bytes
    if ty is not None:
        return 8
    return None


__all__ = [
    "SCALARS",
    "VOID_NAMES",
    "is_void",
    "layout_field_size",
    "scalar",
    "unsigned_of_size",
]
