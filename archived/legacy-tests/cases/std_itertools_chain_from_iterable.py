# probes: chain.from_iterable flattens one level
# expect:
# [1, 2, 3]
import itertools

print(list(itertools.chain.from_iterable([[1, 2], [3]])))
