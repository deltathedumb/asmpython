# tier: spec
# ref: library/abc.html#abc.ABCMeta.register
# expect:
# True
# True
# object
from abc import ABC

class Quacks(ABC):
    pass

class Duck:
    pass

Quacks.register(Duck)
print(issubclass(Duck, Quacks))
print(isinstance(Duck(), Quacks))
print(Duck.__mro__[1].__name__)
