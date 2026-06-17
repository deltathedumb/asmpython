"""_audio_sdl — SDL2_mixer FFI bindings.

Do not import this directly; use ``import audio`` instead.  The low-level
``_audio_load_wav`` symbol is an inline helper emitted by the backend;
all other ``Mix_*`` symbols are linked directly from SDL2_mixer.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # ---- Device lifecycle ---------------------------------------------------
    "open":      Func(arg_types=("int", "int", "int", "int"), ret_type="int",
                      c_name="Mix_OpenAudio"),
    "close":     Func(arg_types=(),                            ret_type="int",
                      c_name="Mix_CloseAudio"),

    # ---- Chunk (WAV) --------------------------------------------------------
    # _audio_load_wav is an inline helper (wraps Mix_LoadWAV_RW + SDL_RWFromFile).
    "load_wav":  Func(arg_types=("str",),                      ret_type="int",
                      c_name="_audio_load_wav"),
    "free":      Func(arg_types=("int",),                      ret_type="int",
                      c_name="Mix_FreeChunk"),
    "play":      Func(arg_types=("int", "int", "int"),         ret_type="int",
                      c_name="Mix_PlayChannel"),
    "halt":      Func(arg_types=("int",),                      ret_type="int",
                      c_name="Mix_HaltChannel"),
    "volume":    Func(arg_types=("int", "int"),                ret_type="int",
                      c_name="Mix_Volume"),
    "playing":   Func(arg_types=("int",),                      ret_type="int",
                      c_name="Mix_Playing"),

    # ---- Music (OGG, MP3, MOD, etc.) ----------------------------------------
    "load_mus":  Func(arg_types=("str",),                      ret_type="int",
                      c_name="Mix_LoadMUS"),
    "free_mus":  Func(arg_types=("int",),                      ret_type="int",
                      c_name="Mix_FreeMusic"),
    "play_mus":  Func(arg_types=("int", "int"),                ret_type="int",
                      c_name="Mix_PlayMusic"),
    "halt_mus":  Func(arg_types=(),                            ret_type="int",
                      c_name="Mix_HaltMusic"),
    "vol_mus":   Func(arg_types=("int",),                      ret_type="int",
                      c_name="Mix_VolumeMusic"),
    "playing_mus": Func(arg_types=(),                          ret_type="int",
                      c_name="Mix_PlayingMusic"),

    # ---- Constants ----------------------------------------------------------
    "MIX_DEFAULT_FORMAT": Const(ty="int", value=0x8010),  # AUDIO_S16SYS
    "MIX_DEFAULT_FREQ":   Const(ty="int", value=44100),
    "MIX_DEFAULT_CHANNELS": Const(ty="int", value=2),
    "MIX_MAX_VOLUME":     Const(ty="int", value=128),
}
