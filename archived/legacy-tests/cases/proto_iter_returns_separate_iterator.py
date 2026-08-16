# probes: __iter__ may hand back a fresh iterator
# expect:
# [1, 2]
# [1, 2]
class Bag:
    def __init__(self, items):
        self.items = items

    def __iter__(self):
        return iter(self.items)


bag = Bag([1, 2])
print(list(bag))
print(list(bag))
