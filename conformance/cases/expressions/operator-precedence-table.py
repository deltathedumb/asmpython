# tier: spec
# ref: reference/expressions.html#operator-precedence
# expect:
# 14
# 20
# 512
# -4
# True
# True
# 3
# 32
print(2 + 3 * 4)
print((2 + 3) * 4)
print(2 ** 3 ** 2)
print(-2 ** 2)
print(not True == False)
print(1 + 2 < 4 and 3 > 2)
print(1 | 2 ^ 3 & 4)
print(1 << 2 + 3)
