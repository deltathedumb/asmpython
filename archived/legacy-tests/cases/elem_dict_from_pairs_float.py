# probes: dict(pairs) reads both halves of each pair (float elements)
# expect:
# {1.5: 2.5, 3.5: 4.5}
# 2
pairs = [(1.5, 2.5), (3.5, 4.5)]
built = dict(pairs)
print(built)
print(len(built))
