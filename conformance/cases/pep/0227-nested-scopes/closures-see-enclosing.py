# tier: spec
# ref: reference/executionmodel.html#resolution-of-names
# expect:
# 6
# [0, 1, 2]
def outer(a):
    def middle(b):
        def inner(c):
            return a + b + c
        return inner
    return middle

print(outer(1)(2)(3))
fns = [outer(i)(0) for i in range(3)]
print([f(0) for f in fns])
