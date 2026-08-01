# tier: spec
# ref: reference/expressions.html#calls
# expect:
# (1, 2, 3)
# [1, 2, 3]
log = []

def probe(n):
    log.append(n)
    return n

def take(a, b, c):
    return (a, b, c)

print(take(probe(1), probe(2), probe(3)))
print(log)
