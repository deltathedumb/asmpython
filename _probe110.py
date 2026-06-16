# probe110: collections module
from collections import defaultdict, Counter, deque, OrderedDict

# defaultdict
dd = defaultdict(int)
dd["a"] = dd["a"] + 1
dd["b"] = dd["b"] + 2
dd["a"] = dd["a"] + 1
print(dd["a"])   # 2
print(dd["b"])   # 2
print(dd["z"])   # 0 (default)

# Counter
c = Counter(["apple", "banana", "apple", "cherry", "banana", "apple"])
print(c["apple"])    # 3
print(c["banana"])   # 2
print(c["cherry"])   # 1

# deque
d = deque([1, 2, 3])
d.appendleft(0)
d.append(4)
print(list(d))   # [0, 1, 2, 3, 4]
print(d.popleft())  # 0
print(d.pop())      # 4
print(list(d))   # [1, 2, 3]
