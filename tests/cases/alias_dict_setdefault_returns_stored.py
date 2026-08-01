# probes: setdefault returns the stored object
# expect:
# [1]
# True
groups = {}
first = groups.setdefault("k", [])
first.append(1)
print(groups["k"])
print(groups.setdefault("k", []) is first)
