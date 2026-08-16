# probes: __iadd__ makes += mutate rather than rebind
# expect:
# ['x', 'y']
# True
class Accum:
    def __init__(self):
        self.items = []

    def __iadd__(self, value):
        self.items.append(value)
        return self


a = Accum()
same = a
a += "x"
a += "y"
print(a.items)
print(same is a)
