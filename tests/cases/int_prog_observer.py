# expect:
# [5, 10]
class Subject:
    def __init__(self):
        self.observers = []
        self.value = 0
    def subscribe(self, fn):
        self.observers.append(fn)
    def set_value(self, v):
        self.value = v
        for obs in self.observers:
            obs(v)
log = []
s = Subject()
s.subscribe(lambda v: log.append(v))
s.set_value(5)
s.set_value(10)
print(log)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
