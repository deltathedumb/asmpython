# probes: bisect_left and bisect_right straddle a run
# expect:
# 1
# 3
import bisect

xs = [1, 2, 2, 3]
print(bisect.bisect_left(xs, 2))
print(bisect.bisect_right(xs, 2))
