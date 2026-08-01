# tier: spec
# ref: library/abc.html
# expect:
# ABCMeta
# ['run']
# TypeError
# ran
# 0
from abc import ABCMeta, abstractmethod

class Base(metaclass=ABCMeta):
    @abstractmethod
    def run(self):
        ...

print(type(Base).__name__)
print(sorted(Base.__abstractmethods__))
try:
    Base()
except TypeError:
    print("TypeError")

class Impl(Base):
    def run(self):
        return "ran"

print(Impl().run())
print(len(Impl.__abstractmethods__))
