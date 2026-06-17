# expect:
# 3
# 2
# a 3
# a 3
# b 2
# 4
# 0
# 3
# 3

from collections import deque, Counter, defaultdict, OrderedDict

# Counter
c = Counter(["a", "b", "a", "c", "a", "b"])
print(c["a"])
print(c["b"])
mc = c.most_common(2)
print(mc[0][0], mc[0][1])
for el, cnt in mc:
    print(el, cnt)

# deque
d = deque([1, 2, 3])
d.append(4)
d.appendleft(0)
print(d.pop())
print(d.popleft())
print(len(d))

# defaultdict
dd = defaultdict("int")
dd["x"] = dd["x"] + 1
dd["x"] = dd["x"] + 1
dd["x"] = dd["x"] + 1
print(dd["x"])
