# tier: spec
# ref: library/functions.html#any
# expect:
# True
# [0, 1]
# False
# [1, 0]
# False True
calls = []

def probe(v):
    calls.append(v)
    return v

print(any(probe(v) for v in (0, 1, 0)))
print(calls)
calls.clear()
print(all(probe(v) for v in (1, 0, 1)))
print(calls)
print(any([]), all([]))
