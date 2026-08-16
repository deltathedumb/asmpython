# probes: takewhile/dropwhile split on a predicate
# expect:
# [1, 2]
# [5, 1]
import itertools

xs = [1, 2, 5, 1]
print(list(itertools.takewhile(lambda v: v < 3, xs)))
print(list(itertools.dropwhile(lambda v: v < 3, xs)))
