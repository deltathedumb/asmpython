# tier: spec
# ref: reference/expressions.html#expression-lists
# expect:
# 1
# 2
# 1
# (1,)
# 9
def f(*a):
    return len(a)

print(f((1, 2)))
print(f(*(1, 2)))
print((1))
print((1,))
a, = [9]
print(a)
