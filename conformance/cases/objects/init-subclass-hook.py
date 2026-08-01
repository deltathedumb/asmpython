# tier: spec
# ref: reference/datamodel.html#object.__init_subclass__
# expect:
# ['A', 'B']
seen = []

class Base:
    def __init_subclass__(cls, **kw):
        seen.append(cls.__name__)

class A(Base):
    pass

class B(Base):
    pass

print(seen)
