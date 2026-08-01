# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 3.5 3
# float
# True
# -4 -3.5
# 3.0
print(7 / 2, 7 // 2)
print(type(6 / 3).__name__)
print(6 / 3 == 2)
print(-7 // 2, 7 / -2)
print(7.0 // 2)
