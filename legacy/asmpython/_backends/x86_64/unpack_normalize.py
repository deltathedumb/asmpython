"""Compatibility facade for the target-neutral typed-unpack prepass."""
from ..._compiler.unpack_normalize import (
    install_ir_lowering_prepass as install,
    normalize_typed_unpacks as normalize_literal_unpacks,
)

__all__ = ["install", "normalize_literal_unpacks"]
