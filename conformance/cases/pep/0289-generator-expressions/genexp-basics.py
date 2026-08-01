# tier: spec
# ref: peps.python.org/pep-0289/
# expect:
# 14
# [0, 1, 2]
# 2
# True
print(sum(x * x for x in range(4)))
print(list(x for x in range(3)))
print(max(x % 3 for x in range(7)))
print(any(x > 2 for x in range(4)))
