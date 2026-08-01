# tier: spec
# ref: reference/expressions.html#shifting-operations
# expect:
# -4 -16
# -1
# -1 0
# 5 -3 -6
# ValueError
print(-8 >> 1, -8 << 1)
print(-1 >> 10)
print(~0, ~-1)
print(5 & -1, 5 | -8, 5 ^ -1)
try:
    1 << -1
except ValueError:
    print("ValueError")
