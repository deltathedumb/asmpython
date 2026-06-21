"""gui._tilemap — grid of tile indices into a single spritesheet Image.

Internal submodule of the `lumen` package; import `lumen` instead of this
directly.
"""
from __future__ import annotations

from ._image import Image
from ._canvas import Canvas


class Tilemap:
    """2D grid of tiles drawn from a single spritesheet Image.

    The spritesheet is a regular grid of `tile_w` x `tile_h` cells, indexed
    left-to-right, top-to-bottom starting at 0. Build the map with `set()`,
    then call `draw()` once per frame.
    """

    def __init__(self, sheet: Image, tile_w: int, tile_h: int, cols: int, rows: int) -> int:
        self._sheet: Image = sheet
        self._tw: int = tile_w
        self._th: int = tile_h
        self._cols: int = cols
        self._rows: int = rows
        self._sheet_cols: int = sheet.w() // tile_w
        self._cells: list[int] = []
        i: int = 0
        n: int = cols * rows
        while i < n:
            self._cells.append(0)
            i = i + 1
        return 0

    def set(self, col: int, row: int, tile_index: int) -> int:
        """Set the tile at (col, row) to the given spritesheet tile index."""
        self._cells[row * self._cols + col] = tile_index
        return 0

    def get(self, col: int, row: int) -> int:
        return self._cells[row * self._cols + col]

    def draw(self, canvas: Canvas, x: int, y: int) -> int:
        """Draw the whole map with its top-left corner at (x, y)."""
        row: int = 0
        while row < self._rows:
            col: int = 0
            while col < self._cols:
                tile: int = self._cells[row * self._cols + col]
                sx: int = (tile % self._sheet_cols) * self._tw
                sy: int = (tile // self._sheet_cols) * self._th
                dx: int = x + col * self._tw
                dy: int = y + row * self._th
                canvas.blit_region(self._sheet, sx, sy, self._tw, self._th, dx, dy)
                col = col + 1
            row = row + 1
        return 0
