# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 2]
# [[1, 2], [3], [4, 5, 6]]
xs = [[1, 2], [3], [4, 5, 6]]
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
