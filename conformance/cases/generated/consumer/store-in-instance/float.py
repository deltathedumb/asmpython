# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3.5
# [3.5, 1.5, 2.5]
xs = [3.5, 1.5, 2.5]
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
