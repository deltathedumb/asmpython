# tier: spec
# ref: reference/datamodel.html#implementing-descriptors
# expect:
# 10
# 5
class Doubler:
    def __get__(self, obj, objtype=None):
        return obj._v * 2
    def __set__(self, obj, value):
        obj._v = value

class C:
    v = Doubler()

c = C()
c.v = 5
print(c.v)
print(c._v)
