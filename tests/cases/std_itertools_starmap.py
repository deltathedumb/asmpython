# probes: starmap spreads each tuple over the callable
# expect:
# [2, 5]
import itertools

print(list(itertools.starmap(max, [(1, 2), (5, 3)])))
