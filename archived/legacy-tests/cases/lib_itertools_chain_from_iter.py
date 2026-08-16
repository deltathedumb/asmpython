# expect:
# [1, 2, 3, 4, 5]
from itertools import chain
print(list(chain.from_iterable([[1, 2], [3], [4, 5]])))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
