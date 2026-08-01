# tier: spec
# ref: library/typing.html#typing.Final
# expect:
# 10
# Still
# True
from typing import Final, final

MAX: Final = 10
print(MAX)

@final
class Sealed:
    pass

class Still(Sealed):
    pass

print(Still.__name__)
print(getattr(Sealed, "__final__", False))
