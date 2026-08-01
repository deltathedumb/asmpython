# tier: spec
# ref: library/typing.html#typing.override
# min-python: 3.12
# expect:
# sub
# True
from typing import override

class Base:
    def m(self):
        return "base"

class Sub(Base):
    @override
    def m(self):
        return "sub"

print(Sub().m())
print(Sub.m.__override__)
