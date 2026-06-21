"""gui._canvas — hardware-accelerated 2D surface backed by an SDL2 window.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

import _gui_sdl
import _gui_ttf
import _font8x8

from ._constants import (
    INIT_VIDEO, WINDOW_CENTERED, WINDOW_SHOWN, WINDOW_FULLSCREEN_DESKTOP,
    RENDERER_ACCELERATED, EVENT_QUIT, EVENT_KEYDOWN, EVENT_MOUSEMOTION,
    EVENT_MOUSEBUTTONDOWN,
)
from ._colors import r, g, b
from ._image import Image
from ._font import Font
from ._pixels import PixelBuffer


def _isqrt(n: int) -> int:
    if n <= 0:
        return 0
    x: int = n
    y: int = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x


class Canvas:
    """2D drawing surface backed by an SDL2 window.

    Call update() once per frame inside your main loop — it presents the
    current frame, drains the event queue, and returns 1 while the window is
    open (0 when the user closes it or break is desired).
    """

    def __init__(self, title: str, w: int, h: int) -> int:
        _gui_sdl.init(INIT_VIDEO)
        self._win: int = _gui_sdl.create_window(
            title,
            WINDOW_CENTERED, WINDOW_CENTERED,
            w, h,
            WINDOW_SHOWN,
        )
        self._ren: int = _gui_sdl.create_renderer(self._win, -1, RENDERER_ACCELERATED)
        self._w: int = w
        self._h: int = h
        self._running: int = 1
        self._last_key: int = 0
        self._mx: int = 0
        self._my: int = 0
        self._mbtn: int = 0
        return 0

    def color(self, packed: int, a: int = 255) -> int:
        """Set draw color from a packed 0xRRGGBB integer."""
        _gui_sdl.set_draw_color(self._ren, r(packed), g(packed), b(packed), a)
        return 0

    def rgb(self, r_: int, g_: int, b_: int, a: int = 255) -> int:
        """Set draw color from individual r, g, b (and optional alpha) components."""
        _gui_sdl.set_draw_color(self._ren, r_, g_, b_, a)
        return 0

    def clear(self, c: int = 0) -> int:
        """Clear the entire canvas to color c (default: black)."""
        _gui_sdl.set_draw_color(self._ren, r(c), g(c), b(c), 255)
        _gui_sdl.clear(self._ren)
        return 0

    def present(self) -> int:
        """Flip the back-buffer to the screen."""
        _gui_sdl.present(self._ren)
        return 0

    def pixel(self, x: int, y: int) -> int:
        """Draw a single pixel at (x, y) in the current draw color.

        One SDL draw call per pixel — fine for a few points, far too slow
        for anything touching every pixel on screen. For bulk pixel work
        (raycasting, software 3D, per-pixel effects) use a PixelBuffer and
        blit_pixels() instead.
        """
        _gui_sdl.draw_point(self._ren, x, y)
        return 0

    def line(self, x1: int, y1: int, x2: int, y2: int) -> int:
        """Draw a line from (x1, y1) to (x2, y2)."""
        _gui_sdl.draw_line(self._ren, x1, y1, x2, y2)
        return 0

    def rect(self, x: int, y: int, w: int, h: int) -> int:
        """Draw the outline of a rectangle."""
        _gui_sdl.draw_rect(self._ren, x, y, w, h)
        return 0

    def fill(self, x: int, y: int, w: int, h: int) -> int:
        """Draw a filled rectangle."""
        _gui_sdl.fill_rect(self._ren, x, y, w, h)
        return 0

    def circle(self, cx: int, cy: int, r: int) -> int:
        """Draw the outline of a circle (Bresenham midpoint algorithm)."""
        x: int = 0
        y: int = r
        d: int = 3 - 2 * r
        while x <= y:
            _gui_sdl.draw_point(self._ren, cx + x, cy + y)
            _gui_sdl.draw_point(self._ren, cx - x, cy + y)
            _gui_sdl.draw_point(self._ren, cx + x, cy - y)
            _gui_sdl.draw_point(self._ren, cx - x, cy - y)
            _gui_sdl.draw_point(self._ren, cx + y, cy + x)
            _gui_sdl.draw_point(self._ren, cx - y, cy + x)
            _gui_sdl.draw_point(self._ren, cx + y, cy - x)
            _gui_sdl.draw_point(self._ren, cx - y, cy - x)
            if d < 0:
                d = d + 4 * x + 6
            else:
                d = d + 4 * (x - y) + 10
                y = y - 1
            x = x + 1
        return 0

    def disc(self, cx: int, cy: int, r: int) -> int:
        """Draw a filled circle using horizontal spans."""
        dy: int = -r
        while dy <= r:
            dx: int = _isqrt(r * r - dy * dy)
            _gui_sdl.fill_rect(self._ren, cx - dx, cy + dy, 2 * dx + 1, 1)
            dy = dy + 1
        return 0

    def triangle(self, x1: int, y1: int, x2: int, y2: int, x3: int, y3: int) -> int:
        """Draw the outline of a triangle."""
        _gui_sdl.draw_line(self._ren, x1, y1, x2, y2)
        _gui_sdl.draw_line(self._ren, x2, y2, x3, y3)
        _gui_sdl.draw_line(self._ren, x3, y3, x1, y1)
        return 0

    def ftriangle(self, x1: int, y1: int, x2: int, y2: int, x3: int, y3: int) -> int:
        """Draw a filled triangle via scanline rasterization."""
        ax: int = x1
        ay: int = y1
        bx: int = x2
        by: int = y2
        cx: int = x3
        cy: int = y3
        tmp: int = 0
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
                _gui_sdl.fill_rect(self._ren, xl, dy, xr - xl + 1, 1)
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
                _gui_sdl.fill_rect(self._ren, xl, dy, xr - xl + 1, 1)
                dy = dy + 1
        return 0

    def char(self, x: int, y: int, ch: str, scale: int = 1) -> int:
        """Draw one character from the built-in 8x8 bitmap font, using the current draw color."""
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
                        _gui_sdl.draw_point(self._ren, x + col, y + row)
                    else:
                        _gui_sdl.fill_rect(self._ren, x + col * scale, y + row * scale, scale, scale)
                col = col + 1
            row = row + 1
        return 0

    def text(self, x: int, y: int, s: str, scale: int = 1) -> int:
        """Draw a string using the built-in 8x8 bitmap font, using the current draw color."""
        i: int = 0
        cx: int = x
        cw: int = 8 * scale
        while i < len(s):
            ch: str = s[i]
            if ch == "\n":
                cx = x
                y = y + cw
            else:
                self.char(cx, y, ch, scale)
                cx = cx + cw
            i = i + 1
        return 0

    def image(self, path: str) -> Image:
        """Load a BMP file and return an Image (SDL2 texture).

        The returned Image must be freed with img.free() when no longer needed.
        """
        surf: int = _gui_sdl.load_bmp(path)
        tex: int = _gui_sdl.create_texture(self._ren, surf)
        _gui_sdl.free_surface(surf)
        img: Image = Image()
        img._setup(tex, _gui_sdl.query_texture_w(tex), _gui_sdl.query_texture_h(tex))
        return img

    def blit(self, img: Image, x: int, y: int) -> int:
        """Draw an Image at (x, y) at its natural size."""
        _gui_sdl.render_copy(self._ren, img._tex, x, y, img._w, img._h)
        return 0

    def blit_scaled(self, img: Image, x: int, y: int, w: int, h: int) -> int:
        """Draw an Image scaled to fit (w, h) at (x, y)."""
        _gui_sdl.render_copy(self._ren, img._tex, x, y, w, h)
        return 0

    def blit_region(self, img: Image, sx: int, sy: int, sw: int, sh: int, dx: int, dy: int) -> int:
        """Draw the (sx, sy, sw, sh) sub-rect of an Image's texture at (dx, dy),
        at its natural (unscaled) size. Useful for sprite sheets.
        """
        _gui_sdl.render_copy_region(self._ren, img._tex, sx, sy, sw, sh, dx, dy)
        return 0

    def blit_ex(self, img: Image, x: int, y: int, w: int, h: int, angle: int, flip: int) -> int:
        """Draw an Image scaled to (w, h) at (x, y), rotated `angle` degrees
        clockwise around its center, optionally flipped (gui.FLIP_NONE,
        FLIP_HORIZONTAL, or FLIP_VERTICAL).
        """
        _gui_sdl.render_copy_ex(self._ren, img._tex, x, y, w, h, angle, flip)
        return 0

    def blit_pixels(self, buf: PixelBuffer, x: int, y: int) -> int:
        """Upload an entire PixelBuffer to the screen at (x, y) in one call.

        Far faster than calling pixel() per pixel for bulk pixel work
        (raycasting, software 3D, per-pixel effects): write into `buf` with
        buf.set()/buf.fill_rect()/raw_addr(), then blit it here once per
        frame.
        """
        buf._bind(self._ren)
        _gui_sdl.update_texture(buf._tex, buf.raw_addr(), buf.pitch())
        _gui_sdl.render_copy(self._ren, buf._tex, x, y, buf.width(), buf.height())
        return 0

    def draw_ttf(self, font: Font, x: int, y: int, text: str, color: int) -> int:
        """Draw anti-aliased TrueType text at (x, y) using a Font and a packed
        0xRRGGBB color (see gui.r()/g()/b() or the named color constants).
        """
        surf: int = _gui_ttf.render_blended(font._font, text, r(color), g(color), b(color))
        tex: int = _gui_sdl.create_texture(self._ren, surf)
        _gui_sdl.free_surface(surf)
        w: int = _gui_ttf.size_text_w(font._font, text)
        h: int = _gui_ttf.size_text_h(font._font, text)
        _gui_sdl.render_copy(self._ren, tex, x, y, w, h)
        _gui_sdl.destroy_texture(tex)
        return 0

    def poll(self) -> int:
        """Return the next event type (0 = no event)."""
        return _gui_sdl.poll_event()

    def update(self) -> int:
        """Present current frame, drain events, return 1 while window is open.

        Typical use::

            while canvas.update():
                canvas.clear()
                # draw here
        """
        _gui_sdl.present(self._ren)
        ev: int = _gui_sdl.poll_event()
        while ev != 0:
            if ev == EVENT_QUIT:
                self._running = 0
                return 0
            if ev == EVENT_KEYDOWN:
                self._last_key = _gui_sdl.key_scancode()
            if ev == EVENT_MOUSEMOTION:
                self._mx = _gui_sdl.mouse_x()
                self._my = _gui_sdl.mouse_y()
            if ev == EVENT_MOUSEBUTTONDOWN:
                self._mbtn = _gui_sdl.mouse_button()
            ev = _gui_sdl.poll_event()
        return self._running

    def key(self) -> int:
        """Return the scancode of the last key pressed (KEY_* constant)."""
        return self._last_key

    def mx(self) -> int:
        """Return the last known mouse X position."""
        return self._mx

    def my(self) -> int:
        """Return the last known mouse Y position."""
        return self._my

    def btn(self) -> int:
        """Return the last mouse button pressed (BUTTON_* constant)."""
        return self._mbtn

    def key_down(self, scancode: int) -> int:
        """Return non-zero if the given KEY_* scancode is currently held down."""
        return _gui_sdl.is_key_down(scancode)

    def mouse_dx(self) -> int:
        """Return relative mouse motion (x) since the last call to mouse_dx/mouse_dy."""
        return _gui_sdl.mouse_dx()

    def mouse_dy(self) -> int:
        """Return relative mouse motion (y) since the last call to mouse_dx/mouse_dy."""
        return _gui_sdl.mouse_dy()

    def relative_mouse(self, enabled: int) -> int:
        """Enable (1) or disable (0) relative mouse mode (hides cursor, captures input)."""
        return _gui_sdl.set_relative_mouse(enabled)

    def show_cursor(self, visible: int) -> int:
        """Show (1) or hide (0) the mouse cursor."""
        return _gui_sdl.show_cursor(visible)

    def fullscreen(self, enabled: int) -> int:
        """Toggle fullscreen. Pass FULLSCREEN_DESKTOP-style truthy/falsy enabled flag."""
        flags: int = 0
        if enabled:
            flags = WINDOW_FULLSCREEN_DESKTOP
        return _gui_sdl.set_fullscreen(self._win, flags)

    def resize(self, w: int, h: int) -> int:
        """Resize the window at runtime."""
        _gui_sdl.set_window_size(self._win, w, h)
        self._w = w
        self._h = h
        return 0

    def set_clipboard(self, text: str) -> int:
        """Set the system clipboard text."""
        return _gui_sdl.set_clipboard_text(text)

    def get_clipboard(self) -> str:
        """Get the system clipboard text."""
        return _gui_sdl.get_clipboard_text()

    def delay(self, ms: int) -> int:
        """Sleep for ms milliseconds."""
        _gui_sdl.delay(ms)
        return 0

    def ticks(self) -> int:
        """Return milliseconds elapsed since SDL was initialised."""
        return _gui_sdl.get_ticks()

    def title(self, t: str) -> int:
        """Change the window title."""
        _gui_sdl.set_window_title(self._win, t)
        return 0

    def width(self) -> int:
        return self._w

    def height(self) -> int:
        return self._h

    def close(self) -> int:
        """Destroy the renderer, window, and quit SDL."""
        _gui_sdl.destroy_renderer(self._ren)
        _gui_sdl.destroy_window(self._win)
        _gui_sdl.quit()
        return 0
