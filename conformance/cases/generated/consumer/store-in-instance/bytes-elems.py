# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# [b'ab', b'cd']
xs = [b'ab', b'cd']
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
