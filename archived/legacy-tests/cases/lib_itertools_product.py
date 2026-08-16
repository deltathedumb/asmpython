# expect:
# [(1, 'a'), (1, 'b'), (2, 'a'), (2, 'b')]
from itertools import product
print(list(product([1, 2], ['a', 'b'])))
# asmpython (beta/3.14.0) MISMATCH: prints '[9606160, 9606224, 9606128, 9606368]\n' (wrong).
