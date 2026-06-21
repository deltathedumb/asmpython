"""gui._colors — named color constants and packed-RGB helpers.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

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


def rgb(r_: int, g_: int, b_: int) -> int:
    """Pack r, g, b (0-255 each) into a 0xRRGGBB color."""
    return (r_ << 16) | (g_ << 8) | b_
