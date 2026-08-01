# tier: spec
# ref: library/functions.html#property
# expect:
# 10
# 50
class C:
    def __init__(self):
        self._v = 1
    @property
    def v(self):
        return self._v * 10
    @v.setter
    def v(self, n):
        self._v = n + 1

c = C()
print(c.v)
c.v = 4
print(c.v)
