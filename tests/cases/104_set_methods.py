# expect:
# 0
# 1
# 1
# 0
# 1
# 0
# 1
# 1
# 0

# set.clear, union, intersection, difference.

s = {"a", "b", "c"}
s.clear()
print(len(s))   # 0

a = {"x", "y"}
b = {"y", "z"}

u = a.union(b)
print("x" in u)  # 1
print("y" in u)  # 1
print("q" in u)  # 0

i = a.intersection(b)
print("y" in i)  # 1
print("x" in i)  # 0

d = a.difference(b)
print("x" in d)  # 1
print("y" in d)  # 0
