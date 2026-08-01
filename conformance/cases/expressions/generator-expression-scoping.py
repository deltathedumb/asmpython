# tier: spec
# ref: reference/expressions.html#generator-expressions
# expect:
# [2, 4, 6, 8]
# [11]
# 2
xs = [1, 2, 3]
g = (v * 2 for v in xs)
xs.append(4)
print(list(g))
outer = 10
print(list(v + outer for v in [1]))
print(sum(v for v in range(4) if v % 2 == 0))
