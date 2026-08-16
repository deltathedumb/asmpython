# probes: mutating an instance through a list element
# expect:
# 7
class Counter:
    def __init__(self):
        self.n = 0


c = Counter()
holder = [c]
holder[0].n = 7
print(c.n)
