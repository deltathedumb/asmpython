# probes: __add__ serves the + operator
# expect:
# 5
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __add__(self, other):
        return Money(self.amount + other.amount)


print((Money(2) + Money(3)).amount)
