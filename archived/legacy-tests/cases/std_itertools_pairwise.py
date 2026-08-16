# probes: itertools.pairwise exists (3.10+)
# expect:
# [(1, 2), (2, 3), (3, 4)]
import itertools

print(list(itertools.pairwise([1, 2, 3, 4])))
