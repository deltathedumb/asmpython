# tier: spec
# ref: library/typing.html#typing.TypeAlias
# expect:
# list[float]
# True
from typing import TypeAlias

Vector: TypeAlias = list[float]
print(Vector)
print(Vector.__origin__ is list)
