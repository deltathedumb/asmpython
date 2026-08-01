# probes: count accepts a step argument
# expect:
# [5, 8, 11, 14]
import itertools

print(list(itertools.islice(itertools.count(5, 3), 4)))
