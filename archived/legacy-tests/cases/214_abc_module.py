# expect:
# 42
# hello

import abc

class Base(abc.ABC):
    def value(self) -> int:
        return 0

    def greet(self) -> str:
        return ""

class Concrete(Base):
    def value(self) -> int:
        return 42

    def greet(self) -> str:
        return "hello"

obj = Concrete()
print(obj.value())
print(obj.greet())
