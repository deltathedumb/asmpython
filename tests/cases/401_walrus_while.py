# expect:
# 1
# 4
# 9

xs = [1, 2, 3]
i = 0
while i < len(xs):
    x = xs[i]
    print(x * x)
    i += 1
