# probes: islice accepts a step argument
# expect:
# [0, 3, 6, 9]
import itertools

print(list(itertools.islice(range(10), 0, 10, 3)))
