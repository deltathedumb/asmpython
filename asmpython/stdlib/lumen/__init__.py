"""lumen — asmpython's graphics/audio/input ecosystem.

`import lumen` gets you the 2D graphics package for hosted (SDL2) targets:
window management, hardware-accelerated drawing, a fast CPU-side pixel
buffer for bulk per-pixel work, texture loading, TrueType and bitmap text,
event handling, named colors, and keyboard/mouse constants.

Two more pieces live as separate submodules, since they pull in different
link dependencies and not every program needs all three:

    import lumen.framebuffer   # bare-metal/UEFI direct pixel framebuffer
    import lumen.audio         # SDL2_mixer sound + music playback

SDL2 must be installed for `lumen` / `lumen.audio` (SDL2_mixer too, for the
latter):
  Linux:   sudo apt install libsdl2-dev libsdl2-mixer-dev
  Windows: place SDL2.dll / SDL2_mixer.dll next to the compiled executable

Quick start::

    import lumen
    canvas = lumen.Canvas("Hello", 800, 600)
    while canvas.update():
        canvas.clear(lumen.NAVY)
        canvas.color(lumen.YELLOW)
        canvas.disc(400, 300, 50)
        if canvas.key() == lumen.KEY_ESCAPE:
            break
    canvas.close()

Low-level, per-pixel drawing — software 3D, raycasting, procedural
textures — goes through PixelBuffer instead of one draw call per pixel::

    buf = lumen.PixelBuffer(800, 600)
    while canvas.update():
        # ... write into buf with buf.set(x, y, color) ...
        canvas.blit_pixels(buf, 0, 0)

For bare-metal-equivalent direct memory access, buf.raw_addr() returns the
buffer's real memory address (same idea as lumen.framebuffer.Framebuffer's
hardware.mmio_write32-based API, but for hosted SDL2 windows).
"""
from __future__ import annotations

from ._colors import (
    r, g, b, rgb,
    BLACK, WHITE, RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA, ORANGE, PURPLE,
    GRAY, DARK_GRAY, LIGHT_GRAY, PINK, BROWN, SKY, NAVY, LIME, TEAL, GOLD,
    CRIMSON, INDIGO, VIOLET, TURQUOISE, SALMON, CORAL, OLIVE, MAROON,
)
from ._constants import (
    INIT_VIDEO, INIT_AUDIO, INIT_EVENTS, INIT_JOYSTICK, INIT_EVERYTHING,
    WINDOW_SHOWN, WINDOW_RESIZABLE, WINDOW_FULLSCREEN,
    WINDOW_FULLSCREEN_DESKTOP, WINDOW_CENTERED,
    FONT_STYLE_NORMAL, FONT_STYLE_BOLD, FONT_STYLE_ITALIC, FONT_STYLE_UNDERLINE,
    RENDERER_ACCELERATED, RENDERER_PRESENTVSYNC, RENDERER_SOFTWARE,
    BLEND_NONE, BLEND_ALPHA, BLEND_ADD, BLEND_MOD,
    FLIP_NONE, FLIP_HORIZONTAL, FLIP_VERTICAL,
    EVENT_QUIT, EVENT_KEYDOWN, EVENT_KEYUP, EVENT_MOUSEMOTION,
    EVENT_MOUSEBUTTONDOWN, EVENT_MOUSEBUTTONUP, EVENT_MOUSEWHEEL,
    EVENT_JOYAXISMOTION, EVENT_JOYBUTTONDOWN, EVENT_JOYBUTTONUP,
    KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I, KEY_J,
    KEY_K, KEY_L, KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R, KEY_S, KEY_T,
    KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z,
    KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0,
    KEY_RETURN, KEY_ESCAPE, KEY_BACKSPACE, KEY_TAB, KEY_SPACE,
    KEY_DELETE, KEY_INSERT, KEY_HOME, KEY_END, KEY_PAGEUP, KEY_PAGEDOWN,
    KEY_RIGHT, KEY_LEFT, KEY_DOWN, KEY_UP,
    KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6,
    KEY_F7, KEY_F8, KEY_F9, KEY_F10, KEY_F11, KEY_F12,
    KEY_LCTRL, KEY_LSHIFT, KEY_LALT, KEY_RCTRL, KEY_RSHIFT, KEY_RALT,
    BUTTON_LEFT, BUTTON_MIDDLE, BUTTON_RIGHT,
)
from ._image import Image
from ._font import Font
from ._pixels import PixelBuffer
from ._joystick import Joystick, num_joysticks, joystick_update
from ._canvas import Canvas
from ._tilemap import Tilemap
