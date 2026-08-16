# expect:
# [4, 6, 8]
print(sorted({x * 2 for x in range(5) if x > 1}))
# asmpython (beta/3.14.0) MISMATCH: prints "['4', '6', '8']\n" (wrong).
