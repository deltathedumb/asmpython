# expect:
# [1, 2, 3]
print(sorted(set([3, 1, 2, 1, 3])))
# asmpython (beta/3.14.0) MISMATCH: prints "['1', '2', '3']\n" (wrong).
