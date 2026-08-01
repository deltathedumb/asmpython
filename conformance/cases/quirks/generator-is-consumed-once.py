# tier: spec
# ref: reference/expressions.html#generator-expressions
# expect:
# [0, 1, 2]
# []
# 3
# False
# []
g = (v for v in range(3))
print(list(g))
print(list(g))
print(sum(v for v in range(3)))
squares = (v * v for v in range(3))
print(2 in squares)
print(list(squares))
