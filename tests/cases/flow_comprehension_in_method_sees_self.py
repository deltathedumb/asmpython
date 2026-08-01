# probes: a comprehension in a method reaches self
# expect:
# [3, 6]
class Scaler:
    def __init__(self, factor):
        self.factor = factor

    def scale(self, values):
        return [v * self.factor for v in values]


print(Scaler(3).scale([1, 2]))
