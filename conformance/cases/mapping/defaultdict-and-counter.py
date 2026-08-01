# tier: spec
# ref: library/collections.html#collections.defaultdict
# expect:
# [('a', [1])]
# False
# [] True
# [('a', 2), ('b', 3), ('c', 1)]
# [('b', 3)]
# 0
# [('a', 3), ('b', 3), ('c', 1)]
from collections import defaultdict, Counter

d = defaultdict(list)
d["a"].append(1)
print(sorted(d.items()))
print("b" in d)
print(d["b"], "b" in d)

c = Counter("aabbbc")
print(sorted(c.items()))
print(c.most_common(1))
print(c["z"])
print(sorted((c + Counter("a")).items()))
