# expect:
# 11
# 10
# 2
# 2
# 1

xs: list[int] = [1, 2, 3]
xs[0] += 10
xs[1] *= 5
xs[2] -= 1
print(xs[0])
print(xs[1])
print(xs[2])

d: dict[str, int] = {}
words = "the quick the".split()
for w in words:
    if w in d:
        d[w] += 1
    else:
        d[w] = 1
print(d["the"])
print(d["quick"])
