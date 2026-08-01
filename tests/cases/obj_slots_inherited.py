# probes: a subclass sees the base's slots
# expect:
# 1
# 2
class Base:
    __slots__ = ("a",)


class Child(Base):
    __slots__ = ("b",)


c = Child()
c.a = 1
c.b = 2
print(c.a)
print(c.b)
