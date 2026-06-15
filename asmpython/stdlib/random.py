"""random module: pseudo-random numbers via the C stdlib LCG (rand/srand).

Higher-level functions (_random_random, _random_randint, _random_uniform)
are implemented as inline NASM helpers in the target subclasses so they work
without requiring a source-level module that could call list operations.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # random.seed(n: int)  — seeds the generator (srand)
    "seed":     Func(arg_types=("int",), ret_type="int", c_name="srand"),
    # random.rand() -> int  — raw rand() value in [0, RAND_MAX]
    "rand":     Func(arg_types=(),       ret_type="int", c_name="rand"),
    # RAND_MAX: 32767 on Windows CRT, 2147483647 on glibc; use the small value
    # so code is portable between the two.
    "RAND_MAX": Const(ty="int", value=32767),

    # Higher-level convenience functions (implemented as inline NASM helpers).
    # random.random() -> float in [0.0, 1.0)
    "random":   Func(arg_types=(),               ret_type="float", c_name="_random_random"),
    # random.randint(a, b) -> int in [a, b] inclusive (like CPython)
    "randint":  Func(arg_types=("int", "int"),   ret_type="int",   c_name="_random_randint"),
    # random.uniform(a, b) -> float in [a, b]
    "uniform":  Func(arg_types=("float", "float"), ret_type="float", c_name="_random_uniform"),
    # random.randrange(stop) -> int in [0, stop)
    "randrange": Func(arg_types=("int",),         ret_type="int",   c_name="_random_randrange"),
    # random.choice(seq) -> element: pick a random element from seq (list)
    "choice":   Func(arg_types=("list",),         ret_type="any",   c_name="_random_choice"),
    # random.shuffle(seq) -> None: shuffle seq in-place (Fisher-Yates)
    "shuffle":  Func(arg_types=("list",),         ret_type="int",   c_name="_random_shuffle"),
    # random.sample(population, k) -> list: k unique random elements
    "sample":   Func(arg_types=("list", "int"),   ret_type="list",  c_name="_random_sample"),
    # random.getrandbits(k) -> int: k random bits (1-64)
    "getrandbits": Func(arg_types=("int",),        ret_type="int",   c_name="_random_getrandbits"),
}
