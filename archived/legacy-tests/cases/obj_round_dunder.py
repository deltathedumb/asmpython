# probes: __round__ serves round()
# expect:
# rounded
class Measure:
    def __init__(self, n):
        self.n = n

    def __round__(self, digits=None):
        return "rounded"


print(round(Measure(1.23)))
