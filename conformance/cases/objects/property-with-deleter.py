# tier: spec
# ref: library/functions.html#property
# expect:
# 1
# 5
# deleted
# property
class C:
    def __init__(self):
        self._v = 1
    @property
    def v(self):
        return self._v
    @v.setter
    def v(self, n):
        self._v = n
    @v.deleter
    def v(self):
        self._v = "deleted"

c = C()
print(c.v)
c.v = 5
print(c.v)
del c.v
print(c.v)
print(type(C.v).__name__)
