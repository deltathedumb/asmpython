# tier: spec
# ref: reference/datamodel.html#object.__radd__
# expect:
# radd:1+5
class Money:
    def __init__(self, n):
        self.n = n
    def __radd__(self, other):
        return "radd:" + str(other) + "+" + str(self.n)

print(1 + Money(5))
