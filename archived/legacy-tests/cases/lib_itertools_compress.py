# expect:
# ['a', 'c']
from itertools import compress
print(list(compress('abcd', [1, 0, 1, 0])))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
