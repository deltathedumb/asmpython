# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# [1, 'two', 3.5, True, None]
xs = [1, 'two', 3.5, True, None]
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
