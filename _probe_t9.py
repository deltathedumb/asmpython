# list.extend, list.pop, list.insert
a = [1, 2, 3]
a.extend([4, 5])
print(a)

b = [1, 2, 3, 4, 5]
x = b.pop()
print(x, b)

y = b.pop(1)
print(y, b)

c = [1, 2, 4, 5]
c.insert(2, 3)
print(c)
