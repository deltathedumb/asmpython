# expect:
# [(1, 2), (2, 1), (3, 3)]
from itertools import groupby
data = [1, 1, 2, 3, 3, 3]
print([(k, len(list(g))) for k, g in groupby(data)])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
