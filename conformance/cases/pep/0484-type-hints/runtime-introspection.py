# tier: spec
# ref: library/typing.html
# expect:
# ['return', 'x', 'y']
# int | None
# list[str]
# True
# (<class 'int'>, <class 'NoneType'>)
# T 1
from typing import Optional, Union, TypeVar, Generic, get_type_hints, get_origin, get_args

T = TypeVar("T")

class Box(Generic[T]):
    def __init__(self, v: T):
        self.v = v

def f(x: Optional[int], y: "list[str]") -> Union[int, str]:
    return 1

hints = get_type_hints(f)
print(sorted(hints))
print(hints["x"])
print(hints["y"])
print(get_origin(Box[int]) is Box)
print(get_args(Optional[int]))
print(T.__name__, Box(1).v)
