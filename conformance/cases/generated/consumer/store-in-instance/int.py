# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3
# [3, 1, 2]
xs = [3, 1, 2]
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
