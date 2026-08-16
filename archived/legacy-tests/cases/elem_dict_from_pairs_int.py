# probes: dict(pairs) reads both halves of each pair (int elements)
# expect:
# {10: 20, 30: 40}
# 2
pairs = [(10, 20), (30, 40)]
built = dict(pairs)
print(built)
print(len(built))
