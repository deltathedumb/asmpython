# tier: spec
# ref: reference/expressions.html#atom-identifiers
# expect:
# [2, 2, 2]
# [0, 1, 2]
fs = []
for i in range(3):
    fs.append(lambda: i)
print([f() for f in fs])

gs = []
for i in range(3):
    gs.append(lambda i=i: i)
print([g() for g in gs])
