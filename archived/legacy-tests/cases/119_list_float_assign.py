# expect:
# [1.0, 9.5, 3.0]
# [1.0, 9.5, 7.25]
# 17.75

xs = [1.0, 2.0, 3.0]
xs[1] = 9.5
print(xs)
xs[-1] = 7.25
print(xs)
total = 0.0
for x in xs:
    total += x
print(total)
