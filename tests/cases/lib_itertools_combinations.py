# expect:
# [(1, 2), (1, 3), (2, 3)]
from itertools import combinations
print(list(combinations([1, 2, 3], 2)))
# asmpython (beta/3.14.0) MISMATCH: prints '[10196000, 10196080, 10195888]\n' (wrong).
