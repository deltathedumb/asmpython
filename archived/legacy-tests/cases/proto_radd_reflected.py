# probes: __radd__ handles a left operand that declines
# expect:
# 3
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __radd__(self, other):
        return Money(other + self.amount)


print((1 + Money(2)).amount)
