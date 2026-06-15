"""gui — SDL2 window, renderer, and event bindings (hosted targets).

SDL2 provides a cross-platform 2D graphics and event API.  These
bindings expose the most commonly used subset: window management,
hardware-accelerated 2D rendering, basic shapes and textures, and the
event pump (keyboard, mouse, window-close).

To use this module the user must have SDL2 installed:
  Linux:   sudo apt install libsdl2-dev
  Windows: place SDL2.dll next to the executable (or in PATH)
The linker flag -lSDL2 (Linux) or linking against SDL2.lib (Windows)
is added automatically when this module is imported.

Coordinate and color conventions match SDL2:
  - (x=0, y=0) is the top-left corner
  - Colors are packed as (r, g, b, a) via gui.color(r,g,b,a)  -> int
  - Event types are gui.EVENT_* integer constants
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

    # ---- Surfaces -----------------------------------------------------------
    "load_bmp":         Func(arg_types=("str",),                    ret_type="int", c_name="_gui_load_bmp"),
    "free_surface":     Func(arg_types=("int",),                    ret_type="int", c_name="SDL_FreeSurface"),

    # ---- Renderer -----------------------------------------------------------
    "create_renderer":  Func(arg_types=("int","int","int"),         ret_type="int", c_name="SDL_CreateRenderer"),
    "destroy_renderer": Func(arg_types=("int",),                   ret_type="int", c_name="SDL_DestroyRenderer"),
    "set_draw_color":   Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="SDL_SetRenderDrawColor"),
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

    # ---- Constants ----------------------------------------------------------
    "INIT_VIDEO":       Const(ty="int", value=0x00000020),
    "INIT_AUDIO":       Const(ty="int", value=0x00000010),
    "INIT_EVENTS":      Const(ty="int", value=0x00004000),
    "INIT_EVERYTHING":  Const(ty="int", value=0x0000FFFF),

    "WINDOW_SHOWN":     Const(ty="int", value=0x00000004),
    "WINDOW_RESIZABLE": Const(ty="int", value=0x00000020),
    "WINDOW_FULLSCREEN":Const(ty="int", value=0x00000001),
    "WINDOW_CENTERED":  Const(ty="int", value=0x2FFF0000),

    "RENDERER_ACCELERATED": Const(ty="int", value=0x00000002),
    "RENDERER_PRESENTVSYNC":Const(ty="int", value=0x00000004),
    "RENDERER_SOFTWARE":    Const(ty="int", value=0x00000001),

    "EVENT_QUIT":       Const(ty="int", value=0x100),
    "EVENT_KEYDOWN":    Const(ty="int", value=0x300),
    "EVENT_KEYUP":      Const(ty="int", value=0x301),
    "EVENT_MOUSEMOTION":Const(ty="int", value=0x400),
    "EVENT_MOUSEBUTTONDOWN": Const(ty="int", value=0x401),
    "EVENT_MOUSEBUTTONUP":   Const(ty="int", value=0x402),

    "KEY_A":            Const(ty="int", value=4),
    "KEY_D":            Const(ty="int", value=7),
    "KEY_S":            Const(ty="int", value=22),
    "KEY_W":            Const(ty="int", value=26),
    "KEY_ESCAPE":       Const(ty="int", value=41),
    "KEY_SPACE":        Const(ty="int", value=44),
    "KEY_RETURN":       Const(ty="int", value=40),
    "KEY_LEFT":         Const(ty="int", value=80),
    "KEY_RIGHT":        Const(ty="int", value=79),
    "KEY_UP":           Const(ty="int", value=82),
    "KEY_DOWN":         Const(ty="int", value=81),

    "BUTTON_LEFT":      Const(ty="int", value=1),
    "BUTTON_MIDDLE":    Const(ty="int", value=2),
    "BUTTON_RIGHT":     Const(ty="int", value=3),
}
