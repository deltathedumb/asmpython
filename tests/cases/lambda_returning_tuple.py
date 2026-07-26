# expect:
# (3, 6)
f = lambda x: (x, x * 2)
print(f(3))
# asmpython (beta/3.14.0) MISMATCH: prints '()\n' (wrong).
