# probes: min/max use __lt__ on instances
# expect:
# 1
# 3
class Version:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        return self.n < other.n


print(min([Version(3), Version(1)]).n)
print(max([Version(3), Version(1)]).n)
