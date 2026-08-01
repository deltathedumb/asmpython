# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# c
# ['c', 'a', 'b']
xs = ['c', 'a', 'b']
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
