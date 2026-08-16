# expect:
# {'a': [1, 2]}
from collections import defaultdict
d = defaultdict(list)
d['a'].append(1)
d['a'].append(2)
print(dict(d))
# asmpython (beta/3.14.0) rejects at compile: [E022] dict() requires a dict or list-of-pairs argument
