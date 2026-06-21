"""gui._font — SDL2_ttf TrueType font wrapper.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

import _gui_ttf


class Font:
    """TrueType font loaded via SDL2_ttf.

    Renders smooth, anti-aliased text at any size/style, unlike the built-in
    8x8 bitmap font used by Canvas.text(). Requires SDL2_ttf to be installed
    (Linux: sudo apt install libsdl2-ttf-dev; Windows: SDL2_ttf.dll next to
    the executable). Free with font.close() when no longer needed.
    """

    def __init__(self, path: str, ptsize: int) -> int:
        _gui_ttf.init()
        self._font: int = _gui_ttf.open_font(path, ptsize)
        return 0

    def set_style(self, style: int) -> int:
        """style is one of the gui.FONT_STYLE_* constants (OR-able)."""
        return _gui_ttf.set_font_style(self._font, style)

    def size(self, text: str) -> tuple[int, int]:
        """Return the (width, height) in pixels text would occupy if rendered."""
        return (_gui_ttf.size_text_w(self._font, text), _gui_ttf.size_text_h(self._font, text))

    def close(self) -> int:
        _gui_ttf.close_font(self._font)
        self._font = 0
        return 0
