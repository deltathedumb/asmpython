# probe77: multiple return values, tuple operations
def minmax(lst: list) -> tuple:
    lo: int = lst[0]
    hi: int = lst[0]
    for v in lst:
        if v < lo:
            lo = v
        if v > hi:
            hi = v
    return lo, hi

lo, hi = minmax([3, 1, 4, 1, 5, 9, 2, 6])
print(lo)   # 1
print(hi)   # 9

# Tuple as dict value
d = {"a": (1, 2), "b": (3, 4)}
x, y = d["a"]
print(x)   # 1
print(y)   # 2

# Tuple in list comprehension result
pairs = [(i, i*i) for i in range(5)]
for n, sq in pairs:
    print(n, sq)

# Nested tuple
t = ((1, 2), (3, 4))
a, b = t
print(a[0], a[1])   # 1 2
print(b[0], b[1])   # 3 4
