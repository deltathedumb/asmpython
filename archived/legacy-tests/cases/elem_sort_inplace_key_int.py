# probes: list.sort(key=) reads each element (int elements)
# expect:
# [10, 20, 30, 40]
xs = list([10, 20, 30, 40])
xs.sort(key=str)
print(xs)
