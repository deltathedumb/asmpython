"""random module: pseudo-random numbers via the C stdlib LCG (rand/srand)."""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # random.seed(n: int)  — seeds the generator
    "seed":     Func(arg_types=("int",), ret_type="int", c_name="srand"),
    # random.rand() -> int  — raw rand() value in [0, RAND_MAX]
    "rand":     Func(arg_types=(),       ret_type="int", c_name="rand"),
    # RAND_MAX: 32767 on Windows CRT, 2147483647 on glibc; use the small value
    # so code is portable between the two.
    "RAND_MAX": Const(ty="int", value=32767),
}
