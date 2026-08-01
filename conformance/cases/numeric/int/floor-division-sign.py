# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# -4
# -4
# 2
# (-3, 2)
print(-7 // 2)
print(7 // -2)
print(-7 % 3)
print(divmod(-7, 3))
