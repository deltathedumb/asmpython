# tier: spec
# ref: library/typing.html#typing.ParamSpec
# expect:
# P
# P.args P.kwargs
# True
from typing import ParamSpec, Callable, get_args

P = ParamSpec("P")
print(P.__name__)
print(P.args, P.kwargs)
C = Callable[P, int]
print(get_args(C)[1] is int)
