# expect:
# 0
# 1
# 2
# 0

import weakref

print(weakref.getweakrefcount(42))
r1 = weakref.ref(100)
print(r1.__bool__())
r2 = weakref.ref(200)
print(r1.__bool__() + r2.__bool__())
print(weakref.getweakrefcount(0))
