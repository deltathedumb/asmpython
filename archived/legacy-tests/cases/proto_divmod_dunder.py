# probes: __divmod__ serves divmod()
# expect:
# (3, 1)
class Num:
    def __init__(self, n):
        self.n = n

    def __divmod__(self, other):
        return (self.n // other, self.n % other)


print(divmod(Num(7), 2))
