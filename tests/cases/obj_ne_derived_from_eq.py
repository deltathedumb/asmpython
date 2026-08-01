# probes: __ne__ defaults to the negation of __eq__
# expect:
# True
# False
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n


print(Version(1) != Version(2))
print(Version(1) != Version(1))
