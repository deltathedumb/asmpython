# tier: spec
# ref: library/functions.html#sum
# expect:
# 6
# 10
# 4.0
# 1 3
# none
# a
print(sum([1, 2, 3]))
print(sum([], 10))
print(sum([1.5, 2.5]))
print(min([3, 1, 2]), max([3, 1, 2]))
print(min([], default="none"))
print(min("cba"))
