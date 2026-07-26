# expect:
# 6
from collections import Counter
c = Counter([1, 1, 2, 3, 3, 3])
print(sum(c.values()))
# asmpython (beta/3.14.0) rejects at compile: [E113] Counter has no method 'values'
