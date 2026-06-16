# probe42: list operations
a = [3, 1, 4, 1, 5, 9, 2, 6]
a.sort()
print(a)  # [1, 1, 2, 3, 4, 5, 6, 9]

b = [1, 2, 3]
b.insert(1, 99)
print(b)  # [1, 99, 2, 3]

c = [1, 2, 3, 2, 1]
print(c.count(2))  # 2
print(c.index(3))  # 2

d = [1, 2, 3]
d.extend([4, 5])
print(d)  # [1, 2, 3, 4, 5]

e = [1, 2, 3]
print(e.pop())   # 3
print(e)         # [1, 2]

f = [1, 2, 3, 2]
f.remove(2)
print(f)  # [1, 3, 2]

g = [1, 2, 3]
g.reverse()
print(g)  # [3, 2, 1]

# list multiplication
h = [0] * 5
print(h)  # [0, 0, 0, 0, 0]
