# probes: a frozenset can be a dict key
# expect:
# pair
table = {frozenset([1, 2]): "pair"}
print(table[frozenset([2, 1])])
