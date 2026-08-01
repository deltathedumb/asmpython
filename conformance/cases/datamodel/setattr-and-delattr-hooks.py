# tier: spec
# ref: reference/datamodel.html#object.__setattr__
# expect:
# 1
# [('set', 'v', 1), ('del', 'v')]
# False
log = []

class C:
    def __setattr__(self, name, value):
        log.append(("set", name, value))
        object.__setattr__(self, name, value)
    def __delattr__(self, name):
        log.append(("del", name))
        object.__delattr__(self, name)

c = C()
c.v = 1
print(c.v)
del c.v
print(log)
print(hasattr(c, "v"))
