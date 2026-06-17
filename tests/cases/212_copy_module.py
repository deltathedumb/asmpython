# expect:
# 3
# 1
# True

import copy

a: list = [1, 2, 3]
b: list = copy.copy(a)
print(len(b))
print(b[0])

c: list = copy.deepcopy(a)
b.append(4)
print(len(c) == 3)
