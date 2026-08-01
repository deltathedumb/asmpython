# tier: spec
# ref: reference/datamodel.html#object.__iadd__
# expect:
# [1, 2] [1, 2] True
# [1, 2] [1] False
a = [1]
b = a
a += [2]
print(a, b, a is b)

c = [1]
d = c
c = c + [2]
print(c, d, c is d)
