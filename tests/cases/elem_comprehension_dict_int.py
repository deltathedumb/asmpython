# probes: a dict comprehension reads each element (int elements)
# expect:
# {0: 10, 1: 20, 2: 30, 3: 40}
xs = [10, 20, 30, 40]
print({i: v for i, v in enumerate(xs)})
