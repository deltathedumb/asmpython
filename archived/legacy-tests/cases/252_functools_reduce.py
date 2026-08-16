# expect:
# 15
# 120

import functools

total: int = functools.reduce(lambda a, x: a + x, [1, 2, 3, 4, 5], 0)
print(total)

prod: int = functools.reduce(lambda a, x: a * x, [1, 2, 3, 4, 5], 1)
print(prod)
