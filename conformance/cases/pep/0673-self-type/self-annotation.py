# tier: spec
# ref: library/typing.html#typing.Self
# min-python: 3.11
# expect:
# ['a', 'b']
from typing import Self

class Builder:
    def __init__(self):
        self.parts = []
    def add(self, p) -> Self:
        self.parts.append(p)
        return self

print(Builder().add("a").add("b").parts)
