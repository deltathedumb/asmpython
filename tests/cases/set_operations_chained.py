# expect:
# [1, 3, 4, 5]
a = {1, 2, 3, 4}
b = {2, 3}
c = {3, 4, 5}
print(sorted((a - b) | c))
# asmpython (beta/3.14.0) MISMATCH: prints "['1', '3', '4', '5']\n" (wrong).
