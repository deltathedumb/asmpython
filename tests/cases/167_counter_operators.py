# expect:
# 3 3 1
# 1 0 0 False
# 1 1 0 False
# 2 2 1

from collections import Counter

c1 = Counter(["a", "a", "b"])
c2 = Counter(["a", "b", "b", "c"])

c3 = c1 + c2
print(c3["a"], c3["b"], c3["c"])

c4 = c1 - c2
print(c4["a"], c4["b"], c4["c"], "c" in c4)

c5 = c1 & c2
print(c5["a"], c5["b"], c5["c"], "c" in c5)

c6 = c1 | c2
print(c6["a"], c6["b"], c6["c"])
