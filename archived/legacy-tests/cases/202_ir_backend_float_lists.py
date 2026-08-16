# expect:
# 1.5
# 3.25
# 4.5
# 4.75
xs = [1.5, 2.5]
print(xs[0])
xs[1] = 3.25
print(xs[1])
xs.append(4.5)
print(xs.pop())

total = 0.0
for x in xs:
    total += x
print(total)
