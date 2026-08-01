# tier: spec
# ref: reference/datamodel.html#slots
# expect:
# 1 2
# False
# AttributeError
# 1
class Base:
    __slots__ = ("a",)

class Sub(Base):
    __slots__ = ("b",)

s = Sub()
s.a, s.b = 1, 2
print(s.a, s.b)
print(hasattr(s, "__dict__"))
try:
    s.c = 3
except AttributeError:
    print("AttributeError")

class WithDict(Base):
    pass

w = WithDict()
w.anything = 1
print(w.anything)
