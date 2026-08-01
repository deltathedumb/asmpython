# tier: spec
# ref: reference/datamodel.html#object.__init_subclass__
# expect:
# [('A', 'a'), ('B', None)]
seen = []

class Base:
    def __init_subclass__(cls, tag=None, **kw):
        seen.append((cls.__name__, tag))
        super().__init_subclass__(**kw)

class A(Base, tag="a"):
    pass

class B(Base):
    pass

print(seen)
