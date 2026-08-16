# expect:
# [1, 2, 1, 2, 1]
from itertools import cycle, islice
print(list(islice(cycle([1, 2]), 5)))
# asmpython (beta/3.14.0) MISMATCH: prints '[1, 2, 1, 2]\n' (wrong).
