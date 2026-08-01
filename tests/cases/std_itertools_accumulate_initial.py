# probes: accumulate accepts initial=
# expect:
# [10, 11, 13, 16]
import itertools

print(list(itertools.accumulate([1, 2, 3], initial=10)))
