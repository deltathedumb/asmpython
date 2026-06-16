# probe14: abs, divmod, list.pop, list.insert, list.remove, list.count
print(abs(-5))
print(abs(3))

# list.pop
xs = [1, 2, 3, 4]
v = xs.pop()
print(v)
print(len(xs))

# list.pop(index)
ys = [10, 20, 30]
v2 = ys.pop(1)
print(v2)
print(len(ys))

# list.insert
zs = [1, 2, 3]
zs.insert(1, 99)
print(zs[1])
print(len(zs))
