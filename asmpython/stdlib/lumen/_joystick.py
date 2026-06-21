"""gui._joystick — SDL2 game controller / joystick input.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

import _gui_sdl

from ._constants import INIT_JOYSTICK


class Joystick:
    """Physical joystick/gamepad opened by device index.

    Call gui.num_joysticks() first to see how many are connected. Axis
    values range roughly -32768..32767 (raw SDL_JoystickGetAxis output).
    Close with .close() when done.
    """

    def __init__(self, index: int) -> int:
        _gui_sdl.init_subsystem(INIT_JOYSTICK)
        self._joy: int = _gui_sdl.joystick_open(index)
        return 0

    def name(self) -> str:
        return _gui_sdl.joystick_name(self._joy)

    def num_axes(self) -> int:
        return _gui_sdl.joystick_num_axes(self._joy)

    def num_buttons(self) -> int:
        return _gui_sdl.joystick_num_buttons(self._joy)

    def axis(self, index: int) -> int:
        """Raw axis value, roughly -32768..32767."""
        return _gui_sdl.joystick_axis(self._joy, index)

    def button(self, index: int) -> int:
        """Return non-zero if the given button index is currently held down."""
        return _gui_sdl.joystick_button(self._joy, index)

    def close(self) -> int:
        _gui_sdl.joystick_close(self._joy)
        self._joy = 0
        return 0


def num_joysticks() -> int:
    """Return the number of joysticks/gamepads currently connected."""
    _gui_sdl.init_subsystem(INIT_JOYSTICK)
    return _gui_sdl.num_joysticks()


def joystick_update() -> int:
    """Poll the OS for joystick state changes (call once per frame if not
    using Canvas.update(), which already does this via the event queue).
    """
    _gui_sdl.joystick_update()
    return 0
