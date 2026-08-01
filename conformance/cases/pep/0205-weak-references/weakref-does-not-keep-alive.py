# tier: impl
# ref: library/weakref.html
# expect:
# True
# True
# True
import weakref

class C:
    pass

c = C()
r = weakref.ref(c)
print(r() is c)
del c
print(r() is None)
print(callable(r))
