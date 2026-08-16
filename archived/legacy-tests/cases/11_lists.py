# expect:
# 3
# 10
# 20
# 30
# 5
# 99
# 20
# 30
# sum = 55
xs = [10, 20, 30]
print(len(xs))
for x in xs:
    print(x)
xs[0] = 5
print(xs[0])
xs.append(99)
last = xs.pop()
print(last)
print(xs[1])
print(xs[2])
total = 0
for x in xs:
    total += x
print("sum", "=", total)
