# expect:
# [('a', 3), ('b', 2), ('c', 1)]
from collections import Counter
a = Counter('aab')
b = Counter('abc')
print(sorted((a + b).items()))
# asmpython (beta/3.14.0) rejects at compile: [E113] Counter has no method 'items'
