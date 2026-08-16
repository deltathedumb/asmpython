# expect:
# [1, 2, 3, 4]
nums = [3, 1, 2, 3, 1, 4]
print(sorted(set(nums)))
# asmpython (beta/3.14.0) MISMATCH: prints "['1', '2', '3', '4']\n" (wrong).
