# expect:
# [0, 1, 2, 3, 4]
print(sorted(set(range(3)) | set(range(2, 5))))
# asmpython (beta/3.14.0) MISMATCH: prints "['0', '1', '2', '3', '4']\n" (wrong).
