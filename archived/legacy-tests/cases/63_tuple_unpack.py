# expect:
# 1
# 2
# label: a
# n: 5
# 1.5
# 2.5
# 0
# 1
# 2
# sum: 3

# Unpack a tuple literal directly into names.
a, b = (1, 2)
print(a)
print(b)

# Heterogeneous unpack: int + str, in either order.
n, label = (5, "a")
print("label:", label)
print("n:", n)

# Float unpack exercises the movsd copy path.
fa, fb = (1.5, 2.5)
print(fa)
print(fb)

# Iterate a homogeneous tuple.
nums = (0, 1, 2)
total = 0
for v in nums:
    print(v)
    total = total + v
print("sum:", total)
