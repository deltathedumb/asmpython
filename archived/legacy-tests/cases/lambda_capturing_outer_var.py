# expect:
# [30, 30, 30]
multiplier = 3
fns = []
for i in range(3):
    fns.append(lambda x, m=multiplier: x * m)
print([f(10) for f in fns])
# asmpython (beta/3.14.0) MISMATCH: prints '[100, 100, 100]\n' (wrong).
