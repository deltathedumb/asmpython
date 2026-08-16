# expect:
# 0
# True
# True
# False
# True
# False
# True
# False

# set.clear, union, intersection, difference.

s = {"a", "b", "c"}
s.clear()
print(len(s))   # 0

a = {"x", "y"}
b = {"y", "z"}

u = a.union(b)
print("x" in u)  # True
print("y" in u)  # True
print("q" in u)  # False

i = a.intersection(b)
print("y" in i)  # True
print("x" in i)  # False

d = a.difference(b)
print("x" in d)  # True
print("y" in d)  # False
