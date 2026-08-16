# probes: deque.appendleft prepends
# expect:
# [1, 2, 3]
import collections

d = collections.deque([2, 3])
d.appendleft(1)
print(list(d))
