# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 9223372036854775808
# 1180591620717411303424
# -9223372036854775809
print(9223372036854775807 + 1)
print(2 ** 70)
print(-9223372036854775808 - 1)
