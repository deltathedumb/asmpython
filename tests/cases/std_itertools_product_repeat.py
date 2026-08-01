# probes: itertools.product accepts repeat=
# expect:
# [(0, 0), (0, 1), (1, 0), (1, 1)]
import itertools

print(list(itertools.product([0, 1], repeat=2)))
