# expect:
# [1, 10, 20, 2, 3]
a = [1, 2, 3]
a[1:1] = [10, 20]
print(a)
# asmpython (beta/3.14.0) MISMATCH: prints '[1, 2, 3]\n' (wrong).
