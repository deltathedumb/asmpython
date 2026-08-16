# expect:
# 1.5
# 2.5
# 3.5
# 7.5
# 1.5
# 2.5
# 3.5
# 4.5
# 4
# 4.5
xs = [1.5, 2.5, 3.5]
print(xs[0])
print(xs[1])
print(xs[2])

total = xs[0] + xs[1] + xs[2]
print(total)

xs.append(4.5)
for x in xs:
    print(x)

print(len(xs))
last = xs.pop()
print(last)
