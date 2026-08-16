# probes: a function mutates the caller's instance
# expect:
# 2
class Counter:
    def __init__(self):
        self.n = 0


def bump(c):
    c.n = c.n + 1


c = Counter()
bump(c)
bump(c)
print(c.n)
