# expect:
# 0 1
g = (x * x for x in range(4))
print(next(g), next(g))
# asmpython (beta/3.14.0) MISMATCH: prints '0 0\n' (wrong).
