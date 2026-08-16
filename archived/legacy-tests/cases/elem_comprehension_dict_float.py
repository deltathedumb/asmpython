# probes: a dict comprehension reads each element (float elements)
# expect:
# {0: 1.5, 1: 2.5, 2: 3.5, 3: 4.5}
xs = [1.5, 2.5, 3.5, 4.5]
print({i: v for i, v in enumerate(xs)})
