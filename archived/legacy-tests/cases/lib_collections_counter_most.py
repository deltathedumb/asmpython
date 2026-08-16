# expect:
# [('a', 3), ('b', 2)]
from collections import Counter
c = Counter(['a', 'b', 'a', 'c', 'a', 'b'])
print(c.most_common(2))
# asmpython (beta/3.14.0) MISMATCH: prints '[]\n' (wrong).
