# expect:
# 2
# 2
# abc
# 2.0
# 16

import operator

print(operator.countOf([1, 2, 3, 2, 1], 2))
print(operator.indexOf([10, 20, 30], 30))
print(operator.iconcat("ab", "c"))
print(operator.itruediv(10, 5))
print(operator.ipow(2, 4))
