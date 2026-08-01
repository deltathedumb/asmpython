# tier: spec
# ref: library/typing.html#typing.Protocol
# expect:
# True
# False
# closed
from typing import Protocol, runtime_checkable

@runtime_checkable
class Closeable(Protocol):
    def close(self) -> None: ...

class Resource:
    def close(self):
        return "closed"

class Other:
    pass

print(isinstance(Resource(), Closeable))
print(isinstance(Other(), Closeable))
print(Resource().close())
