# tier: impl
# ref: library/weakref.html#weakref.proxy
# expect:
# 1
# ['collected']
# True
import weakref

log = []

class C:
    v = 1

c = C()
r = weakref.ref(c, lambda ref: log.append("collected"))
p = weakref.proxy(c)
print(p.v)
del c
print(log)
print(r() is None)
