# expect:
# 1.5
# 2.5
# 2.5
# 1.5
# 7.0
# 7.0
# 17.0
# 4.0
a, b = 1.5, 2.5
print(a)
print(b)
a, b = b, a
print(a)
print(b)
total: float = 0.0
for x in [1.5, 2.5, 3.0]:
    total = total + x
print(total)
print(sum([1.5, 2.5, 3.0]))
print(sum([1.5, 2.5, 3.0], 10.0))
print(sum([2.0, 4.0, 6.0]) / 3.0)
