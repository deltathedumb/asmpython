# tier: spec
# ref: library/typing.html#typing.TypeGuard
# expect:
# True
# False
# (<class 'int'>,)
from typing import TypeGuard, get_args

def is_str_list(v: list) -> TypeGuard[list[str]]:
    return all(isinstance(x, str) for x in v)

print(is_str_list(["a"]))
print(is_str_list([1]))
print(get_args(TypeGuard[int]))
