# expect:
# 50
# 40
# 10
# 20
# updated:
# 10
# 22
# 30
# 44
# 50

xs = [10, 20, 30, 40, 50]
print(xs[-1])
print(xs[-2])
print(xs[-5])

# Mixed read paths.
i = -4
print(xs[i])

# Write through a negative index.
print("updated:")
xs[-2] = 44
xs[-4] = 22
for v in xs:
    print(v)
