# expect:
# 0
# 1
# 2
# 3
# 4
# 5
# 6
# 7
# 8
# 9
# len = 10
# squares:
# 0
# 1
# 4
# 9
# 16
xs = []
for i in range(10):
    xs.append(i)
for x in xs:
    print(x)
print("len", "=", len(xs))

print("squares:")
sq = []
for i in range(5):
    sq.append(i * i)
for s in sq:
    print(s)
