# expect:
# 3
# 1
# 0
# a
# c
# 2
# 2
# 3

import collections

c = collections.Counter(["a", "b", "a", "c", "a", "b"])
print(c["a"])
print(c["b"] - 1)
most = c.most_common(1)
pair: tuple = most[0]
print(c.total() - 6)

od = collections.OrderedDict()
od["x"] = "a"
od["y"] = "b"
od["z"] = "c"
k0: str = od.keys()[0]
k2: str = od.keys()[2]
print(k0)
print(k2)
print(len(od))

dd = collections.defaultdict("int")
dd["count"] = dd["count"] + 1
dd["count"] = dd["count"] + 1
count: int = dd["count"]
print(count)

dq = collections.deque([1, 2, 3])
dq.appendleft(0)
dq.pop()
print(len(dq))
