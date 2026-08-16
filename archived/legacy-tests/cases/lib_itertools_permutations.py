# expect:
# [(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]
from itertools import permutations
print(list(permutations([1, 2, 3], 2)))
# asmpython (beta/3.14.0) MISMATCH: prints '[9409648, 9409728, 9409456, 9409904, 9409984, 9409808]\n' (wrong).
