# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# []
# 0
# [0]
# [1, 2]
# [0, 1, 2]
log = []

def gen():
    for i in range(3):
        log.append(i)
        yield i

g = gen()
print(log)
print(next(g))
print(log)
print(list(g))
print(log)
