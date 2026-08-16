# probes: iterating a dict of lists yields intact lists
# expect:
# x [1, 2]
# y [3]
groups = {"x": [1, 2], "y": [3]}
for key in groups:
    print(key, groups[key])
