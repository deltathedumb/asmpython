# expect:
# 3
# 0
# 0
# 3
# 3
# list
# 3

import collections

d = collections.deque([1, 2, 3])
print(len(d))
d.appendleft(0)
d.pop()
print(d[0])       # 0 (appendleft put 0 at front)
print(d.popleft()) # 0
d.append(10)
print(len(d))     # [1, 2, 10] -> 3

c = collections.Counter(["a", "b", "b", "c", "c", "c"])
print(c["c"])     # 3

dd = collections.defaultdict("list")
dd["x"]
dd["y"]
dd["z"]
print(dd.default_factory)
print(len(dd.keys()))
