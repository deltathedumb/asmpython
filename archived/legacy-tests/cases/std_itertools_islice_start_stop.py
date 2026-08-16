# probes: islice accepts start and stop
# expect:
# [2, 3, 4]
import itertools

print(list(itertools.islice(range(10), 2, 5)))
