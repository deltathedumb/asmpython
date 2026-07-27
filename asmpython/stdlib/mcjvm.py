"""Minecraft host API for the JVM backend.

Declares the functions a compiled mod may call. Each maps to a static Java
method on the backend's runtime class, so a Python mod compiled with
``--backend jvm`` links against the game-side implementation without the
compiler knowing anything about Minecraft.

Strings cross as heap addresses into the runtime's ByteBuffer; the Java side
reads them with ``Runtime.readString``. Entities, levels and stacks are opaque
64-bit handles the host hands out and takes back — exactly the shape PyMod's
existing ``GameHost`` interface already uses, which is why the same Java
implementation can serve both the interpreted and the compiled path.
"""
from __future__ import annotations

from . import Func

BINDINGS: dict = {
    # --- registration ---------------------------------------------------
    # mc_item(name, max_stack, max_damage) -> registered item handle
    "mc_item": Func(
        arg_types=("str", "int", "int"), ret_type="int",
        c_name="mc_item",
    ),
    # mc_block(name, hardness_milli) -> registered block handle
    "mc_block": Func(
        arg_types=("str", "int"), ret_type="int",
        c_name="mc_block",
    ),

    # --- world interaction ----------------------------------------------
    "mc_message": Func(
        arg_types=("int", "str"), ret_type="int",
        c_name="mc_message",
    ),
    "mc_play_sound": Func(
        arg_types=("int", "str"), ret_type="int",
        c_name="mc_play_sound",
    ),
    "mc_knockback": Func(
        arg_types=("int", "int"), ret_type="int",
        c_name="mc_knockback",
    ),
    "mc_give_effect": Func(
        arg_types=("int", "str", "int", "int"), ret_type="int",
        c_name="mc_give_effect",
    ),
    # mc_property(target, name) -> a 64-bit value; strings come back as an
    # address, numbers as themselves.
    "mc_property": Func(
        arg_types=("int", "str"), ret_type="int",
        c_name="mc_property",
    ),
    "mc_log": Func(
        arg_types=("str",), ret_type="int",
        c_name="mc_log",
    ),
}
