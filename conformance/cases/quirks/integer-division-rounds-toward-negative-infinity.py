# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 3 -4
# 3 -3
# 1 -1
# (-4, 1)
print(7 // 2, -7 // 2)
print(int(7 / 2), int(-7 / 2))
print(-7 % 2, 7 % -2)
print(divmod(-7, 2))
