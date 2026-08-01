# probes: appending to a list held as a dict value
# expect:
# [1, 2]
# 2
groups = {"x": [1]}
groups["x"].append(2)
print(groups["x"])
print(len(groups["x"]))
