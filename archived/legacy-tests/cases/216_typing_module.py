# expect:
# T
# 42

from typing import TypeVar

T = TypeVar('T')
print(str(T))

x: int = 42
print(x)
