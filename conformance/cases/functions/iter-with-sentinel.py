# tier: spec
# ref: library/functions.html#iter
# expect:
# [1, 2]
# 3
# [1, 2]
data = [1, 2, 0, 3]
it = iter(data)
print(list(iter(lambda: next(it), 0)))
print(next(it))
print(list(iter([1, 2])))
