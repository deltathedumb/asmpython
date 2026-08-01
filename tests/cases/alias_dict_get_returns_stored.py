# probes: dict.get returns the stored object, not a copy
# expect:
# [1, 2]
groups = {"k": [1]}
got = groups.get("k")
got.append(2)
print(groups["k"])
