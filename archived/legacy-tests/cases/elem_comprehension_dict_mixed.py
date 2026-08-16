# probes: a dict comprehension reads each element (mixed elements)
# expect:
# {0: 1, 1: 'two', 2: 3.5, 3: True, 4: None}
xs = [1, "two", 3.5, True, None]
print({i: v for i, v in enumerate(xs)})
