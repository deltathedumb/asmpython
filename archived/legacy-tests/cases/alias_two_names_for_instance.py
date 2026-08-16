# probes: two names for one instance see one state
# expect:
# 5
class Counter:
    def __init__(self):
        self.n = 0


a = Counter()
b = a
b.n = 5
print(a.n)
