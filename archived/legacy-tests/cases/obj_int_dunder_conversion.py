# probes: __int__ and __float__ serve the conversions
# expect:
# 3
# 3.0
class Measure:
    def __init__(self, n):
        self.n = n

    def __int__(self):
        return int(self.n)

    def __float__(self):
        return float(self.n)


print(int(Measure(3.7)))
print(float(Measure(3)))
