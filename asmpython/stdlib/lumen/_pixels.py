"""gui._pixels — CPU-side pixel buffer for fast bulk drawing.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.

Canvas.pixel(x, y) issues one SDL_RenderDrawPoint call per pixel — fine for
a handful of points, far too slow for anything that touches every pixel on
screen (raycasting, software 3D, procedural textures, per-pixel effects).
PixelBuffer gives you a plain w*h array of packed 0xRRGGBB ints you can
write directly with set(), then upload to the screen in one call via
Canvas.blit_pixels() (SDL_UpdateTexture). For bare-metal-style direct
memory access (matching framebuffer.Framebuffer's hardware.mmio_write32
API), raw_addr() returns the buffer's real memory address.
"""
from __future__ import annotations

import _gui_sdl


class PixelBuffer:
    """A w x h array of packed 0xRRGGBB pixels you can write directly.

    Construct once, write into it with set() (or poke raw_addr() directly
    for MMIO-style access), then blit the whole thing to a Canvas in one
    call with canvas.blit_pixels(buf, x, y).
    """

    def __init__(self, w: int, h: int) -> int:
        self._w: int = w
        self._h: int = h
        self._pixels: list[int] = []
        i: int = 0
        n: int = w * h
        while i < n:
            self._pixels.append(0)
            i = i + 1
        self._tex: int = 0
        self._ren: int = 0
        return 0

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h

    def set(self, x: int, y: int, color: int) -> int:
        """Write one pixel (packed 0xRRGGBB). Silently clipped if out of bounds."""
        if x < 0:
            return 0
        if x >= self._w:
            return 0
        if y < 0:
            return 0
        if y >= self._h:
            return 0
        self._pixels[y * self._w + x] = 0xFF000000 | color
        return 0

    def get(self, x: int, y: int) -> int:
        """Read back one pixel as a packed 0xRRGGBB int (alpha byte stripped)."""
        return self._pixels[y * self._w + x] & 0x00FFFFFF

    def clear(self, color: int = 0) -> int:
        """Fill the whole buffer with one color."""
        packed: int = 0xFF000000 | color
        i: int = 0
        n: int = self._w * self._h
        while i < n:
            self._pixels[i] = packed
            i = i + 1
        return 0

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> int:
        """Fill an axis-aligned rectangle directly in the buffer (clipped)."""
        x0: int = x
        y0: int = y
        x1: int = x + w - 1
        y1: int = y + h - 1
        if x0 < 0:
            x0 = 0
        if y0 < 0:
            y0 = 0
        if x1 >= self._w:
            x1 = self._w - 1
        if y1 >= self._h:
            y1 = self._h - 1
        if x0 > x1:
            return 0
        if y0 > y1:
            return 0
        packed: int = 0xFF000000 | color
        row: int = y0
        while row <= y1:
            base: int = row * self._w
            col: int = x0
            while col <= x1:
                self._pixels[base + col] = packed
                col = col + 1
            row = row + 1
        return 0

    def raw_addr(self) -> int:
        """Real memory address of the pixel buffer's backing storage.

        Each pixel is a 4-byte 0xAARRGGBB word (4 * width() * height() bytes
        total, row-major). Usable with hardware.mmio_write32-style direct
        writes for bare-metal-equivalent code, or pass directly to
        SDL_UpdateTexture (which Canvas.blit_pixels() does for you).
        """
        return _gui_sdl.list_buf_addr(self._pixels)

    def pitch(self) -> int:
        """Bytes per row — width() * 4, for code calling update_texture directly."""
        return self._w * 4

    def _bind(self, ren: int) -> int:
        """Lazily create (or recreate, if the renderer changed) the streaming
        texture this buffer uploads into. Called by Canvas.blit_pixels()."""
        if self._tex != 0 and self._ren == ren:
            return 0
        if self._tex != 0:
            _gui_sdl.destroy_texture(self._tex)
        self._tex = _gui_sdl.create_texture_argb(
            ren, _gui_sdl.TEXTUREACCESS_STREAMING, self._w, self._h
        )
        self._ren = ren
        return 0

    def free(self) -> int:
        """Destroy the backing streaming texture, if one was ever created."""
        if self._tex != 0:
            _gui_sdl.destroy_texture(self._tex)
            self._tex = 0
        return 0
