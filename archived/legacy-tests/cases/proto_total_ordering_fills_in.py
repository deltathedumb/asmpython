# probes: functools.total_ordering derives the rest
# expect:
# True
# True
# True
import functools


@functools.total_ordering
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __lt__(self, other):
        return self.n < other.n


print(Version(1) < Version(2))
print(Version(3) > Version(2))
print(Version(2) >= Version(2))
