# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# 1 2 3
# 1
# [2, 3, 4]
# [1, 2]
# 3
a, (b, c) = 1, (2, 3)
print(a, b, c)
first, *rest = [1, 2, 3, 4]
print(first)
print(rest)
*init, last = [1, 2, 3]
print(init)
print(last)
