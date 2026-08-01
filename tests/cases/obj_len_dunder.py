# probes: len() dispatches to __len__
# expect:
# 4
class Bag:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


print(len(Bag(4)))
