# probes: __eq__ decides ==
# expect:
# True
# False
# True
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n


print(Version(1) == Version(1))
print(Version(1) == Version(2))
print(Version(1) != Version(2))
