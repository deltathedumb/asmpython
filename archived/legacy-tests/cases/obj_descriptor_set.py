# probes: __set__ intercepts attribute assignment
# expect:
# wrapped:5
class Recorder:
    def __init__(self):
        self.stored = None

    def __get__(self, obj, owner):
        return self.stored

    def __set__(self, obj, value):
        self.stored = "wrapped:" + str(value)


class Holder:
    field = Recorder()


h = Holder()
h.field = 5
print(h.field)
