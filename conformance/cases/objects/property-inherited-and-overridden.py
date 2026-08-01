# tier: spec
# ref: library/functions.html#property
# expect:
# base
# sub:base
class Base:
    @property
    def v(self):
        return "base"

class Sub(Base):
    @property
    def v(self):
        return "sub:" + Base.v.fget(self)

print(Base().v)
print(Sub().v)
