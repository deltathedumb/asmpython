# expect:
# 5
# 12
# 1
# 4
# 9
class Counter:
    def __init__(self, start=0):
        self.value = start

    def bump(self, by=1):
        self.value = self.value + by
        return self.value

c = Counter(4)
print(c.bump())

d = Counter()
print(d.bump(12))

# Method-level default invocation inside another method.
class Squarer:
    def __init__(self):
        self.last = 0

    def square(self, n=1):
        self.last = n * n
        return self.last

s = Squarer()
print(s.square())
print(s.square(2))
print(s.square(3))
