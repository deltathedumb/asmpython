"""framebuffer — software pixel rendering for bare-metal and UEFI targets.

Part of Lumen, asmpython's graphics/audio/input ecosystem (gui + framebuffer
+ audio). Provides a Framebuffer class that writes pixels directly to a
linear memory-mapped framebuffer using hardware.mmio_write32 / mmio_write8.
No OS, no SDL2, no dependencies beyond the hardware module.

Typical UEFI GOP setup::

    import framebuffer
    # addr, width, height, and pitch come from the GOP FrameBufferBase,
    # HorizontalResolution, VerticalResolution, and PixelsPerScanLine fields.
    fb = framebuffer.Framebuffer(0x80000000, 1920, 1080, 7680, 32)
    fb.clear(framebuffer.BLACK)
    fb.fill_rect(100, 100, 400, 300, framebuffer.BLUE)

The color format for 32 bpp is 0x00RRGGBB (little-endian stores it as
BGRX in memory, matching the typical UEFI PixelBlueGreenRedReserved format).
For UEFI GOP use bgr() to pack colors for the wire format.
"""
from __future__ import annotations

import hardware
import _font8x8


def _fb_isqrt(n: int) -> int:
    if n <= 0:
        return 0
    x: int = n
    y: int = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x


class Framebuffer:
    """Linear pixel framebuffer.  Supports 32 bpp and 8 bpp modes."""

    def __init__(self, addr: int, width: int, height: int, pitch: int, bpp: int) -> int:
        self._addr: int = addr
        self._w: int = width
        self._h: int = height
        self._pitch: int = pitch  # bytes per scanline (may include padding)
        self._bpp: int = bpp      # bits per pixel (32 or 8)
        return 0

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h

    def put_pixel(self, x: int, y: int, color: int) -> int:
        """Write one pixel.  Silently clipped if outside the framebuffer."""
        if x < 0:
            return 0
        if x >= self._w:
            return 0
        if y < 0:
            return 0
        if y >= self._h:
            return 0
        if self._bpp == 8:
            hardware.mmio_write8(self._addr + y * self._pitch + x, color)
        else:
            hardware.mmio_write32(self._addr + y * self._pitch + x * 4, color)
        return 0

    def clear(self, color: int = 0) -> int:
        """Fill the entire framebuffer with color."""
        row: int = 0
        while row < self._h:
            col: int = 0
            row_base: int = self._addr + row * self._pitch
            while col < self._w:
                hardware.mmio_write32(row_base + col * 4, color)
                col = col + 1
            row = row + 1
        return 0

    def fill_rect(self, x: int, y: int, w: int, h: int, color: int) -> int:
        """Draw a filled axis-aligned rectangle."""
        # Clip to framebuffer
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
        row: int = y0
        while row <= y1:
            row_base: int = self._addr + row * self._pitch
            col: int = x0
            while col <= x1:
                hardware.mmio_write32(row_base + col * 4, color)
                col = col + 1
            row = row + 1
        return 0

    def draw_rect(self, x: int, y: int, w: int, h: int, color: int) -> int:
        """Draw the outline of an axis-aligned rectangle."""
        self.fill_rect(x, y, w, 1, color)
        self.fill_rect(x, y + h - 1, w, 1, color)
        self.fill_rect(x, y, 1, h, color)
        self.fill_rect(x + w - 1, y, 1, h, color)
        return 0

    def _hline(self, x0: int, x1: int, y: int, color: int) -> int:
        if y < 0:
            return 0
        if y >= self._h:
            return 0
        if x0 < 0:
            x0 = 0
        if x1 >= self._w:
            x1 = self._w - 1
        if x0 > x1:
            return 0
        row_base: int = self._addr + y * self._pitch
        x: int = x0
        while x <= x1:
            hardware.mmio_write32(row_base + x * 4, color)
            x = x + 1
        return 0

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: int) -> int:
        """Draw a line using Bresenham's algorithm."""
        dx: int = x1 - x0
        dy: int = y1 - y0
        adx: int = dx
        if adx < 0:
            adx = -adx
        ady: int = dy
        if ady < 0:
            ady = -ady
        sx: int = 1
        if x0 > x1:
            sx = -1
        sy: int = 1
        if y0 > y1:
            sy = -1
        steps: int = adx
        if ady > adx:
            steps = ady
        err: int = adx - ady
        i: int = 0
        while i <= steps:
            self.put_pixel(x0, y0, color)
            e2: int = 2 * err
            if e2 > -ady:
                err = err - ady
                x0 = x0 + sx
            if e2 < adx:
                err = err + adx
                y0 = y0 + sy
            i = i + 1
        return 0

    def draw_circle(self, cx: int, cy: int, r: int, color: int) -> int:
        """Draw the outline of a circle using the Bresenham midpoint algorithm."""
        x: int = 0
        y: int = r
        d: int = 3 - 2 * r
        while x <= y:
            self.put_pixel(cx + x, cy + y, color)
            self.put_pixel(cx - x, cy + y, color)
            self.put_pixel(cx + x, cy - y, color)
            self.put_pixel(cx - x, cy - y, color)
            self.put_pixel(cx + y, cy + x, color)
            self.put_pixel(cx - y, cy + x, color)
            self.put_pixel(cx + y, cy - x, color)
            self.put_pixel(cx - y, cy - x, color)
            if d < 0:
                d = d + 4 * x + 6
            else:
                d = d + 4 * (x - y) + 10
                y = y - 1
            x = x + 1
        return 0

    def fill_circle(self, cx: int, cy: int, r: int, color: int) -> int:
        """Draw a filled circle using horizontal scanlines."""
        dy: int = -r
        while dy <= r:
            dx: int = _fb_isqrt(r * r - dy * dy)
            self._hline(cx - dx, cx + dx, cy + dy, color)
            dy = dy + 1
        return 0

    def draw_char(self, x: int, y: int, ch: str, color: int, scale: int = 1) -> int:
        """Draw one character from the built-in 8x8 bitmap font at (x, y)."""
        code: int = ord(ch)
        if code < 32:
            return 0
        if code > 126:
            return 0
        base: int = (code - 32) * 8
        row: int = 0
        while row < 8:
            bits: int = _font8x8._FONT[base + row]
            col: int = 0
            while col < 8:
                if (bits >> (7 - col)) & 1:
                    if scale == 1:
                        self.put_pixel(x + col, y + row, color)
                    else:
                        self.fill_rect(x + col * scale, y + row * scale, scale, scale, color)
                col = col + 1
            row = row + 1
        return 0

    def draw_text(self, x: int, y: int, text: str, color: int, scale: int = 1) -> int:
        """Draw a string using the built-in 8x8 bitmap font, left to right."""
        i: int = 0
        cx: int = x
        cw: int = 8 * scale
        while i < len(text):
            ch: str = text[i]
            if ch == "\n":
                cx = x
                y = y + cw
            else:
                self.draw_char(cx, y, ch, color, scale)
                cx = cx + cw
            i = i + 1
        return 0

    def draw_triangle(self, x1: int, y1: int, x2: int, y2: int, x3: int, y3: int, color: int) -> int:
        """Draw the outline of a triangle."""
        self.draw_line(x1, y1, x2, y2, color)
        self.draw_line(x2, y2, x3, y3, color)
        self.draw_line(x3, y3, x1, y1, color)
        return 0

    def fill_triangle(self, x1: int, y1: int, x2: int, y2: int, x3: int, y3: int, color: int) -> int:
        """Draw a filled triangle via scanline rasterization."""
        ax: int = x1
        ay: int = y1
        bx: int = x2
        by: int = y2
        cx: int = x3
        cy: int = y3
        tmp: int = 0
        # Sort vertices by y ascending
        if ay > by:
            tmp = ax
            ax = bx
            bx = tmp
            tmp = ay
            ay = by
            by = tmp
        if by > cy:
            tmp = bx
            bx = cx
            cx = tmp
            tmp = by
            by = cy
            cy = tmp
        if ay > by:
            tmp = ax
            ax = bx
            bx = tmp
            tmp = ay
            ay = by
            by = tmp
        if cy == ay:
            return 0
        if by > ay:
            dy: int = ay
            while dy <= by:
                xl: int = ax + (dy - ay) * (cx - ax) // (cy - ay)
                xr: int = ax + (dy - ay) * (bx - ax) // (by - ay)
                if xl > xr:
                    tmp = xl
                    xl = xr
                    xr = tmp
                self._hline(xl, xr, dy, color)
                dy = dy + 1
        if cy > by:
            dy = by
            while dy <= cy:
                xl = ax + (dy - ay) * (cx - ax) // (cy - ay)
                xr = bx + (dy - by) * (cx - bx) // (cy - by)
                if xl > xr:
                    tmp = xl
                    xl = xr
                    xr = tmp
                self._hline(xl, xr, dy, color)
                dy = dy + 1
        return 0


