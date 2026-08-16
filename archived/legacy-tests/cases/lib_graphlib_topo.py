# expect:
# ['c', 'b', 'a']
from graphlib import TopologicalSorter
ts = TopologicalSorter({'a': ['b'], 'b': ['c']})
print(list(ts.static_order()))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
