# probes: sorted() uses __lt__ on instances
# expect:
# [v1, v2, v3]
class Version:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        return self.n < other.n

    def __repr__(self):
        return "v" + str(self.n)


print(sorted([Version(3), Version(1), Version(2)]))
