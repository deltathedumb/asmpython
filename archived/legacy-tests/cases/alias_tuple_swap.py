# probes: a, b = b, a swaps without aliasing
# expect:
# [2]
# [1]
# [1]
a = [1]
b = [2]
a, b = b, a
print(a)
print(b)
a.append(9)
print(b)
