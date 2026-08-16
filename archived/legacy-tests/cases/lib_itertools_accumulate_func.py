# expect:
# [1, 2, 6, 24]
from itertools import accumulate
import operator
print(list(accumulate([1, 2, 3, 4], operator.mul)))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
