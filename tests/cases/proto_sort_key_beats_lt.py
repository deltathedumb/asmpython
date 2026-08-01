# probes: sorted(key=) does not consult __lt__
# expect:
# [1, 3]
class Item:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        raise AssertionError("__lt__ must not be used when key= is given")


print([i.n for i in sorted([Item(3), Item(1)], key=lambda i: i.n)])
