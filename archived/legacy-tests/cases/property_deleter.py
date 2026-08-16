# expect:
# 0
class C:
    def __init__(self):
        self._x = 10
    @property
    def x(self):
        return self._x
    @x.deleter
    def x(self):
        self._x = 0
c = C()
del c.x
print(c._x)
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt Del (Attr)
