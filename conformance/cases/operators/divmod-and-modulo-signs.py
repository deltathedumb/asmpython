# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 7 3 (2, 1) 1
# -7 3 (-3, 2) 2
# 7 -3 (-3, -2) -2
# -7 -3 (2, -1) -1
for a, b in ((7, 3), (-7, 3), (7, -3), (-7, -3)):
    print(a, b, divmod(a, b), a % b)
