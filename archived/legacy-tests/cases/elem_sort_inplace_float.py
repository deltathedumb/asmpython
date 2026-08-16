# probes: list.sort orders in place (float elements)
# expect:
# [1.5, 2.5, 3.5, 4.5]
xs = list([1.5, 2.5, 3.5, 4.5])
xs.sort()
print(xs)
