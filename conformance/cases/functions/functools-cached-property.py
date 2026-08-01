# tier: spec
# ref: library/functools.html#functools.cached_property
# expect:
# 42 42
# ['computed']
# 42
# 2
import functools

calls = []

class C:
    @functools.cached_property
    def value(self):
        calls.append("computed")
        return 42

c = C()
print(c.value, c.value)
print(calls)
print(C().value)
print(len(calls))
