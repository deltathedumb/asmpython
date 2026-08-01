# tier: spec
# ref: peps.python.org/pep-3132/
# expect:
# 1 [2, 3]
# [1, 2] 3
# 1 [2, 3] 4
# 1 []
a, *b = [1, 2, 3]
print(a, b)
*a, b = [1, 2, 3]
print(a, b)
a, *b, c = [1, 2, 3, 4]
print(a, b, c)
a, *b = [1]
print(a, b)
