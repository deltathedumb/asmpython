# expect:
# [('a', 3), ('b', 1)]
from collections import Counter
c = Counter('aa')
c.update('ab')
print(sorted(c.items()))
# asmpython (beta/3.14.0) rejects at compile: [E113] Counter has no method 'items'
