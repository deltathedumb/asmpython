"""gui._constants — SDL2 flag/event/key/button constants.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

# ---- SDL2 init flags --------------------------------------------------------
INIT_VIDEO:             int = 0x00000020
INIT_AUDIO:             int = 0x00000010
INIT_EVENTS:            int = 0x00004000
INIT_JOYSTICK:          int = 0x00000200
INIT_EVERYTHING:        int = 0x0000FFFF

# ---- Window creation flags ---------------------------------------------------
WINDOW_SHOWN:           int = 0x00000004
WINDOW_RESIZABLE:       int = 0x00000020
WINDOW_FULLSCREEN:      int = 0x00000001
WINDOW_FULLSCREEN_DESKTOP: int = 0x00001001
WINDOW_CENTERED:        int = 0x2FFF0000

# ---- TrueType font style flags (OR-able, see Font.set_style) ----------------
FONT_STYLE_NORMAL:      int = 0x00
FONT_STYLE_BOLD:        int = 0x01
FONT_STYLE_ITALIC:      int = 0x02
FONT_STYLE_UNDERLINE:   int = 0x04

# ---- Renderer creation flags --------------------------------------------------
RENDERER_ACCELERATED:   int = 0x00000002
RENDERER_PRESENTVSYNC:  int = 0x00000004
RENDERER_SOFTWARE:      int = 0x00000001

# ---- Blend modes --------------------------------------------------------------
BLEND_NONE:             int = 0
BLEND_ALPHA:            int = 1
BLEND_ADD:              int = 2
BLEND_MOD:              int = 4

# ---- SDL_RendererFlip (for Canvas.blit_ex) -----------------------------------
FLIP_NONE:              int = 0
FLIP_HORIZONTAL:        int = 1
FLIP_VERTICAL:          int = 2

# ---- Event types ----------------------------------------------------------------
EVENT_QUIT:             int = 0x100
EVENT_KEYDOWN:          int = 0x300
EVENT_KEYUP:            int = 0x301
EVENT_MOUSEMOTION:      int = 0x400
EVENT_MOUSEBUTTONDOWN:  int = 0x401
EVENT_MOUSEBUTTONUP:    int = 0x402
EVENT_MOUSEWHEEL:       int = 0x403
EVENT_JOYAXISMOTION:    int = 0x600
EVENT_JOYBUTTONDOWN:    int = 0x603
EVENT_JOYBUTTONUP:      int = 0x604

# ---- Keyboard scancodes (SDL_Scancode) -----------------------------------------
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

# ---- Mouse buttons --------------------------------------------------------------
BUTTON_LEFT:    int = 1
BUTTON_MIDDLE:  int = 2
BUTTON_RIGHT:   int = 3
