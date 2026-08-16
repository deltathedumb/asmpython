# expect:
# 4.0
# 2.0
# 2.5
# 3.5
# 4.0


def add(a, b):
    return a + b


def avg(x, y, z):
    return (x + y + z) / 3.0


def snd(a, b):
    return b


def ratio(a, b):
    return a / b


class Calc:
    def addm(self, a, b):
        return a + b


print(add(1.5, 2.5))
print(avg(1.0, 2.0, 3.0))
print(snd(1.5, 2.5))
print(ratio(7.0, 2.0))
c = Calc()
print(c.addm(1.5, 2.5))
