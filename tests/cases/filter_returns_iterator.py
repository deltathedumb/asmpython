# expect:
# 3
f = filter(lambda x: x > 2, [1, 2, 3, 4])
print(next(f))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
