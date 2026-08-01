# probes: equal objects collapse to one dict key
# expect:
# 2
# b
class Key:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __hash__(self):
        return hash(self.n)


table = {Key(1): "a", Key(1): "b", Key(2): "c"}
print(len(table))
print(table[Key(1)])
