# probes: __hash__ plus __eq__ makes instances dict keys
# expect:
# 1
# second
class Key:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __hash__(self):
        return hash(self.n)


table = {}
table[Key(1)] = "first"
table[Key(1)] = "second"
print(len(table))
print(table[Key(1)])
