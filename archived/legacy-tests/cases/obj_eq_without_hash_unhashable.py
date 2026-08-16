# probes: defining __eq__ alone clears __hash__
# expect:
# unhashable
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n


try:
    hash(Version(1))
    print("hashable")
except TypeError:
    print("unhashable")
