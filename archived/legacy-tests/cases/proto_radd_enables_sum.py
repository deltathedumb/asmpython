# probes: sum() reaches __radd__ starting from 0
# expect:
# 3
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __add__(self, other):
        if isinstance(other, Money):
            return Money(self.amount + other.amount)
        return Money(self.amount + other)

    def __radd__(self, other):
        return Money(self.amount + other)


print(sum([Money(1), Money(2)]).amount)
