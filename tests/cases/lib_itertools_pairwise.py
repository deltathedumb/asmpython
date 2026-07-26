# expect:
# [(1, 2), (2, 3), (3, 4)]
from itertools import pairwise
print(list(pairwise([1, 2, 3, 4])))
# asmpython (beta/3.14.0) MISMATCH: prints '[8885216, 8885280, 8885184]\n' (wrong).
