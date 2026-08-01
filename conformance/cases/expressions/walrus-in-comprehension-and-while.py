# tier: spec
# ref: reference/expressions.html#assignment-expressions
# expect:
# [6, 8]
# 8
# 4 6
# len 4
data = [1, 2, 3, 4]
print([y for v in data if (y := v * 2) > 4])
print(y)
n = 0
total = 0
while (n := n + 1) < 4:
    total += n
print(n, total)
if (m := len(data)) > 3:
    print("len", m)
