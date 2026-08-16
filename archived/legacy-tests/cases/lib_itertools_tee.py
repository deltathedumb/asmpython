# expect:
# [1, 2, 3] [1, 2, 3]
from itertools import tee
a, b = tee([1, 2, 3])
print(list(a), list(b))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
