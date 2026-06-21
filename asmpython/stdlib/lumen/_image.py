"""gui._image — SDL2 texture wrapper.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

import _gui_sdl


class Image:
    """SDL2 texture.  Obtain via Canvas.image(path); free with img.free()."""

    def __init__(self) -> int:
        self._tex: int = 0
        self._w: int = 0
        self._h: int = 0
        return 0

    def _setup(self, tex: int, w: int, h: int) -> int:
        self._tex = tex
        self._w = w
        self._h = h
        return 0

    def w(self) -> int:
        return self._w

    def h(self) -> int:
        return self._h

    def free(self) -> int:
        _gui_sdl.destroy_texture(self._tex)
        self._tex = 0
        return 0
