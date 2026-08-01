# tier: spec
# ref: library/functions.html#iter
# expect:
# 1 2
# StopIteration
# default
# True
xs = [1, 2]
it = iter(xs)
print(next(it), next(it))
try:
    next(it)
except StopIteration:
    print("StopIteration")
print(next(iter([]), "default"))
print(iter(it) is it)
