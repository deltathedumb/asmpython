# tier: spec
# ref: library/typing.html#typing.Annotated
# expect:
# (<class 'int'>, 'meta', 42)
# typing.Annotated
# typing.Annotated[int, 'positive']
# <class 'int'>
from typing import Annotated, get_args, get_origin, get_type_hints

A = Annotated[int, "meta", 42]
print(get_args(A))
print(get_origin(A) is int or get_origin(A))

def f(x: Annotated[int, "positive"]) -> None:
    pass

print(get_type_hints(f, include_extras=True)["x"])
print(get_type_hints(f)["x"])
