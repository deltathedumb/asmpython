# probes: permutations accepts the length argument
# expect:
# [(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]
import itertools

print(list(itertools.permutations([1, 2, 3], 2)))
