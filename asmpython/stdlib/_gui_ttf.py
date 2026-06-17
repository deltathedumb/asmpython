"""_gui_ttf — internal SDL2_ttf FFI bindings used by the gui source module.

Do not import this directly; use ``import gui`` instead (``gui.Font``).  This
module holds the low-level Func/Const BINDINGS dict that the compiler
resolves to SDL2_ttf C function calls.  It is not in _BUNDLED_SOURCE_STDLIB
and is treated as a pure FFI module by the backend.

SDL_Color (Uint8 r,g,b,a -- 4 bytes) is passed by value in both the Win64 and
SysV ABIs as a single general-purpose register, so render_blended packs r/g/b
(alpha fixed at 255) into one int rather than needing a struct-by-value Func
argument type.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    "init":             Func(arg_types=(),                         ret_type="int", c_name="TTF_Init"),
    "quit":             Func(arg_types=(),                         ret_type="int", c_name="TTF_Quit"),
    "open_font":        Func(arg_types=("str", "int"),              ret_type="int", c_name="TTF_OpenFont"),
    "close_font":       Func(arg_types=("int",),                    ret_type="int", c_name="TTF_CloseFont"),

    # render_blended(font, text, r, g, b) -> SDL_Surface* (anti-aliased text)
    "render_blended":   Func(arg_types=("int", "str", "int", "int", "int"), ret_type="int", c_name="_ttf_render_blended"),

    "size_text_w":      Func(arg_types=("int", "str"),              ret_type="int", c_name="_ttf_size_text_w"),
    "size_text_h":      Func(arg_types=("int", "str"),              ret_type="int", c_name="_ttf_size_text_h"),
    "set_font_style":   Func(arg_types=("int", "int"),               ret_type="int", c_name="TTF_SetFontStyle"),

    "FONT_STYLE_NORMAL":    Const(ty="int", value=0x00),
    "FONT_STYLE_BOLD":      Const(ty="int", value=0x01),
    "FONT_STYLE_ITALIC":    Const(ty="int", value=0x02),
    "FONT_STYLE_UNDERLINE": Const(ty="int", value=0x04),
}
