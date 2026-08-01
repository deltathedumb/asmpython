# tier: spec
# ref: library/typing.html#typing.TypeVarTuple
# min-python: 3.11
# expect:
# Ts
# (<class 'int'>, <class 'str'>)
from typing import TypeVarTuple, Unpack, Generic

Ts = TypeVarTuple("Ts")

class Arr(Generic[Unpack[Ts]]):
    pass

print(Ts.__name__)
print(Arr[int, str].__args__)
