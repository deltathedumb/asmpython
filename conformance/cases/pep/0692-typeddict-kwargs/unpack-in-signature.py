# tier: spec
# ref: library/typing.html#typing.Unpack
# min-python: 3.12
# expect:
# [('a', 1), ('b', 'x')]
from typing import TypedDict, Unpack

class Opts(TypedDict):
    a: int
    b: str

def f(**kw: Unpack[Opts]):
    return sorted(kw.items())

print(f(a=1, b="x"))
