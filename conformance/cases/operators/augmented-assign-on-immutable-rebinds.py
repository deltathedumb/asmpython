# tier: spec
# ref: reference/simple_stmts.html#augmented-assignment-statements
# expect:
# (1, 2, 3)
# (1, 2)
# [1, 2, 3]
# [1, 2, 3]
# True
a = (1, 2)
b = a
a += (3,)
print(a)
print(b)

x = [1, 2]
y = x
x += [3]
print(x)
print(y)
print(x is y)
