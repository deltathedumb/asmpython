# expect:
# [('a', 2), ('b', 2), ('c', 1)]
from collections import defaultdict
d = defaultdict(int)
for c in 'aabbc':
    d[c] += 1
print(sorted(d.items()))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
