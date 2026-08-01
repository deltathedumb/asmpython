# probes: __setattr__ sees every assignment
# expect:
# ['a', 'b']
# 1
class Logged:
    def __init__(self):
        object.__setattr__(self, "log", [])

    def __setattr__(self, name, value):
        self.log.append(name)
        object.__setattr__(self, name, value)


o = Logged()
o.a = 1
o.b = 2
print(o.log)
print(o.a)
