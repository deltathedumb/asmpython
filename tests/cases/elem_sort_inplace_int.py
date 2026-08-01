# probes: list.sort orders in place (int elements)
# expect:
# [10, 20, 30, 40]
xs = list([10, 20, 30, 40])
xs.sort()
print(xs)
