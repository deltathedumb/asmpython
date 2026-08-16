# expect:
# 1
# 2
# 3

from bisect import bisect_left, bisect_right, insort_left

a = [1, 3, 5, 7, 9]
print(bisect_left(a, 3))
print(bisect_right(a, 3))
print(bisect_left(a, 6))
