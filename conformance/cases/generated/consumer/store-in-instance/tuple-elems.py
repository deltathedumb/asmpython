# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'a')
# [(1, 'a'), (2, 'b')]
xs = [(1, 'a'), (2, 'b')]
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
