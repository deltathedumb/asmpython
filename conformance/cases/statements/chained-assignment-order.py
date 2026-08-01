# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# [1, 2] [1, 2] True
# 2 1
a = b = [1]
a.append(2)
print(a, b, a is b)

x, y = 1, 2
x, y = y, x
print(x, y)
