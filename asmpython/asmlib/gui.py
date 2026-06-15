"""asmlib.gui — SDL2 window, renderer, and event bindings (hosted targets).

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
  - Colors are packed as (r, g, b, a) via gui.color(r,g,b,a)  → int
  - Event types are gui.EVENT_* integer constants
"""
from __future__ import annotations

from ..stdlib import Func, Const

BINDINGS: dict = {
    # ---- Init / quit --------------------------------------------------------
    # init(flags: int) -> 0 on success  (SDL_Init)
    "init":             Func(arg_types=("int",),                    ret_type="int", c_name="SDL_Init"),
    # quit()  (SDL_Quit)
    "quit":             Func(arg_types=(),                          ret_type="int", c_name="SDL_Quit"),

    # ---- Window -------------------------------------------------------------
    # create_window(title, x, y, w, h, flags) -> handle int
    "create_window":    Func(arg_types=("str","int","int","int","int","int"), ret_type="int", c_name="SDL_CreateWindow"),
    # destroy_window(handle)
    "destroy_window":   Func(arg_types=("int",),                   ret_type="int", c_name="SDL_DestroyWindow"),
    # set_window_title(handle, title)
    "set_window_title": Func(arg_types=("int", "str"),              ret_type="int", c_name="SDL_SetWindowTitle"),
    # set_window_icon(window, surface) — give the window's titlebar / taskbar
    # entry a custom icon (SDL_SetWindowIcon). `surface` is a handle returned
    # by load_bmp().
    "set_window_icon":  Func(arg_types=("int","int"),               ret_type="int", c_name="SDL_SetWindowIcon"),

    # ---- Surfaces (for window icons etc.) ------------------------------------
    # load_bmp(path) -> surface handle int, or 0 on failure. SDL2 has no
    # built-in loader for arbitrary image formats without SDL_image, but BMP
    # loading is always available — convert your icon to .bmp for set_window_icon.
    "load_bmp":         Func(arg_types=("str",),                    ret_type="int", c_name="_gui_load_bmp"),
    # free_surface(surface) — release a surface returned by load_bmp()
    "free_surface":     Func(arg_types=("int",),                    ret_type="int", c_name="SDL_FreeSurface"),

    # ---- Renderer -----------------------------------------------------------
    # create_renderer(window, index, flags) -> renderer handle
    "create_renderer":  Func(arg_types=("int","int","int"),         ret_type="int", c_name="SDL_CreateRenderer"),
    # destroy_renderer(renderer)
    "destroy_renderer": Func(arg_types=("int",),                   ret_type="int", c_name="SDL_DestroyRenderer"),
    # set_draw_color(renderer, r, g, b, a) -> 0 on success
    "set_draw_color":   Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="SDL_SetRenderDrawColor"),
    # clear(renderer)
    "clear":            Func(arg_types=("int",),                   ret_type="int", c_name="SDL_RenderClear"),
    # present(renderer)  — swap the back buffer
    "present":          Func(arg_types=("int",),                   ret_type="int", c_name="SDL_RenderPresent"),
    # draw_point(renderer, x, y)
    "draw_point":       Func(arg_types=("int","int","int"),         ret_type="int", c_name="SDL_RenderDrawPoint"),
    # draw_line(renderer, x1, y1, x2, y2)
    "draw_line":        Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="SDL_RenderDrawLine"),
    # fill_rect(renderer, x, y, w, h) — fills a solid rectangle
    "fill_rect":        Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="_gui_fill_rect"),
    # draw_rect(renderer, x, y, w, h) — draws a rectangle outline
    "draw_rect":        Func(arg_types=("int","int","int","int","int"), ret_type="int", c_name="_gui_draw_rect"),

    # ---- Events -------------------------------------------------------------
    # poll_event() -> event_type int  (0 = no event; SDL_EVENT_* on event)
    "poll_event":       Func(arg_types=(),                         ret_type="int", c_name="_gui_poll_event"),
    # wait_event() -> event_type int  (blocks until an event arrives)
    "wait_event":       Func(arg_types=(),                         ret_type="int", c_name="_gui_wait_event"),
    # key_scancode() -> int  — scancode of the last key event
    "key_scancode":     Func(arg_types=(),                         ret_type="int", c_name="_gui_key_scancode"),
    # mouse_x() -> int, mouse_y() -> int — position from last motion event
    "mouse_x":          Func(arg_types=(),                         ret_type="int", c_name="_gui_mouse_x"),
    "mouse_y":          Func(arg_types=(),                         ret_type="int", c_name="_gui_mouse_y"),
    # mouse_button() -> int — button mask from last button event
    "mouse_button":     Func(arg_types=(),                         ret_type="int", c_name="_gui_mouse_button"),

    # ---- Timing -------------------------------------------------------------
    # delay(ms: int) — sleep for `ms` milliseconds
    "delay":            Func(arg_types=("int",),                   ret_type="int", c_name="SDL_Delay"),
    # get_ticks() -> int — milliseconds since SDL_Init
    "get_ticks":        Func(arg_types=(),                         ret_type="int", c_name="SDL_GetTicks"),

    # ---- Constants ----------------------------------------------------------
    # SDL_Init flags
    "INIT_VIDEO":       Const(ty="int", value=0x00000020),
    "INIT_AUDIO":       Const(ty="int", value=0x00000010),
    "INIT_EVENTS":      Const(ty="int", value=0x00004000),
    "INIT_EVERYTHING":  Const(ty="int", value=0x0000FFFF),

    # SDL_CreateWindow flags
    "WINDOW_SHOWN":     Const(ty="int", value=0x00000004),
    "WINDOW_RESIZABLE": Const(ty="int", value=0x00000020),
    "WINDOW_FULLSCREEN":Const(ty="int", value=0x00000001),
    "WINDOW_CENTERED":  Const(ty="int", value=0x2FFF0000),

    # SDL_CreateRenderer flags
    "RENDERER_ACCELERATED": Const(ty="int", value=0x00000002),
    "RENDERER_PRESENTVSYNC":Const(ty="int", value=0x00000004),
    "RENDERER_SOFTWARE":    Const(ty="int", value=0x00000001),

    # Event types (SDL_EventType subset)
    "EVENT_QUIT":       Const(ty="int", value=0x100),
    "EVENT_KEYDOWN":    Const(ty="int", value=0x300),
    "EVENT_KEYUP":      Const(ty="int", value=0x301),
    "EVENT_MOUSEMOTION":Const(ty="int", value=0x400),
    "EVENT_MOUSEBUTTONDOWN": Const(ty="int", value=0x401),
    "EVENT_MOUSEBUTTONUP":   Const(ty="int", value=0x402),

    # Keyboard scancodes (SDL_Scancode, most common)
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

    # Mouse buttons
    "BUTTON_LEFT":      Const(ty="int", value=1),
    "BUTTON_MIDDLE":    Const(ty="int", value=2),
    "BUTTON_RIGHT":     Const(ty="int", value=3),
}
