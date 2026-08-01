# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# []
# 1 ['a']
# 2 ['a', 'b']
log = []

def gen():
    log.append("a")
    yield 1
    log.append("b")
    yield 2
    log.append("c")

g = gen()
print(log)
print(next(g), log)
print(next(g), log)
