# expect:
# 7
# 3
# 10
# 0
# 2
# 1
# 1
# 0
# 1
# 0

import operator

print(operator.add(3, 4))
print(operator.sub(7, 4))
print(operator.mul(2, 5))
print(operator.floordiv(7, 3) - 2)
print(operator.mod(7, 5))
print(operator.neg(-1))
print(operator.eq(5, 5))
print(operator.ne(5, 5))
print(operator.lt(3, 5))
print(operator.gt(3, 5))
