# expect:
# ('b', 1)
# b
# 1
# ('a', 3)
# a

pairs = [("a", 3), ("b", 1), ("c", 2)]

m = min(pairs, key=lambda p: p[1])
print(m)
print(m[0])
print(m[1])

mx = max(pairs, key=lambda p: p[1])
print(mx)
print(mx[0])
