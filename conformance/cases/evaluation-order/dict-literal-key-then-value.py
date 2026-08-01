# tier: spec
# ref: reference/expressions.html#dictionary-displays
# expect:
# ['k1', 'v1', 'k2', 'v2']
# ['k1', 'k2']
log = []

def probe(n):
    log.append(n)
    return n

d = {probe("k1"): probe("v1"), probe("k2"): probe("v2")}
print(log)
print(sorted(d))
