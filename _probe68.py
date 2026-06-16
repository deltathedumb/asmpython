# probe68: multiple assignment, tuple unpacking, swap
a, b = 1, 2
print(a, b)   # 1 2

a, b = b, a
print(a, b)   # 2 1

# Extended unpacking
first, *rest = [1, 2, 3, 4, 5]
print(first)       # 1
print(len(rest))   # 4
print(rest[0])     # 2
print(rest[3])     # 5

*init, last = [10, 20, 30, 40]
print(last)         # 40
print(len(init))    # 3
print(init[0])      # 10

# Nested unpacking
(x, y), z = (1, 2), 3
print(x, y, z)   # 1 2 3

# Multi-target assign
c = d = 5
print(c, d)   # 5 5
