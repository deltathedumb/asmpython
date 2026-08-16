# probes: a property setter intercepts assignment
# expect:
# 21
# 21
class Temp:
    def __init__(self):
        self._c = 0

    @property
    def celsius(self):
        return self._c

    @celsius.setter
    def celsius(self, value):
        self._c = value * 1


t = Temp()
t.celsius = 21
print(t.celsius)
print(t._c)
