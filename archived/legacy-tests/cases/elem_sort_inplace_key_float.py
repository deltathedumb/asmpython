# probes: list.sort(key=) reads each element (float elements)
# expect:
# [1.5, 2.5, 3.5, 4.5]
xs = list([1.5, 2.5, 3.5, 4.5])
xs.sort(key=str)
print(xs)
