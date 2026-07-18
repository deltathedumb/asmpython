"""Compiler-extension system -- DISABLED.

asmpython's job is to compile the Python subset people actually write; the
opt-in `--ext`/`--apm` compiler-syntax-extension system (enum/interface/
overload/access-modifiers/etc., 16 built-ins as of the last active version)
added new keywords and semantics Python itself doesn't have, which cuts
against that goal even though every extension was itself opt-in. The
feature has been withdrawn: no extension can be activated, `--ext`/`--apm`
no longer exist as CLI flags, and this module is now a thin, permanently-
empty stand-in kept only so `Parser.__init__`'s `extensions.ExtensionContext()`
call (and any stray code still checking `ext_ctx.is_active(...)`) keeps
working without edits, always reporting nothing active.

The real implementation -- registry, activation/retraction, conflict and
dependency checking, the 16 built-in extensions, and the public
`asmpython.extend.Extension(...)` plugin-authoring API -- is preserved
as-is under `archived/extensions/` for reference. Nothing there is wired
up to the compiler anymore.
"""

from __future__ import annotations

from typing import Optional

from .errors import ErrorCode, ParseError, SourcePos


class ExtensionError(ParseError):
    """Kept for compatibility with any code still catching this type."""


class CompilerExtension:
    """Kept for compatibility. No built-in subclasses are registered."""

    name: str = ""
    version: str = "1.0"
    requires: dict = {}
    conflicts: set = set()

    def statement_handlers(self) -> dict:
        return {}

    def activate(self, context: "ExtensionContext") -> None:
        pass

    def deactivate(self, context: "ExtensionContext") -> None:
        pass


_REGISTRY: dict = {}


def register_extension(cls_or_instance):
    """No-op: the extension system is disabled, so registration always
    fails loudly rather than silently accepting a plugin that can never
    activate."""
    raise RuntimeError(
        "asmpython's compiler-extension system has been disabled -- "
        "see archived/extensions/ for the withdrawn implementation"
    )


class ExtensionContext:
    """Always empty. `activate()` unconditionally refuses, so no source
    file and no CLI flag can ever turn on non-standard grammar."""

    def __init__(self) -> None:
        self._active: dict = {}
        self._activation_order: list = []
        self._stmt_handlers: dict = {}

    def is_active(self, name: str) -> bool:
        return False

    def handler_for(self, keyword: str):
        return None

    def activate(self, name: str, pos: Optional[SourcePos] = None) -> None:
        raise ExtensionError(
            f"extension {name!r} cannot be activated -- asmpython's "
            "compiler-extension system has been disabled",
            pos,
            ErrorCode.P_UNKNOWN_EXTENSION,
        )

    def retract(self, name: str, pos: Optional[SourcePos] = None) -> None:
        raise ExtensionError(
            f"extension {name!r} is not active",
            pos,
            ErrorCode.P_EXTENSION_NOT_ACTIVE,
        )
