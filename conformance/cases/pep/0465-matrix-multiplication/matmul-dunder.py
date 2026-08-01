# tier: spec
# ref: peps.python.org/pep-0465/
# expect:
# M(12)
class M:
    def __init__(self, v):
        self.v = v
    def __matmul__(self, other):
        return M(self.v * other.v)
    def __repr__(self):
        return 'M(' + str(self.v) + ')'

print(M(3) @ M(4))
