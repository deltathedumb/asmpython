# expect:
# [0, 1, 2]
print(sorted(set(x % 3 for x in range(10))))
# asmpython (beta/3.14.0) MISMATCH: prints "['0', '1', '2']\n" (wrong).
