"""_gui_sdl — internal SDL2 FFI bindings used by the gui source module.

Do not import this directly; use ``import gui`` instead.  This module holds
the low-level Func/Const BINDINGS dict that the compiler resolves to SDL2
C function calls.  It is not in _BUNDLED_SOURCE_STDLIB and is treated as
a pure FFI module by the backend.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # ---- Init / quit --------------------------------------------------------
    "init":             Func(arg_types=("int",),                    ret_type="int", c_name="SDL_Init"),
    "quit":             Func(arg_types=(),                          ret_type="int", c_name="SDL_Quit"),

    # ---- Window -------------------------------------------------------------
    "create_window":    Func(arg_types=("str","int","int","int","int","int"), ret_type="int", c_name="SDL_CreateWindow"),
    "destroy_window":   Func(arg_types=("int",),                   ret_type="int", c_name="SDL_DestroyWindow"),
    "set_window_title": Func(arg_types=("int", "str"),              ret_type="int", c_name="SDL_SetWindowTitle"),
    "set_window_icon":  Func(arg_types=("int","int"),               ret_type="int", c_name="SDL_SetWindowIcon"),

    # ---- Surfaces / textures ------------------------------------------------
    "load_bmp":         Func(arg_types=("str",),                    ret_type="int", c_name="_gui_load_bmp"),
    "free_surface":     Func(arg_types=("int",),                    ret_type="int", c_name="SDL_FreeSurface"),
    "create_texture":   Func(arg_types=("int","int"),               ret_type="int", c_name="SDL_CreateTextureFromSurface"),
    "destroy_texture":  Func(arg_types=("int",),                    ret_type="int", c_name="SDL_DestroyTexture"),
    "query_texture_w":  Func(arg_types=("int",),                    ret_type="int", c_name="_gui_query_texture_w"),
    "query_texture_h":  Func(arg_types=("int",),                    ret_type="int", c_name="_gui_query_texture_h"),
    "render_copy":      Func(arg_types=("int","int","int","int","int","int"), ret_type="int", c_name="_gui_render_copy"),
    "set_texture_blend":Func(arg_types=("int","int"),               ret_type="int", c_name="SDL_SetTextureBlendMode"),
    "set_texture_alpha":Func(arg_types=("int","int"),               ret_type="int", c_name="SDL_SetTextureAlphaMod"),

    # ---- Renderer -----------------------------------------------------------
    "create_renderer":  Func(arg_types=("int","int","int"),         ret_type="int", c_name="SDL_CreateRenderer"),
    "destroy_renderer": Func(arg_types=("int",),                   ret_type="int", c_name="SDL_DestroyRenderer"),
    "set_draw_color":   Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="SDL_SetRenderDrawColor"),
    "set_draw_blend":   Func(arg_types=("int","int"),               ret_type="int", c_name="SDL_SetRenderDrawBlendMode"),
    "clear":            Func(arg_types=("int",),                   ret_type="int", c_name="SDL_RenderClear"),
    "present":          Func(arg_types=("int",),                   ret_type="int", c_name="SDL_RenderPresent"),
    "draw_point":       Func(arg_types=("int","int","int"),         ret_type="int", c_name="SDL_RenderDrawPoint"),
    "draw_line":        Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="SDL_RenderDrawLine"),
    "fill_rect":        Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="_gui_fill_rect"),
    "draw_rect":        Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="_gui_draw_rect"),

    # ---- Events -------------------------------------------------------------
    "poll_event":       Func(arg_types=(),                         ret_type="int", c_name="_gui_poll_event"),
    "wait_event":       Func(arg_types=(),                         ret_type="int", c_name="_gui_wait_event"),
    "key_scancode":     Func(arg_types=(),                         ret_type="int", c_name="_gui_key_scancode"),
    "mouse_x":          Func(arg_types=(),                         ret_type="int", c_name="_gui_mouse_x"),
    "mouse_y":          Func(arg_types=(),                         ret_type="int", c_name="_gui_mouse_y"),
    "mouse_button":     Func(arg_types=(),                         ret_type="int", c_name="_gui_mouse_button"),

    # ---- Timing -------------------------------------------------------------
    "delay":            Func(arg_types=("int",),                   ret_type="int", c_name="SDL_Delay"),
    "get_ticks":        Func(arg_types=(),                         ret_type="int", c_name="SDL_GetTicks"),

    # ---- Init flags ---------------------------------------------------------
    "INIT_VIDEO":           Const(ty="int", value=0x00000020),
    "INIT_AUDIO":           Const(ty="int", value=0x00000010),
    "INIT_EVENTS":          Const(ty="int", value=0x00004000),
    "INIT_EVERYTHING":      Const(ty="int", value=0x0000FFFF),

    # ---- Window flags -------------------------------------------------------
    "WINDOW_SHOWN":         Const(ty="int", value=0x00000004),
    "WINDOW_RESIZABLE":     Const(ty="int", value=0x00000020),
    "WINDOW_FULLSCREEN":    Const(ty="int", value=0x00000001),
    "WINDOW_CENTERED":      Const(ty="int", value=0x2FFF0000),

    # ---- Renderer flags -----------------------------------------------------
    "RENDERER_ACCELERATED": Const(ty="int", value=0x00000002),
    "RENDERER_PRESENTVSYNC":Const(ty="int", value=0x00000004),
    "RENDERER_SOFTWARE":    Const(ty="int", value=0x00000001),

    # ---- Blend modes --------------------------------------------------------
    "BLEND_NONE":           Const(ty="int", value=0),
    "BLEND_ALPHA":          Const(ty="int", value=1),
    "BLEND_ADD":            Const(ty="int", value=2),
    "BLEND_MOD":            Const(ty="int", value=4),

    # ---- Event types --------------------------------------------------------
    "EVENT_QUIT":           Const(ty="int", value=0x100),
    "EVENT_KEYDOWN":        Const(ty="int", value=0x300),
    "EVENT_KEYUP":          Const(ty="int", value=0x301),
    "EVENT_MOUSEMOTION":    Const(ty="int", value=0x400),
    "EVENT_MOUSEBUTTONDOWN":Const(ty="int", value=0x401),
    "EVENT_MOUSEBUTTONUP":  Const(ty="int", value=0x402),
    "EVENT_MOUSEWHEEL":     Const(ty="int", value=0x403),

    # ---- Keyboard scancodes (SDL_Scancode) ----------------------------------
    # Letters
    "KEY_A":            Const(ty="int", value=4),
    "KEY_B":            Const(ty="int", value=5),
    "KEY_C":            Const(ty="int", value=6),
    "KEY_D":            Const(ty="int", value=7),
    "KEY_E":            Const(ty="int", value=8),
    "KEY_F":            Const(ty="int", value=9),
    "KEY_G":            Const(ty="int", value=10),
    "KEY_H":            Const(ty="int", value=11),
    "KEY_I":            Const(ty="int", value=12),
    "KEY_J":            Const(ty="int", value=13),
    "KEY_K":            Const(ty="int", value=14),
    "KEY_L":            Const(ty="int", value=15),
    "KEY_M":            Const(ty="int", value=16),
    "KEY_N":            Const(ty="int", value=17),
    "KEY_O":            Const(ty="int", value=18),
    "KEY_P":            Const(ty="int", value=19),
    "KEY_Q":            Const(ty="int", value=20),
    "KEY_R":            Const(ty="int", value=21),
    "KEY_S":            Const(ty="int", value=22),
    "KEY_T":            Const(ty="int", value=23),
    "KEY_U":            Const(ty="int", value=24),
    "KEY_V":            Const(ty="int", value=25),
    "KEY_W":            Const(ty="int", value=26),
    "KEY_X":            Const(ty="int", value=27),
    "KEY_Y":            Const(ty="int", value=28),
    "KEY_Z":            Const(ty="int", value=29),
    # Digit row (top row, not numpad)
    "KEY_1":            Const(ty="int", value=30),
    "KEY_2":            Const(ty="int", value=31),
    "KEY_3":            Const(ty="int", value=32),
    "KEY_4":            Const(ty="int", value=33),
    "KEY_5":            Const(ty="int", value=34),
    "KEY_6":            Const(ty="int", value=35),
    "KEY_7":            Const(ty="int", value=36),
    "KEY_8":            Const(ty="int", value=37),
    "KEY_9":            Const(ty="int", value=38),
    "KEY_0":            Const(ty="int", value=39),
    # Editing / navigation
    "KEY_RETURN":       Const(ty="int", value=40),
    "KEY_ESCAPE":       Const(ty="int", value=41),
    "KEY_BACKSPACE":    Const(ty="int", value=42),
    "KEY_TAB":          Const(ty="int", value=43),
    "KEY_SPACE":        Const(ty="int", value=44),
    "KEY_DELETE":       Const(ty="int", value=76),
    "KEY_INSERT":       Const(ty="int", value=73),
    "KEY_HOME":         Const(ty="int", value=74),
    "KEY_END":          Const(ty="int", value=77),
    "KEY_PAGEUP":       Const(ty="int", value=75),
    "KEY_PAGEDOWN":     Const(ty="int", value=78),
    # Arrow keys
    "KEY_RIGHT":        Const(ty="int", value=79),
    "KEY_LEFT":         Const(ty="int", value=80),
    "KEY_DOWN":         Const(ty="int", value=81),
    "KEY_UP":           Const(ty="int", value=82),
    # Function keys
    "KEY_F1":           Const(ty="int", value=58),
    "KEY_F2":           Const(ty="int", value=59),
    "KEY_F3":           Const(ty="int", value=60),
    "KEY_F4":           Const(ty="int", value=61),
    "KEY_F5":           Const(ty="int", value=62),
    "KEY_F6":           Const(ty="int", value=63),
    "KEY_F7":           Const(ty="int", value=64),
    "KEY_F8":           Const(ty="int", value=65),
    "KEY_F9":           Const(ty="int", value=66),
    "KEY_F10":          Const(ty="int", value=67),
    "KEY_F11":          Const(ty="int", value=68),
    "KEY_F12":          Const(ty="int", value=69),
    # Modifier keys
    "KEY_LCTRL":        Const(ty="int", value=224),
    "KEY_LSHIFT":       Const(ty="int", value=225),
    "KEY_LALT":         Const(ty="int", value=226),
    "KEY_RCTRL":        Const(ty="int", value=228),
    "KEY_RSHIFT":       Const(ty="int", value=229),
    "KEY_RALT":         Const(ty="int", value=230),

    # ---- Mouse buttons ------------------------------------------------------
    "BUTTON_LEFT":      Const(ty="int", value=1),
    "BUTTON_MIDDLE":    Const(ty="int", value=2),
    "BUTTON_RIGHT":     Const(ty="int", value=3),
}
