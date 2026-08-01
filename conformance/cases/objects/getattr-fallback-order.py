# tier: spec
# ref: reference/datamodel.html#object.__getattr__
# expect:
# 1
# missing:nope
class C:
    def __init__(self):
        self.real = 1
    def __getattr__(self, name):
        return "missing:" + name

c = C()
print(c.real)
print(c.nope)
