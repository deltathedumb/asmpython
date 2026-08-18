# COVERAGE: abstractmethod and the refusal to instantiate; a name left
# abstract by a base staying abstract; an override at two levels; register as
# a call and as a decorator; a subclass of a registered class counting;
# __subclasshook__ answering True, False and NotImplemented; the stacked
# @property/@classmethod spellings; update_abstractmethods; get_cache_token.
#
# THE REFUSAL IS THE FEATURE, so every case that should fail is written as a
# try/except that prints. A class that instantiates when it should not is the
# failure this module exists to prevent, and it is invisible unless asked.
import abc
from abc import ABC, ABCMeta, abstractmethod

# ---- the basic refusal -----------------------------------------------------
class Shape(ABC):
    @abstractmethod
    def area(self):
        ...

    def describe(self):
        return "area=" + str(self.area())


print(type(Shape).__name__, sorted(Shape.__abstractmethods__))
try:
    Shape()
except TypeError as e:
    print("refused:", "Can't instantiate abstract class Shape" in str(e))

class Square(Shape):
    def __init__(self, n):
        self.n = n

    def area(self):
        return self.n ** 2


print(Square(3).describe(), sorted(Square.__abstractmethods__))
print(isinstance(Square(1), Shape), issubclass(Square, Shape))

# A SUBCLASS THAT FILLS ONLY SOME is still abstract, and the message names
# every remaining one in sorted order.
class Two(ABC):
    @abstractmethod
    def a(self):
        ...

    @abstractmethod
    def b(self):
        ...


print(sorted(Two.__abstractmethods__))

class Half(Two):
    def a(self):
        return 1


print(sorted(Half.__abstractmethods__))
try:
    Half()
except TypeError as e:
    print("half refused:", "'b'" in str(e), "'a'" in str(e))

class Whole(Half):
    def b(self):
        return 2


print(len(Whole.__abstractmethods__), Whole().a(), Whole().b())

# ---- the stacked spellings -------------------------------------------------
class WithProp(ABC):
    @property
    @abstractmethod
    def value(self):
        ...

    @classmethod
    @abstractmethod
    def build(cls):
        ...


print(sorted(WithProp.__abstractmethods__))
try:
    WithProp()
except TypeError:
    print("prop refused")

class Filled(WithProp):
    @property
    def value(self):
        return 7

    @classmethod
    def build(cls):
        return cls()


print(Filled.build().value, len(Filled.__abstractmethods__))

# The deprecated decorators, which mark the same way.
class OldStyle(ABC):
    @abc.abstractproperty
    def here(self):
        ...


print(sorted(OldStyle.__abstractmethods__))

# ---- registration ----------------------------------------------------------
class Quacks(ABC):
    pass


class Duck:
    pass


class Mallard(Duck):
    pass


Quacks.register(Duck)
print(issubclass(Duck, Quacks), isinstance(Duck(), Quacks))
# A SUBCLASS OF A REGISTERED CLASS COUNTS -- registration is about the whole
# subtree, not the one class named.
print(issubclass(Mallard, Quacks), isinstance(Mallard(), Quacks))
print(Duck.__mro__[1].__name__)

# Nothing about the registered class changed: its own hierarchy never mentions
# the ABC, which is the entire point of a virtual subclass.
print(Quacks in Duck.__mro__)


class Unrelated:
    pass


print(issubclass(Unrelated, Quacks), isinstance(Unrelated(), Quacks))

# `register` RETURNS ITS ARGUMENT, so it works as a decorator.
@Quacks.register
class Decorated:
    pass


print(Decorated.__name__, issubclass(Decorated, Quacks))

# ---- __subclasshook__ ------------------------------------------------------
class Walks(ABC):
    @classmethod
    def __subclasshook__(cls, C):
        # NotImplemented MEANS "CARRY ON ASKING", which is what lets the
        # registry still have a say; True and False are decisions.
        if cls is not Walks:
            return NotImplemented
        if hasattr(C, "walk"):
            return True
        if hasattr(C, "refuses"):
            return False
        return NotImplemented


class Walker:
    def walk(self):
        return "step"


class Refuser:
    refuses = True


class Silent:
    pass


print(issubclass(Walker, Walks), isinstance(Walker(), Walks))
print(issubclass(Refuser, Walks), issubclass(Silent, Walks))
# A HOOK THAT SAID False WINS OVER THE REGISTRY -- a refusal is a decision.
Walks.register(Refuser)
print("after register:", issubclass(Refuser, Walks))
Walks.register(Silent)
print("silent after register:", issubclass(Silent, Walks))

# ---- update_abstractmethods ------------------------------------------------
class Late(ABC):
    @abstractmethod
    def later(self):
        ...


print(sorted(Late.__abstractmethods__))
Late.later = lambda self: "filled"
abc.update_abstractmethods(Late)
print(sorted(Late.__abstractmethods__), Late().later())

# ---- the cache token -------------------------------------------------------
before = abc.get_cache_token()
class Fresh(ABC):
    pass


class Other:
    pass


Fresh.register(Other)
print("token moved:", abc.get_cache_token() != before)

# ---- metaclass directly ----------------------------------------------------
class Direct(metaclass=ABCMeta):
    @abstractmethod
    def run(self):
        ...


print(type(Direct).__name__, sorted(Direct.__abstractmethods__))
try:
    Direct()
except TypeError:
    print("direct refused")


class Impl(Direct):
    def run(self):
        return "ran"


print(Impl().run(), len(Impl.__abstractmethods__))
