# expect:
# [('a', 1), ('b', 2), ('c', 3)]
d = dict(a=1, b=2, c=3)
print(sorted(d.items()))
# asmpython (beta/3.14.0) MISMATCH: prints '[]\n' (wrong).
