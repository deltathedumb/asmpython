# probes: a list stored as a dict value keeps its elements
# expect:
# [1, 2]
# 2
# a
groups = {"x": [1, 2], "y": ["a"]}
print(groups["x"])
print(groups["x"][1])
print(groups["y"][0])
