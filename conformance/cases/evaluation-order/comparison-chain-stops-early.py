# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# False
# [1, 2]
log = []

def probe(n):
    log.append(n)
    return n

print(probe(1) > probe(2) > probe(3))
print(log)
