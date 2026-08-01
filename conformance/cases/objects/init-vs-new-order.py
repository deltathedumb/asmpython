# tier: spec
# ref: reference/datamodel.html#object.__new__
# expect:
# ['new', 'init']
# 1
log = []

class C:
    def __new__(cls, *a):
        log.append("new")
        return super().__new__(cls)
    def __init__(self, v):
        log.append("init")
        self.v = v

c = C(1)
print(log)
print(c.v)