# ---- Color packing helpers ------------------------------------------------

def rgb(r: int, g: int, b: int) -> int:
    """Pack r, g, b as a 32-bit pixel value (0x00RRGGBB).

    A little-endian 32-bit write of 0x00RRGGBB places Blue in memory byte 0,
    Green in byte 1, Red in byte 2 — matching the UEFI GOP
    PixelBlueGreenRedReserved (BGR) format used by most UEFI systems.
    Use this for the common case.
    """
    return (r << 16) | (g << 8) | b


def bgr(r: int, g: int, b: int) -> int:
    """Pack r, g, b for framebuffers with RGB byte order (Red in memory byte 0).

    Returns 0x00BBGGRR.  Use when your hardware uses PixelRedGreenBlueReserved.
    """
    return (b << 16) | (g << 8) | r


# ---- Named colors (0x00RRGGBB) -------------------------------------------
BLACK:      int = 0x000000
WHITE:      int = 0xFFFFFF
RED:        int = 0xFF0000
GREEN:      int = 0x00FF00
BLUE:       int = 0x0000FF
YELLOW:     int = 0xFFFF00
CYAN:       int = 0x00FFFF
MAGENTA:    int = 0xFF00FF
ORANGE:     int = 0xFF8000
PURPLE:     int = 0x800080
GRAY:       int = 0x808080
DARK_GRAY:  int = 0x404040
LIGHT_GRAY: int = 0xC0C0C0
PINK:       int = 0xFF80C0
BROWN:      int = 0x8B4513
SKY:        int = 0x87CEEB
NAVY:       int = 0x000080
LIME:       int = 0x00FF80
TEAL:       int = 0x008080
GOLD:       int = 0xFFD700
CRIMSON:    int = 0xDC143C
