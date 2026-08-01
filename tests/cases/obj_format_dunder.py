# probes: format() dispatches to __format__
# expect:
# $3.00
# $5
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __format__(self, spec):
        return "$" + format(self.amount, spec)


print(format(Money(3), ".2f"))
print(format(Money(5), "d"))
