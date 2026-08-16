# probes: a default argument captures at definition time
# expect:
# [0, 1, 2]
fns = []
for i in range(3):
    fns.append(lambda bound=i: bound)
print([f() for f in fns])
