"""gui — complete 2D graphics package for hosted (SDL2) targets.

Part of Lumen, asmpython's graphics/audio/input ecosystem (gui + framebuffer
+ audio). One import gets you everything: window management, hardware-
accelerated drawing, texture loading, TrueType and bitmap text, event
handling, named colors, and keyboard/mouse constants.

SDL2 must be installed:
  Linux:   sudo apt install libsdl2-dev
  Windows: place SDL2.dll next to the compiled executable (or in PATH)

Quick start::

    import gui
    canvas = gui.Canvas("Hello", 800, 600)
    while canvas.update():
        canvas.clear(gui.NAVY)
        canvas.color(gui.YELLOW)
        canvas.disc(400, 300, 50)
        if canvas.key() == gui.KEY_ESCAPE:
            break
    canvas.close()
"""
from __future__ import annotations

import _gui_sdl
import _gui_ttf
import _font8x8


# ---------------------------------------------------------------------------
# Internal integer square root (Newton's method, integer-only)
# ---------------------------------------------------------------------------

def _isqrt(n: int) -> int:
    if n <= 0:
        return 0
    x: int = n
    y: int = (x + 1) // 2
    while y < x:
        x = y
        y = (x + n // x) // 2
    return x


# ---------------------------------------------------------------------------
# Image — SDL2 texture wrapper
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Font — SDL2_ttf TrueType font wrapper
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Canvas — hardware-accelerated 2D surface backed by an SDL2 window
# ---------------------------------------------------------------------------

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
        r: int = (packed >> 16) & 255
        g: int = (packed >> 8) & 255
        b: int = packed & 255
        _gui_sdl.set_draw_color(self._ren, r, g, b, a)
        return 0

    def rgb(self, r: int, g: int, b: int, a: int = 255) -> int:
        """Set draw color from individual r, g, b (and optional alpha) components."""
        _gui_sdl.set_draw_color(self._ren, r, g, b, a)
        return 0

    def clear(self, c: int = 0) -> int:
        """Clear the entire canvas to color c (default: black)."""
        r: int = (c >> 16) & 255
        g: int = (c >> 8) & 255
        b: int = c & 255
        _gui_sdl.set_draw_color(self._ren, r, g, b, 255)
        _gui_sdl.clear(self._ren)
        return 0

    def present(self) -> int:
        """Flip the back-buffer to the screen."""
        _gui_sdl.present(self._ren)
        return 0

    def pixel(self, x: int, y: int) -> int:
        """Draw a single pixel at (x, y) in the current draw color."""
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


# ---------------------------------------------------------------------------
# Named color constants (packed as 0xRRGGBB)
# ---------------------------------------------------------------------------
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
INDIGO:     int = 0x4B0082
VIOLET:     int = 0xEE82EE
TURQUOISE:  int = 0x40E0D0
SALMON:     int = 0xFA8072
CORAL:      int = 0xFF7F50
OLIVE:      int = 0x808000
MAROON:     int = 0x800000


def r(c: int) -> int:
    """Extract the red component of a packed 0xRRGGBB color."""
    return (c >> 16) & 255


def g(c: int) -> int:
    """Extract the green component of a packed 0xRRGGBB color."""
    return (c >> 8) & 255


def b(c: int) -> int:
    """Extract the blue component of a packed 0xRRGGBB color."""
    return c & 255


# ---------------------------------------------------------------------------
# SDL2 init flags
# ---------------------------------------------------------------------------
INIT_VIDEO:             int = 0x00000020
INIT_AUDIO:             int = 0x00000010
INIT_EVENTS:            int = 0x00004000
INIT_EVERYTHING:        int = 0x0000FFFF

# ---------------------------------------------------------------------------
# Window creation flags
# ---------------------------------------------------------------------------
WINDOW_SHOWN:           int = 0x00000004
WINDOW_RESIZABLE:       int = 0x00000020
WINDOW_FULLSCREEN:      int = 0x00000001
WINDOW_FULLSCREEN_DESKTOP: int = 0x00001001
WINDOW_CENTERED:        int = 0x2FFF0000

# ---------------------------------------------------------------------------
# TrueType font style flags (OR-able, see Font.set_style)
# ---------------------------------------------------------------------------
FONT_STYLE_NORMAL:      int = 0x00
FONT_STYLE_BOLD:        int = 0x01
FONT_STYLE_ITALIC:      int = 0x02
FONT_STYLE_UNDERLINE:   int = 0x04

# ---------------------------------------------------------------------------
# Renderer creation flags
# ---------------------------------------------------------------------------
RENDERER_ACCELERATED:   int = 0x00000002
RENDERER_PRESENTVSYNC:  int = 0x00000004
RENDERER_SOFTWARE:      int = 0x00000001

# ---------------------------------------------------------------------------
# Blend modes
# ---------------------------------------------------------------------------
BLEND_NONE:             int = 0
BLEND_ALPHA:            int = 1
BLEND_ADD:              int = 2
BLEND_MOD:              int = 4

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------
EVENT_QUIT:             int = 0x100
EVENT_KEYDOWN:          int = 0x300
EVENT_KEYUP:            int = 0x301
EVENT_MOUSEMOTION:      int = 0x400
EVENT_MOUSEBUTTONDOWN:  int = 0x401
EVENT_MOUSEBUTTONUP:    int = 0x402
EVENT_MOUSEWHEEL:       int = 0x403

# ---------------------------------------------------------------------------
# Keyboard scancodes (SDL_Scancode)
# ---------------------------------------------------------------------------
# Letters
KEY_A:          int = 4
KEY_B:          int = 5
KEY_C:          int = 6
KEY_D:          int = 7
KEY_E:          int = 8
KEY_F:          int = 9
KEY_G:          int = 10
KEY_H:          int = 11
KEY_I:          int = 12
KEY_J:          int = 13
KEY_K:          int = 14
KEY_L:          int = 15
KEY_M:          int = 16
KEY_N:          int = 17
KEY_O:          int = 18
KEY_P:          int = 19
KEY_Q:          int = 20
KEY_R:          int = 21
KEY_S:          int = 22
KEY_T:          int = 23
KEY_U:          int = 24
KEY_V:          int = 25
KEY_W:          int = 26
KEY_X:          int = 27
KEY_Y:          int = 28
KEY_Z:          int = 29
# Digit row (not numpad)
KEY_1:          int = 30
KEY_2:          int = 31
KEY_3:          int = 32
KEY_4:          int = 33
KEY_5:          int = 34
KEY_6:          int = 35
KEY_7:          int = 36
KEY_8:          int = 37
KEY_9:          int = 38
KEY_0:          int = 39
# Editing / navigation
KEY_RETURN:     int = 40
KEY_ESCAPE:     int = 41
KEY_BACKSPACE:  int = 42
KEY_TAB:        int = 43
KEY_SPACE:      int = 44
KEY_DELETE:     int = 76
KEY_INSERT:     int = 73
KEY_HOME:       int = 74
KEY_END:        int = 77
KEY_PAGEUP:     int = 75
KEY_PAGEDOWN:   int = 78
# Arrow keys
KEY_RIGHT:      int = 79
KEY_LEFT:       int = 80
KEY_DOWN:       int = 81
KEY_UP:         int = 82
# Function keys
KEY_F1:         int = 58
KEY_F2:         int = 59
KEY_F3:         int = 60
KEY_F4:         int = 61
KEY_F5:         int = 62
KEY_F6:         int = 63
KEY_F7:         int = 64
KEY_F8:         int = 65
KEY_F9:         int = 66
KEY_F10:        int = 67
KEY_F11:        int = 68
KEY_F12:        int = 69
# Modifier keys
KEY_LCTRL:      int = 224
KEY_LSHIFT:     int = 225
KEY_LALT:       int = 226
KEY_RCTRL:      int = 228
KEY_RSHIFT:     int = 229
KEY_RALT:       int = 230

# ---------------------------------------------------------------------------
# Mouse buttons
# ---------------------------------------------------------------------------
BUTTON_LEFT:    int = 1
BUTTON_MIDDLE:  int = 2
BUTTON_RIGHT:   int = 3
