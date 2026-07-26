# expect:
# [1, 2, 3]
vals = [3, 1, 2]
print(sorted(vals, key=lambda x: (x is None, x)))
# asmpython (beta/3.14.0) MISMATCH: prints '[3, 1, 2]\n' (wrong).
