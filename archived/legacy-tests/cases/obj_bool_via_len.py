# probes: truthiness falls back to __len__
# expect:
# False
# True
class Bag:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


print(bool(Bag(0)))
print(bool(Bag(2)))
