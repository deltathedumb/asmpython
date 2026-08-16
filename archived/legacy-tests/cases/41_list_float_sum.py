# expect:
# 3
# 66
# 1.1
# 2.2
# 3.3
xs = []
xs.append(1.1)
xs.append(2.2)
xs.append(3.3)

# Iterate twice: once to count, once to sum.
n = 0
for v in xs:
    n = n + 1
print(n)

total = 0.0
for v in xs:
    total = total + v
# Result is a float ~6.6; print as int to avoid format quirks.
print(int(total * 10))

# Index access.
print(xs[0])
print(xs[1])
print(xs[2])
