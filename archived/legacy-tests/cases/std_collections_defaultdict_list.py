# probes: defaultdict(list) auto-creates lists
# expect:
# [1, 2]
import collections

groups = collections.defaultdict(list)
groups["k"].append(1)
groups["k"].append(2)
print(groups["k"])
