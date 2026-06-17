# expect:
# 3
# 1
# 2
# 3
# 10
# 1
# 1
# 2
# 3

import copy

a: list = [1, 2, 3]
b: list = copy.copy(a)
print(len(b))
print(b[0])
print(b[1])
print(b[2])

b[0] = 10
print(b[0])
print(a[0])

c: list = copy.deepcopy(a)
print(c[0])
print(c[1])
print(c[2])
