# expect:
# {'x': 1, 'y': 2}
from collections import defaultdict
d = defaultdict(lambda: defaultdict(int))
d['a']['x'] += 1
d['a']['y'] += 2
print(dict(d['a']))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
