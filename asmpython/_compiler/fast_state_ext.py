"""FastComp cache hardening installed by the public CLI facade."""
from __future__ import annotations

from . import fast_state as _base


_original_prepare_state = _base.prepare_state


def prepare_state(*args, **kwargs):
    state = _original_prepare_state(*args, **kwargs)
    if not state.hit:
        # The manifest and parsed graph were just refreshed. Old lowered state
        # belongs to the previous dependency snapshot and must never become a hit
        # on the next invocation merely because the source is now unchanged.
        for name in ("optimized-ir.pkl", "backend-state.pkl"):
            try:
                (state.directory / name).unlink()
            except FileNotFoundError:
                pass
        state.ir = None
        state.backend_state = None
    return state


_base.prepare_state = prepare_state


__all__ = ["prepare_state"]
