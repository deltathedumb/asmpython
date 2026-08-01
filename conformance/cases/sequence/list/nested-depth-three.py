# tier: spec
# ref: reference/expressions.html#subscriptions
# expect:
# [[0, 0, 0], [0, 9, 0], [0, 0, 0]]
# 9
# 0
m = [[0] * 3 for _ in range(3)]
m[1][1] = 9
print(m)
print(m[1][1])
print(m[0][2])
