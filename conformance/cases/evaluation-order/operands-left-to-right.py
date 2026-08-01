# tier: spec
# ref: reference/expressions.html#evaluation-order
# expect:
# 7
# [1, 2, 3]
log = []

def probe(n):
    log.append(n)
    return n

print(probe(1) + probe(2) * probe(3))
print(log)
