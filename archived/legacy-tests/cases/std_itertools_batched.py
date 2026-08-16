# probes: itertools.batched exists (3.12+)
# expect:
# [[1, 2], [3, 4], [5]]
import itertools

print([list(b) for b in itertools.batched([1, 2, 3, 4, 5], 2)])
