# expect:
# 1
# 2
# 3
# 3
# 2
# 1
# 2
# 0
# 0
# 1
# 2
# 1
# 2
# 3
# 1
# 3
# 2

# list.sort, reverse, count, clear, copy, insert, remove.

a = [3, 1, 2]
a.sort()
print(a[0])   # 1
print(a[1])   # 2
print(a[2])   # 3

a.reverse()
print(a[0])   # 3
print(a[1])   # 2
print(a[2])   # 1

b = [1, 2, 1, 3]
print(b.count(1))  # 2
print(b.count(4))  # 0

c = b.copy()
b.clear()
print(len(b))  # 0
print(c[0])    # 1
print(c[1])    # 2

d = [1, 3]
d.insert(1, 2)
print(d[0])   # 1
print(d[1])   # 2  (inserted)
print(d[2])   # 3 (shifted)

e = [1, 2, 3, 2]
e.remove(2)
print(e[0])   # 1
print(e[1])   # 3
print(e[2])   # 2  (only first removed)
