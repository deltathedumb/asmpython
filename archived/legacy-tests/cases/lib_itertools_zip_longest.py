# expect:
# [(1, 'a'), (2, '?'), (3, '?')]
from itertools import zip_longest
print(list(zip_longest([1, 2, 3], ['a'], fillvalue='?')))
# asmpython (beta/3.14.0) MISMATCH: prints '[10064944, 10065008, 10064912]\n' (wrong).
