# probes: OrderedDict.popitem accepts last=
# expect:
# ('a', 1)
import collections

d = collections.OrderedDict()
d["a"] = 1
d["b"] = 2
print(d.popitem(last=False))
