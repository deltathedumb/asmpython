# expect:
# 1
# 3
# 5
# 5
# 1
# 2
# 4
# 5
# 3

xs = [1, 2, 3, 4, 5]
a = xs[::2]
b = xs[::-1]
c = xs[1::2]
d = xs[4:0:-2]
print(a[0])
print(a[1])
print(a[2])
print(b[0])
print(b[4])
print(c[0])
print(c[1])
print(d[0])
print(d[1])
