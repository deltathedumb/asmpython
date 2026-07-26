# expect:
# [1, 2, 3]
class N:
    def __init__(self, v):
        self.v = v
    def __lt__(self, o):
        return self.v < o.v
xs = [N(3), N(1), N(2)]
print([n.v for n in sorted(xs)])
# asmpython (beta/3.14.0) MISMATCH: prints '[3, 1, 2]\n' (wrong).
