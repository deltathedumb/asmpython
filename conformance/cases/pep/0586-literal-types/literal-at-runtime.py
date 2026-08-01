# tier: spec
# ref: library/typing.html#typing.Literal
# expect:
# ('a', 'b')
# True
# r
from typing import Literal, get_args, get_origin

L = Literal["a", "b"]
print(get_args(L))
print(get_origin(L) is Literal)

def f(mode: Literal["r", "w"]) -> str:
    return mode

print(f("r"))
