# probes: bisect.insort inserts in sorted position
# expect:
# [1, 3, 4, 5]
import bisect

xs = [1, 3, 5]
bisect.insort(xs, 4)
print(xs)
