# expect:
# 2
# 3
# 5
# 6
# 4
# 5

import bisect

a: list = [1, 3, 5, 7, 9]
print(bisect.bisect_left(a, 4))
print(bisect.bisect_right(a, 5))
print(len(a))

bisect.insort(a, 4)
print(len(a))
print(a[2])
print(a[3])
