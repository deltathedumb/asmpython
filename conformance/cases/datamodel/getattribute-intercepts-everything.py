# tier: spec
# ref: reference/datamodel.html#object.__getattribute__
# expect:
# 1
# fallback:missing
# ['x', 'missing']
log = []

class C:
    x = 1
    def __getattribute__(self, name):
        log.append(name)
        return object.__getattribute__(self, name)
    def __getattr__(self, name):
        return "fallback:" + name

c = C()
print(c.x)
print(c.missing)
print(log)
