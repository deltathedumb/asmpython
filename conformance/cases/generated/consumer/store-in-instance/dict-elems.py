# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'a': 1}
# [{'a': 1}, {'b': 2}]
xs = [{'a': 1}, {'b': 2}]
class _Holder:
    def __init__(self, seq):
        self.seq = seq

h = _Holder(xs)
print(h.seq[0])
print(h.seq)
