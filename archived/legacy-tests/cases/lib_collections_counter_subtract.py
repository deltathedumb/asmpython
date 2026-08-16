# expect:
# 3 -1
from collections import Counter
a = Counter(a=4, b=2)
a.subtract(Counter(a=1, b=3))
print(a['a'], a['b'])
# asmpython (beta/3.14.0) rejects at compile: [E021] Counter() got an unexpected keyword argument 'a'
