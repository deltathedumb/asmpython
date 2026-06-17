# expect:
# 6
# 6
# 6
# 4
# 2
# 3

import itertools

prod = itertools.product([1, 2, 3], [4, 5])
print(len(prod))

combos = itertools.combinations([1, 2, 3, 4], 2)
print(len(combos))

perms = itertools.permutations([1, 2, 3], 2)
print(len(perms))

cycle2 = itertools.cycle([1, 2], 2)
print(len(cycle2))

compressed = itertools.compress([1, 2, 3, 4, 5], [1, 0, 1, 0, 0])
print(len(compressed))

cwr = itertools.combinations_with_replacement([1, 2], 2)
print(len(cwr))
