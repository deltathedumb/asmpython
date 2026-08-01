# tier: spec
# ref: reference/expressions.html#generator-expressions
# expect:
# []
# 0 [0]
# [1, 2] [0, 1, 2]
# 14
# 3
log = []

def probe(v):
    log.append(v)
    return v

g = (probe(v) for v in range(3))
print(log)
print(next(g), log)
print(list(g), log)
print(sum(v * v for v in range(4)))
print(max((v for v in [3, 1]), default=None))
