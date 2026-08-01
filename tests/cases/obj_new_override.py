# probes: __new__ can return a prepared instance
# expect:
# True
# x
class Tagged:
    def __new__(cls, *args, **kwargs):
        obj = super().__new__(cls)
        obj.created = True
        return obj

    def __init__(self, name):
        self.name = name


t = Tagged("x")
print(t.created)
print(t.name)
