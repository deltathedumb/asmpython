# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# 1 [2, 3]
# [1, 2] 3
# 1 [2, 3] 4
# 1 []
# list
a, *rest = [1, 2, 3]
print(a, rest)
*init, last = [1, 2, 3]
print(init, last)
a, *mid, b = [1, 2, 3, 4]
print(a, mid, b)
x, *empty = [1]
print(x, empty)
print(type(rest).__name__)
