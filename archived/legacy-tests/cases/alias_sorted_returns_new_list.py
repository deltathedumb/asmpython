# probes: sorted() does not alias its input
# expect:
# [3, 1]
# [1, 3, 9]
a = [3, 1]
b = sorted(a)
b.append(9)
print(a)
print(b)
