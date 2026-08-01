# tier: spec
# ref: library/typing.html#typing.TypeIs
# min-python: 3.13
# expect:
# True False
# (<class 'int'>,)
# True
from typing import TypeIs, get_args

def is_str(v: object) -> TypeIs[str]:
    return isinstance(v, str)

print(is_str("a"), is_str(1))
print(get_args(TypeIs[int]))
print(TypeIs[int].__class__.__name__ != "")
