# probe111: complex slice patterns
xs = list(range(10))   # [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

# Slice copy
ys = xs[:]
ys[0] = 99
print(xs[0])   # 0 (unchanged)
print(ys[0])   # 99

# Negative step slice (reversed)
rev = xs[::-1]
print(rev[0])   # 9
print(rev[9])   # 0

# Slice of slice
sub = xs[2:8]
sub2 = sub[1:4]
print(sub2)   # [3, 4, 5]

# String slicing
s = "hello world"
print(s[:5])     # hello
print(s[6:])     # world
print(s[::2])    # hlowrd
print(s[::-1])   # dlrow olleh

# Slice with negative indices
print(xs[-3:])    # [7, 8, 9]
print(xs[:-3])    # [0, 1, 2, 3, 4, 5, 6]
print(xs[-5:-2])  # [5, 6, 7]

# Slice assignment (extend)
zs = [1, 2, 3, 4, 5]
zs[2:2] = [10, 20]  # insert at position 2
print(zs)   # Python: [1, 2, 10, 20, 3, 4, 5] - may be unsupported
