# probes: a set drops equal-and-equally-hashed members
# expect:
# 2
class Key:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __hash__(self):
        return hash(self.n)


print(len({Key(1), Key(1), Key(2)}))
