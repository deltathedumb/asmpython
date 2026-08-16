# probes: dict(pairs) reads both halves of each pair (mixed elements)
# expect:
# {1: 'two', 'three': 4.5}
# 2
pairs = [(1, "two"), ("three", 4.5)]
built = dict(pairs)
print(built)
print(len(built))
