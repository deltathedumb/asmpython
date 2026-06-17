# expect:
# 2
# 2
# 5
# 3

import bisect

a: list = [1, 2, 4, 5]
print(bisect.bisect_left(a, 3))
print(bisect.bisect_right(a, 3))

bisect.insort(a, 3)
print(len(a))
print(a[2])
